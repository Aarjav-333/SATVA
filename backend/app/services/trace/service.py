"""SATVA Trace service: lot registration, custody appends, splits and verification.

Business rules enforced here:

* Custody events are append-only. The service never updates or deletes one, and
  a database trigger blocks it at the storage layer as well.
* A lot's chain is verified from genesis on every read that claims integrity.
  The denormalised ``head_hash`` on the lot is a cache, never the authority.
* A child lot inherits its parent's verified chain: the split event on the
  parent and the genesis event on the child both record the linkage, so the
  consumer journey for a shelf unit reaches back to the farm.
* Trace is optional (rule 9) and a missing QR never blocks Scan (rule 10). This
  service is therefore never called from the Scan path.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import CustodyEventType, RipeningMethod
from app.core.errors import ChainIntegrityError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.trace import CustodyEvent, Farm, ProduceLot
from app.services.trace.hashchain import (
    GENESIS_HASH,
    HASH_ALGORITHM,
    ChainLink,
    ChainVerification,
    build_event_payload,
    compute_event_hash,
    verify_chain,
)

log = get_logger("satva.trace")


def new_lot_code(crop: str, when: date | None = None) -> str:
    """Human-legible lot code, e.g. ``MNG-20260904-7F3A``.

    Readable because it gets printed on crates and read aloud over the phone;
    the random tail prevents guessing a neighbouring lot's code.
    """
    stamp = (when or datetime.now(UTC).date()).strftime("%Y%m%d")
    return f"{crop[:3].upper()}-{stamp}-{secrets.token_hex(2).upper()}"


def new_qr_token() -> str:
    """Unguessable QR token.

    Scanning a QR reveals a lot's journey, so the token must not be enumerable:
    a sequential identifier would let anyone walk the entire supply chain.
    """
    return secrets.token_urlsafe(24)


@dataclass
class LotJourneyStep:
    """One step of the consumer-facing journey."""

    sequence_no: int
    event_type: str
    title: str
    occurred_at: datetime
    actor: str | None
    location: str | None
    details: dict[str, Any]


class TraceService:
    def __init__(self, db: Session):
        self.db = db

    # --- Reads -------------------------------------------------------------
    def get_lot(self, lot_id: uuid.UUID) -> ProduceLot:
        lot = self.db.get(ProduceLot, lot_id)
        if lot is None:
            raise NotFoundError("No such produce lot.")
        return lot

    def get_lot_by_qr(self, qr_token: str) -> ProduceLot:
        lot = self.db.scalar(select(ProduceLot).where(ProduceLot.qr_token == qr_token))
        if lot is None:
            raise NotFoundError(
                "This QR code is not a SATVA Trace code, or the lot has been removed."
            )
        return lot

    def _events(self, lot_id: uuid.UUID) -> list[CustodyEvent]:
        return list(
            self.db.scalars(
                select(CustodyEvent)
                .where(CustodyEvent.lot_id == lot_id)
                .order_by(CustodyEvent.sequence_no)
            )
        )

    # --- Writes ------------------------------------------------------------
    def register_lot(
        self,
        *,
        crop: str,
        quantity_kg: float,
        farm_id: uuid.UUID | None = None,
        cultivar: str | None = None,
        plot_identifier: str | None = None,
        harvest_date: date | None = None,
        declared_ripening_method: str = RipeningMethod.UNDECLARED,
        grade: str | None = None,
        actor_ref: str | None = None,
        actor_display_name: str | None = None,
        location_name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        occurred_at: datetime | None = None,
        is_synthetic: bool = False,
    ) -> ProduceLot:
        """Create a lot and write its genesis custody event."""
        if quantity_kg <= 0:
            raise ConflictError("A lot must have a positive quantity.")

        farm: Farm | None = None
        if farm_id is not None:
            farm = self.db.get(Farm, farm_id)
            if farm is None:
                raise NotFoundError("No such farm or FPO.")

        lot = ProduceLot(
            lot_code=new_lot_code(crop, harvest_date),
            qr_token=new_qr_token(),
            farm_id=farm_id,
            crop=crop,
            cultivar=cultivar,
            plot_identifier=plot_identifier,
            harvest_date=harvest_date,
            declared_ripening_method=declared_ripening_method,
            quantity_kg=Decimal(str(quantity_kg)),
            grade=grade,
            status="active",
            head_hash=GENESIS_HASH,
            event_count=0,
            is_synthetic=is_synthetic,
        )
        self.db.add(lot)
        self.db.flush()

        self.append_event(
            lot=lot,
            event_type=CustodyEventType.LOT_REGISTERED,
            actor_kind="farmer",
            actor_ref=actor_ref or (farm.farm_code if farm else None),
            actor_display_name=actor_display_name or (farm.name if farm else None),
            location_name=location_name or (farm.village if farm else None),
            latitude=latitude if latitude is not None else (farm.latitude if farm else None),
            longitude=longitude if longitude is not None else (farm.longitude if farm else None),
            quantity_kg=quantity_kg,
            occurred_at=occurred_at,
            extra={
                "crop": crop,
                "cultivar": cultivar,
                "plot_identifier": plot_identifier,
                "harvest_date": harvest_date,
                "declared_ripening_method": declared_ripening_method,
                "lot_code": lot.lot_code,
            },
            is_synthetic=is_synthetic,
        )
        log.info("lot_registered", lot_id=str(lot.id), lot_code=lot.lot_code, crop=crop)
        return lot

    def append_event(
        self,
        *,
        lot: ProduceLot,
        event_type: str,
        actor_kind: str,
        actor_ref: str | None = None,
        actor_display_name: str | None = None,
        location_name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        quantity_kg: float | None = None,
        temperature_c: float | None = None,
        humidity_pct: float | None = None,
        occurred_at: datetime | None = None,
        extra: dict[str, Any] | None = None,
        is_synthetic: bool = False,
    ) -> CustodyEvent:
        """Append one custody record, extending the hash chain.

        The previous hash is read from the actual last row rather than from the
        lot's cached ``head_hash``, so a stale or tampered cache cannot be used
        to fork the chain.
        """
        if lot.status == "withdrawn":
            raise ConflictError("This lot has been withdrawn; no further custody events.")

        last = self.db.scalar(
            select(CustodyEvent)
            .where(CustodyEvent.lot_id == lot.id)
            .order_by(CustodyEvent.sequence_no.desc())
            .limit(1)
        )
        sequence_no = 0 if last is None else last.sequence_no + 1
        previous_hash = GENESIS_HASH if last is None else last.event_hash

        when = occurred_at or datetime.now(UTC)
        payload = build_event_payload(
            lot_id=lot.id,
            sequence_no=sequence_no,
            event_type=str(event_type),
            occurred_at=when,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            quantity_kg=quantity_kg,
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            extra=extra or {},
        )
        event_hash = compute_event_hash(payload, previous_hash)

        event = CustodyEvent(
            lot_id=lot.id,
            sequence_no=sequence_no,
            event_type=str(event_type),
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            actor_display_name=actor_display_name,
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            quantity_kg=Decimal(str(quantity_kg)) if quantity_kg is not None else None,
            temperature_c=temperature_c,
            humidity_pct=humidity_pct,
            payload=payload,
            occurred_at=when,
            previous_hash=previous_hash,
            event_hash=event_hash,
            hash_algorithm=HASH_ALGORITHM,
            is_synthetic=is_synthetic,
        )
        self.db.add(event)

        lot.head_hash = event_hash
        lot.event_count = sequence_no + 1
        self.db.flush()
        return event

    def split_lot(
        self,
        *,
        parent: ProduceLot,
        quantity_kg: float,
        actor_ref: str | None = None,
        actor_display_name: str | None = None,
        location_name: str | None = None,
        grade: str | None = None,
        occurred_at: datetime | None = None,
        is_synthetic: bool = False,
    ) -> ProduceLot:
        """Split a child lot off a parent, inheriting the parent's chain.

        The child gets its own QR and its own chain, but its genesis event
        records the parent's lot id and current head hash. That is what lets a
        consumer scanning a shelf unit walk back to the farm: the link is
        cryptographic, not merely a foreign key.
        """
        verification = self.verify_lot(parent.id)
        if not verification.valid:
            raise ChainIntegrityError(
                "The parent lot's custody chain does not verify, so it cannot be split.",
                details=verification.to_dict(),
            )

        remaining = float(parent.quantity_kg) - self._already_split(parent.id)
        if quantity_kg <= 0:
            raise ConflictError("A split must have a positive quantity.")
        if quantity_kg > remaining + 1e-9:
            raise ConflictError(
                f"Cannot split {quantity_kg} kg from this lot: only {remaining:.2f} kg remains "
                "unallocated."
            )

        child = ProduceLot(
            lot_code=new_lot_code(parent.crop, parent.harvest_date),
            qr_token=new_qr_token(),
            farm_id=parent.farm_id,
            parent_lot_id=parent.id,
            crop=parent.crop,
            cultivar=parent.cultivar,
            plot_identifier=parent.plot_identifier,
            harvest_date=parent.harvest_date,
            declared_ripening_method=parent.declared_ripening_method,
            quantity_kg=Decimal(str(quantity_kg)),
            grade=grade or parent.grade,
            status="active",
            head_hash=GENESIS_HASH,
            event_count=0,
            is_synthetic=is_synthetic,
        )
        self.db.add(child)
        self.db.flush()

        inheritance = {
            "parent_lot_id": str(parent.id),
            "parent_lot_code": parent.lot_code,
            "parent_head_hash": verification.head_hash,
            "parent_chain_length": verification.length,
        }

        self.append_event(
            lot=parent,
            event_type=CustodyEventType.LOT_SPLIT,
            actor_kind="retailer",
            actor_ref=actor_ref,
            actor_display_name=actor_display_name,
            location_name=location_name,
            quantity_kg=quantity_kg,
            occurred_at=occurred_at,
            extra={"child_lot_id": str(child.id), "child_lot_code": child.lot_code},
            is_synthetic=is_synthetic,
        )
        self.append_event(
            lot=child,
            event_type=CustodyEventType.LOT_REGISTERED,
            actor_kind="retailer",
            actor_ref=actor_ref,
            actor_display_name=actor_display_name,
            location_name=location_name,
            quantity_kg=quantity_kg,
            occurred_at=occurred_at,
            extra={"inherited_from": inheritance, "lot_code": child.lot_code},
            is_synthetic=is_synthetic,
        )
        log.info("lot_split", parent=str(parent.id), child=str(child.id), kg=quantity_kg)
        return child

    def _already_split(self, parent_id: uuid.UUID) -> float:
        total = self.db.scalar(
            select(func.coalesce(func.sum(ProduceLot.quantity_kg), 0)).where(
                ProduceLot.parent_lot_id == parent_id
            )
        )
        return float(total or 0)

    # --- Verification ------------------------------------------------------
    def verify_lot(self, lot_id: uuid.UUID) -> ChainVerification:
        """Recompute a lot's chain from genesis."""
        events = self._events(lot_id)
        links = [
            ChainLink(
                sequence_no=e.sequence_no,
                payload=e.payload,
                previous_hash=e.previous_hash,
                event_hash=e.event_hash,
            )
            for e in events
        ]
        return verify_chain(links)

    def verify_ancestry(self, lot_id: uuid.UUID) -> dict[str, Any]:
        """Verify a lot and every ancestor up to the original farm lot.

        A child lot with a valid chain of its own is still untrustworthy if its
        parent's chain was tampered with, so a consumer-facing "verified" badge
        has to mean the whole ancestry verified.
        """
        chain_reports: list[dict[str, Any]] = []
        seen: set[uuid.UUID] = set()
        current: ProduceLot | None = self.get_lot(lot_id)

        while current is not None:
            if current.id in seen:
                # Defensive: a cycle would otherwise hang the request.
                chain_reports.append(
                    {"lot_id": str(current.id), "valid": False, "failure_kind": "ancestry_cycle"}
                )
                break
            seen.add(current.id)

            report = self.verify_lot(current.id)
            chain_reports.append(
                {"lot_id": str(current.id), "lot_code": current.lot_code, **report.to_dict()}
            )

            if current.parent_lot_id is None:
                break
            parent = self.db.get(ProduceLot, current.parent_lot_id)
            if parent is None:
                chain_reports.append(
                    {
                        "lot_id": str(current.parent_lot_id),
                        "valid": False,
                        "failure_kind": "missing_ancestor",
                        "detail": "a parent lot referenced by this chain no longer exists",
                    }
                )
                break

            # The child's genesis event records the parent head hash it split
            # from. If the parent has since diverged, the inheritance is broken
            # even though both chains verify individually.
            self._check_inheritance(current, parent, chain_reports)
            current = parent

        return {
            "valid": all(r.get("valid") for r in chain_reports),
            "depth": len(chain_reports),
            "lots": chain_reports,
        }

    def _check_inheritance(
        self, child: ProduceLot, parent: ProduceLot, reports: list[dict[str, Any]]
    ) -> None:
        genesis = self.db.scalar(
            select(CustodyEvent).where(
                CustodyEvent.lot_id == child.id, CustodyEvent.sequence_no == 0
            )
        )
        inherited = (genesis.payload.get("extra") or {}).get("inherited_from") if genesis else None
        if not inherited:
            return
        recorded_head = inherited.get("parent_head_hash")
        recorded_length = inherited.get("parent_chain_length")
        if recorded_head is None:
            return

        parent_events = self._events(parent.id)
        if recorded_length is not None and recorded_length <= len(parent_events):
            actual = (
                parent_events[recorded_length - 1].event_hash
                if recorded_length
                else GENESIS_HASH
            )
            if actual != recorded_head:
                reports.append(
                    {
                        "lot_id": str(parent.id),
                        "lot_code": parent.lot_code,
                        "valid": False,
                        "failure_kind": "inheritance_mismatch",
                        "detail": (
                            f"lot {child.lot_code} was split from {parent.lot_code} at chain head "
                            f"{recorded_head[:12]}..., but that position now hashes to "
                            f"{actual[:12]}...; the parent's history changed after the split"
                        ),
                    }
                )

    # --- Consumer journey --------------------------------------------------
    _TITLES = {
        CustodyEventType.LOT_REGISTERED: "Harvested and registered",
        CustodyEventType.AGGREGATOR_SCAN_IN: "Received at collection centre",
        CustodyEventType.AGGREGATOR_SCAN_OUT: "Dispatched from collection centre",
        CustodyEventType.TRANSPORT_DEPART: "Left for market",
        CustodyEventType.TRANSPORT_ARRIVE: "Arrived at market",
        CustodyEventType.TEMPERATURE_READING: "Temperature logged in transit",
        CustodyEventType.RETAILER_RECEIVED: "Received by retailer",
        CustodyEventType.LOT_SPLIT: "Divided into shelf units",
        CustodyEventType.LOT_WITHDRAWN: "Withdrawn from sale",
    }

    def consumer_journey(self, lot_id: uuid.UUID) -> list[LotJourneyStep]:
        """Readable, oldest-first journey spanning the lot and its ancestry."""
        steps: list[LotJourneyStep] = []
        lineage: list[ProduceLot] = []
        current: ProduceLot | None = self.get_lot(lot_id)
        guard = 0
        while current is not None and guard < 32:
            lineage.append(current)
            current = (
                self.db.get(ProduceLot, current.parent_lot_id) if current.parent_lot_id else None
            )
            guard += 1

        for lot in reversed(lineage):
            for event in self._events(lot.id):
                # A child's own genesis restates the split already shown on the
                # parent; showing both would make one physical handover look
                # like two.
                if event.sequence_no == 0 and lot.parent_lot_id is not None:
                    continue
                steps.append(
                    LotJourneyStep(
                        sequence_no=len(steps),
                        event_type=event.event_type,
                        title=self._TITLES.get(
                            event.event_type, event.event_type.replace("_", " ")
                        ),
                        occurred_at=event.occurred_at,
                        actor=event.actor_display_name,
                        location=event.location_name,
                        details={
                            "quantity_kg": float(event.quantity_kg)
                            if event.quantity_kg is not None
                            else None,
                            "temperature_c": event.temperature_c,
                            "lot_code": lot.lot_code,
                        },
                    )
                )
        return steps
