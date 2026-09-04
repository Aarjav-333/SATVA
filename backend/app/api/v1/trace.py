"""/lots, /custody and /trace -- SATVA Trace.

Trace is optional (rule 9) and a missing QR never blocks Scan (rule 10). The
consumer lookup route therefore returns a structured, actionable 404 rather than
an error page, so the app can fall back to plain Scan cleanly.
"""

from __future__ import annotations

import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    Principal,
    require_roles,
    write_audit,
)
from app.core.constants import Role
from app.core.errors import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.retail import InventoryItem, RetailOutlet
from app.models.trace import CustodyEvent, Farm, ProduceLot
from app.schemas.trace import (
    AncestryVerificationOut,
    ChainVerificationOut,
    ConsumerJourneyOut,
    CustodyEventCreate,
    CustodyEventOut,
    FarmCreate,
    FarmOut,
    JourneyStepOut,
    LotCreate,
    LotDetail,
    LotOut,
    LotSplitRequest,
)
from app.services.trace.service import TraceService

router = APIRouter(tags=["trace"])

require_supply_chain = require_roles(Role.FARMER, Role.RETAILER, Role.OFFICER)


def assert_may_write_chain(db: Session, lot: ProduceLot, principal: Principal) -> None:
    """Establish that this caller may append to this lot's chain.

    Holding a supply-chain role says what kind of party someone is, not which
    lots are theirs. That distinction matters more here than on an ordinary
    route: custody events are append-only at the database level, so a record
    written against the wrong lot can never be withdrawn, and a chain a rival
    has written into is discredited permanently.

    Three ways to qualify: an officer, whose mandate is regulatory; a farmer
    whose farm produced the lot; or a retailer who actually holds it, which is
    what makes a `retailer_received` event mean anything.

    This is narrower than it should be. Without a model of who currently holds
    a lot, a retailer establishes standing by booking the lot into their own
    inventory, so the check bounds who can write rather than proving handover.
    A custody-transfer record is the real fix; see docs/known_limitations.md.
    """
    if principal.is_(Role.OFFICER):
        return

    if principal.is_(Role.FARMER) and lot.farm is not None:
        if lot.farm.owner_user_id is not None and lot.farm.owner_user_id == principal.id:
            return

    if principal.is_(Role.RETAILER):
        holding = db.scalar(
            select(InventoryItem.id)
            .join(RetailOutlet, RetailOutlet.id == InventoryItem.outlet_id)
            .where(
                InventoryItem.lot_id == lot.id,
                RetailOutlet.owner_user_id == principal.id,
            )
            .limit(1)
        )
        if holding is not None:
            return

    raise PermissionDeniedError(
        "This lot is not yours to write to.",
        details={"lot_id": str(lot.id), "reason": "not_lot_custodian"},
    )

INTEGRITY_NOTE = (
    "Each custody record stores a SHA-256 hash of its own contents combined with the hash of "
    "the record before it, so altering any historical entry invalidates every entry after it. "
    "A daily Merkle root is published as an external anchor, which means even SATVA's "
    "operators cannot rewrite this history without the change being detectable. SATVA uses a "
    "hash chain rather than a blockchain because the requirement is tamper-evidence, not "
    "decentralised consensus."
)

TRACE_OPTIONAL_NOTE = (
    "Trace is optional. Produce without a QR code can still be screened with SATVA Scan; a "
    "missing code never blocks anything."
)


def _lot_out(lot: ProduceLot, *, include_qr_token: bool = False) -> LotOut:
    """Serialise a lot, withholding the QR token unless the caller earned it.

    The lot listing and detail routes are deliberately open -- a citizen can
    read a chain without an account, and `/trace` in the dashboard is a public
    page. The chain is the point of that openness; the QR token is not. It is
    an unguessable capability (`new_qr_token`), and returning it in a list
    would let an anonymous caller collect every token in one request and walk
    the entire supply chain -- exactly what making it unguessable was meant to
    prevent. Handing it back to the farmer who just registered the lot, or
    through the authenticated PNG route, is the whole of its legitimate use.
    """
    return LotOut(
        id=lot.id,
        lot_code=lot.lot_code,
        qr_token=lot.qr_token if include_qr_token else None,
        crop=lot.crop,
        cultivar=lot.cultivar,
        plot_identifier=lot.plot_identifier,
        harvest_date=lot.harvest_date,
        declared_ripening_method=lot.declared_ripening_method,
        quantity_kg=float(lot.quantity_kg),
        grade=lot.grade,
        status=lot.status,
        head_hash=lot.head_hash,
        event_count=lot.event_count,
        parent_lot_id=lot.parent_lot_id,
        farm=FarmOut.model_validate(lot.farm) if lot.farm else None,
        is_synthetic=lot.is_synthetic,
        created_at=lot.created_at,
    )


# --- Farms -------------------------------------------------------------------
@router.post(
    "/farms", response_model=FarmOut, status_code=status.HTTP_201_CREATED, tags=["trace"]
)
def create_farm(
    payload: FarmCreate,
    principal: Principal = Depends(require_supply_chain),
    db: Session = Depends(get_db),
) -> FarmOut:
    import secrets

    farm = Farm(
        farm_code=f"FRM-{secrets.token_hex(3).upper()}",
        name=payload.name,
        entity_type=payload.entity_type,
        owner_user_id=principal.id,
        village=payload.village,
        block=payload.block,
        district=payload.district,
        state=payload.state,
        latitude=payload.latitude,
        longitude=payload.longitude,
        certifications=payload.certifications,
        fpo_member_count=payload.fpo_member_count,
    )
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return FarmOut.model_validate(farm)


@router.get("/farms", response_model=list[FarmOut], tags=["trace"])
def list_farms(
    district: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[FarmOut]:
    statement = select(Farm).order_by(Farm.name).limit(limit)
    if district:
        statement = statement.where(Farm.district == district)
    return [FarmOut.model_validate(f) for f in db.scalars(statement)]


# --- Lots --------------------------------------------------------------------
@router.post(
    "/lots",
    response_model=LotDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Register a produce lot and open its custody chain",
)
def create_lot(
    payload: LotCreate,
    request: Request,
    principal: Principal = Depends(require_supply_chain),
    db: Session = Depends(get_db),
) -> LotDetail:
    service = TraceService(db)
    lot = service.register_lot(
        crop=payload.crop,
        quantity_kg=payload.quantity_kg,
        farm_id=payload.farm_id,
        cultivar=payload.cultivar,
        plot_identifier=payload.plot_identifier,
        harvest_date=payload.harvest_date,
        declared_ripening_method=payload.declared_ripening_method,
        grade=payload.grade,
        actor_display_name=principal.user.display_name,
        location_name=payload.location_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    write_audit(
        db,
        action="trace.lot_registered",
        principal=principal,
        object_type="lot",
        object_id=str(lot.id),
        request=request,
    )
    db.commit()
    db.refresh(lot)
    return _lot_detail(service, lot, db, include_qr_token=True)


def _lot_detail(
    service: TraceService, lot: ProduceLot, db: Session, *, include_qr_token: bool = False
) -> LotDetail:
    verification = service.verify_lot(lot.id)
    children = list(
        db.scalars(select(ProduceLot.id).where(ProduceLot.parent_lot_id == lot.id))
    )
    return LotDetail(
        **_lot_out(lot, include_qr_token=include_qr_token).model_dump(),
        custody_events=[CustodyEventOut.model_validate(e) for e in lot.custody_events],
        verification=ChainVerificationOut(**verification.to_dict()),
        child_lot_ids=children,
    )


@router.get("/lots", response_model=list[LotOut], summary="List produce lots")
def list_lots(
    crop: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[LotOut]:
    statement = select(ProduceLot).order_by(ProduceLot.created_at.desc()).limit(limit)
    if crop:
        statement = statement.where(ProduceLot.crop == crop)
    return [_lot_out(lot) for lot in db.scalars(statement)]


@router.get("/lots/{lot_id}", response_model=LotDetail, summary="Lot detail with chain state")
def get_lot(lot_id: uuid.UUID, db: Session = Depends(get_db)) -> LotDetail:
    service = TraceService(db)
    return _lot_detail(service, service.get_lot(lot_id), db)


@router.get(
    "/lots/{lot_id}/verify",
    response_model=AncestryVerificationOut,
    summary="Verify the custody chain and every ancestor",
    description=(
        "Recomputes each hash from the stored payload and checks the links. Verifies the "
        "whole ancestry, not just this lot: a child with a sound chain of its own is still "
        "untrustworthy if its parent was tampered with after the split."
    ),
)
def verify_lot(lot_id: uuid.UUID, db: Session = Depends(get_db)) -> AncestryVerificationOut:
    return AncestryVerificationOut(**TraceService(db).verify_ancestry(lot_id))


@router.get(
    "/lots/{lot_id}/qr",
    summary="PNG QR code for a lot",
    response_class=Response,
    description=(
        "Authenticated: the image encodes the lot's QR token, so serving it openly would "
        "publish the capability the token exists to protect."
    ),
)
def lot_qr(
    lot_id: uuid.UUID,
    principal: Principal = Depends(require_supply_chain),
    db: Session = Depends(get_db),
) -> Response:
    import json

    import qrcode

    lot = TraceService(db).get_lot(lot_id)
    # The QR encodes a compact JSON document rather than a bare URL so that the
    # app can resolve a lot offline from the token it already holds.
    encoded = json.dumps(
        {"v": 1, "t": "satva.lot", "code": lot.lot_code, "tok": lot.qr_token},
        separators=(",", ":"),
    )
    image = qrcode.make(encoded, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{lot.lot_code}.png"'},
    )


@router.post(
    "/lots/{lot_id}/split",
    response_model=LotDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Split a child lot inheriting the parent chain",
    description=(
        "Creates a shelf unit with its own QR. The child's genesis event records the parent's "
        "lot id and head hash, so a consumer scanning the shelf code can walk the chain back "
        "to the farm. Refuses to split a lot whose chain does not verify."
    ),
)
def split_lot(
    lot_id: uuid.UUID,
    payload: LotSplitRequest,
    request: Request,
    principal: Principal = Depends(require_supply_chain),
    db: Session = Depends(get_db),
) -> LotDetail:
    service = TraceService(db)
    parent = service.get_lot(lot_id)
    # A split writes to the parent's chain as well as opening the child's.
    assert_may_write_chain(db, parent, principal)
    child = service.split_lot(
        parent=parent,
        quantity_kg=payload.quantity_kg,
        actor_display_name=principal.user.display_name,
        location_name=payload.location_name,
        grade=payload.grade,
    )
    write_audit(
        db,
        action="trace.lot_split",
        principal=principal,
        object_type="lot",
        object_id=str(child.id),
        request=request,
        context={"parent_lot_id": str(parent.id)},
    )
    db.commit()
    db.refresh(child)
    return _lot_detail(service, child, db, include_qr_token=True)


# --- Custody -----------------------------------------------------------------
@router.post(
    "/custody/{lot_id}",
    response_model=CustodyEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a custody event",
    description=(
        "Append-only. There is no update or delete route, and a database trigger rejects "
        "UPDATE and DELETE on this table, so the append-only property does not depend on "
        "application discipline alone."
    ),
)
def append_custody(
    lot_id: uuid.UUID,
    payload: CustodyEventCreate,
    principal: Principal = Depends(require_supply_chain),
    db: Session = Depends(get_db),
) -> CustodyEventOut:
    service = TraceService(db)
    lot = service.get_lot(lot_id)
    assert_may_write_chain(db, lot, principal)
    event = service.append_event(
        lot=lot,
        event_type=payload.event_type,
        actor_kind=payload.actor_kind,
        actor_ref=payload.actor_ref,
        actor_display_name=payload.actor_display_name or principal.user.display_name,
        location_name=payload.location_name,
        latitude=payload.latitude,
        longitude=payload.longitude,
        quantity_kg=payload.quantity_kg,
        temperature_c=payload.temperature_c,
        humidity_pct=payload.humidity_pct,
        occurred_at=payload.occurred_at,
        extra=payload.extra,
    )
    db.commit()
    db.refresh(event)
    return CustodyEventOut.model_validate(event)


@router.get("/custody/{lot_id}", response_model=list[CustodyEventOut])
def list_custody(lot_id: uuid.UUID, db: Session = Depends(get_db)) -> list[CustodyEventOut]:
    events = db.scalars(
        select(CustodyEvent)
        .where(CustodyEvent.lot_id == lot_id)
        .order_by(CustodyEvent.sequence_no)
    )
    return [CustodyEventOut.model_validate(e) for e in events]


# --- Consumer lookup ---------------------------------------------------------
@router.get(
    "/trace/{qr_token}",
    response_model=ConsumerJourneyOut,
    summary="Consumer journey for a scanned QR code",
    description=(
        "Public. Returns the readable farm-to-shelf journey plus the integrity verdict for "
        "the whole ancestry. If the token is unknown the response is a 404 the app treats as "
        "'no trace available', and screening continues normally."
    ),
)
def consumer_journey(qr_token: str, db: Session = Depends(get_db)) -> ConsumerJourneyOut:
    service = TraceService(db)
    lot = service.get_lot_by_qr(qr_token)
    steps = service.consumer_journey(lot.id)
    ancestry = service.verify_ancestry(lot.id)

    root = lot
    guard = 0
    while root.parent_lot_id is not None and guard < 32:
        parent = db.get(ProduceLot, root.parent_lot_id)
        if parent is None:
            break
        root = parent
        guard += 1

    days_since_harvest = None
    if root.harvest_date:
        days_since_harvest = (date.today() - root.harvest_date).days

    farm = root.farm
    return ConsumerJourneyOut(
        lot_code=lot.lot_code,
        crop=lot.crop,
        cultivar=lot.cultivar,
        harvest_date=root.harvest_date,
        declared_ripening_method=lot.declared_ripening_method,
        farm_name=farm.name if farm else None,
        farm_location=(
            ", ".join(p for p in [farm.village, farm.district, farm.state] if p) if farm else None
        ),
        steps=[JourneyStepOut(**s.__dict__) for s in steps],
        verification=AncestryVerificationOut(**ancestry),
        days_since_harvest=days_since_harvest,
        integrity_note=INTEGRITY_NOTE,
        trace_is_optional_note=TRACE_OPTIONAL_NOTE,
    )


@router.get(
    "/trace/merkle/latest",
    summary="The most recent published daily Merkle root",
    description=(
        "The external anchor over the day's custody records. Anyone holding a custody record "
        "can recompute its hash and check inclusion against this root."
    ),
)
def latest_merkle_root(db: Session = Depends(get_db)) -> dict:
    from app.models.watch import MerkleRoot

    root = db.scalar(select(MerkleRoot).order_by(MerkleRoot.period_date.desc()).limit(1))
    if root is None:
        raise NotFoundError(
            "No Merkle root has been published yet. Roots are generated by the daily "
            "anchoring job."
        )
    return {
        "period_date": root.period_date.isoformat(),
        "root_hash": root.root_hash,
        "leaf_count": root.leaf_count,
        "algorithm": root.algorithm,
        "covered_from": root.covered_from.isoformat(),
        "covered_to": root.covered_to.isoformat(),
        "published_at": root.published_at.isoformat() if root.published_at else None,
        "note": INTEGRITY_NOTE,
    }
