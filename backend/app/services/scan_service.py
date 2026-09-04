"""Scan lifecycle: creation, idempotent offline sync, and reading attachment.

The two behaviours that matter here are idempotency and the evidence gate.

Idempotency: the handset queues scans while offline and retries until the server
acknowledges. Retries are guaranteed -- flaky market connectivity is the normal
case, not the exception -- so `client_scan_uid` is a unique key and a repeat
submission returns the original record rather than creating a duplicate.

Evidence gate: nothing in this module promotes a scan on its own. Promotion runs
through `app.services.evidence.promote_to_confirmatory`, which requires an
accepted colorimetric reading.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import EvidenceGrade, ScreeningVerdict
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.identity import Device, ScanOwnership, User
from app.models.scan import ChemicalReading, Scan
from app.services import evidence as evidence_rules
from app.services.watch.dedup import find_duplicate
from app.services.watch.wards import resolve_ward

log = get_logger("satva.scans")


class ScanService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, scan_id: uuid.UUID) -> Scan:
        scan = self.db.get(Scan, scan_id)
        if scan is None:
            raise NotFoundError("No such scan.")
        return scan

    def get_owned(self, scan_id: uuid.UUID, user: User) -> Scan:
        """Fetch a scan the caller owns.

        Ownership lives in the identity schema; a scan row itself carries no
        user id, so this is the only way to answer "is this mine".
        """
        scan = self.get(scan_id)
        ownership = self.db.scalar(
            select(ScanOwnership).where(ScanOwnership.scan_id == scan_id)
        )
        if ownership is None or ownership.user_id != user.id:
            # Deliberately a 404 rather than a 403: telling a caller that a scan
            # exists but belongs to somebody else is itself a disclosure.
            raise NotFoundError("No such scan.")
        return scan

    def find_by_client_uid(self, client_scan_uid: str) -> Scan | None:
        return self.db.scalar(select(Scan).where(Scan.client_scan_uid == client_scan_uid))

    def create(
        self,
        payload,
        *,
        user: User | None = None,
        device: Device | None = None,
        device_pseudonym: str | None = None,
        is_synthetic: bool = False,
    ) -> tuple[Scan, bool]:
        """Create a scan. Returns (scan, created) -- False when it already existed."""
        existing = self.find_by_client_uid(payload.client_scan_uid)
        if existing is not None:
            return existing, False

        evidence_rules.assert_crop_supported(payload.crop)

        pseudonym = device_pseudonym or (device.device_pseudonym if device else None)
        if not pseudonym:
            raise ConflictError(
                "A scan must be attributed to a registered device so that Watch can require "
                "corroboration from independent devices."
            )

        vision = payload.vision
        verdict = vision.verdict if vision else ScreeningVerdict.INCONCLUSIVE
        if vision and vision.anomaly_score is not None:
            # Re-derive the band server-side rather than trusting the client's
            # label: the two must agree, and the server's classification wins.
            verdict = evidence_rules.classify_vision_score(vision.anomaly_score)

        location = None
        ward = None
        if payload.location is not None:
            location = from_shape(
                Point(payload.location.longitude, payload.location.latitude), srid=4326
            )
            ward = resolve_ward(payload.location.latitude, payload.location.longitude)

        scan = Scan(
            client_scan_uid=payload.client_scan_uid,
            device_pseudonym=pseudonym,
            app_version=payload.app_version,
            crop=payload.crop,
            cultivar=payload.cultivar,
            vision_anomaly_score=vision.anomaly_score if vision else None,
            vision_verdict=verdict,
            vision_model_id=vision.model_id if vision else None,
            vision_model_kind=vision.model_kind if vision else None,
            vision_inference_ms=vision.inference_ms if vision else None,
            ripeness_index=vision.ripeness_index if vision else None,
            saliency_summary=(
                {"regions": [r.model_dump() for r in vision.saliency]} if vision else None
            ),
            evidence_grade=EvidenceGrade.SCREENING_ONLY,
            location=location,
            location_accuracy_m=payload.location.accuracy_m if payload.location else None,
            ward_code=payload.ward_code or (ward.ward_code if ward else None),
            ward_name=ward.ward_name if ward else None,
            district=ward.district if ward else None,
            state=ward.state if ward else None,
            merchant_ref=payload.merchant_ref,
            lot_id=payload.lot_id,
            captured_at=payload.captured_at,
            synced_at=datetime.now(UTC),
            captured_offline=payload.captured_offline,
            shared_with_watch=payload.shared_with_watch,
            perceptual_hash=payload.perceptual_hash,
            embedding=payload.embedding,
            notes=payload.notes,
            is_synthetic=is_synthetic,
        )
        self.db.add(scan)

        try:
            self.db.flush()
        except IntegrityError:
            # Two syncs of the same queued scan raced. The unique constraint did
            # its job; return whichever row won.
            self.db.rollback()
            winner = self.find_by_client_uid(payload.client_scan_uid)
            if winner is None:
                raise
            return winner, False

        # Identity linkage lives on the identity side, written separately.
        if user is not None or device is not None:
            self.db.add(
                ScanOwnership(
                    scan_id=scan.id,
                    user_id=user.id if user else None,
                    device_id=device.id if device else None,
                )
            )

        self._check_duplicate(scan)
        self.db.flush()
        log.info(
            "scan_created",
            scan_id=str(scan.id),
            crop=scan.crop,
            verdict=scan.vision_verdict,
            offline=scan.captured_offline,
        )
        return scan, True

    def _check_duplicate(self, scan: Scan) -> None:
        """Flag re-submissions of a photograph already in the system."""
        if not scan.perceptual_hash and scan.embedding is None:
            return
        verdict = find_duplicate(
            self.db,
            perceptual_hash=scan.perceptual_hash,
            embedding=np.asarray(scan.embedding) if scan.embedding is not None else None,
            device_pseudonym=scan.device_pseudonym,
            exclude_scan_id=scan.id,
        )
        if verdict.is_duplicate:
            scan.duplicate_of_scan_id = verdict.matched_scan_id
            log.info(
                "duplicate_scan_detected",
                scan_id=str(scan.id),
                matched=str(verdict.matched_scan_id),
                method=verdict.method,
            )

    def attach_reading(
        self,
        scan: Scan,
        payload,
        *,
        computed_on: str = "device",
        is_synthetic: bool = False,
    ) -> ChemicalReading:
        """Attach a colorimetric result and re-grade the scan accordingly."""
        reading = ChemicalReading(
            scan_id=scan.id,
            assay=str(payload.assay),
            calibration_id=payload.calibration_id,
            accepted=payload.accepted,
            reject_reason=payload.reject_reason,
            reject_detail=payload.reject_detail,
            concentration_value=payload.concentration_value,
            concentration_unit=payload.concentration_unit,
            ci_low=payload.ci_low,
            ci_high=payload.ci_high,
            band_label=payload.band_label,
            exceeds_action_threshold=payload.exceeds_action_threshold,
            delta_e_nearest=payload.delta_e_nearest,
            lab_l=payload.strip_lab[0] if payload.strip_lab else None,
            lab_a=payload.strip_lab[1] if payload.strip_lab else None,
            lab_b=payload.strip_lab[2] if payload.strip_lab else None,
            correction_residual_de=payload.correction_residual_de,
            exposure_score=payload.exposure_score,
            illuminant_tint=payload.illuminant_tint,
            quality_report=payload.quality,
            computed_on=computed_on,
            pipeline_version=payload.pipeline_version,
            is_synthetic=is_synthetic,
        )
        self.db.add(reading)
        self.db.flush()

        evidence_rules.promote_to_confirmatory(self.db, scan, reading)
        return reading

    def capabilities(self, scan: Scan) -> dict:
        """What this scan may currently be used for, plus why not."""
        reading = evidence_rules.accepted_reading_for(self.db, scan.id)
        eligible, blocker = evidence_rules.eligible_for_watch(scan, reading)

        reason = None
        if scan.evidence_grade == EvidenceGrade.SCREENING_ONLY:
            reason = (
                "This scan has only been screened visually. SATVA never treats a photograph "
                "as evidence: complete the confirmatory strip test to unlock reporting."
            )
        elif scan.evidence_grade == EvidenceGrade.REJECTED:
            reason = (
                "The strip reading was refused because capture conditions were outside the "
                "range SATVA can measure reliably. Retake the strip photograph."
            )
        elif not eligible and blocker:
            reason = {
                "not_shared_by_user": "You have not shared this scan with SATVA Watch.",
                "no_location": "This scan has no location, so it cannot contribute to a hotspot.",
                "duplicate_submission": (
                    "This photograph was already submitted, so it counts once."
                ),
            }.get(blocker)

        return {
            "view_own": True,
            "share_with_watch": evidence_rules.can(scan, "share_with_watch"),
            "participate_in_clustering": eligible,
            "attach_to_complaint": evidence_rules.can(scan, "attach_to_complaint"),
            "appear_in_officer_worklist": eligible,
            "reason": reason,
        }

    def set_watch_sharing(self, scan: Scan, shared: bool) -> Scan:
        """Opt a scan in or out of Watch.

        Opting in requires a confirmatory grade, so a screening-only scan can
        never reach the hotspot pipeline even if a client asks for it.
        """
        if shared:
            evidence_rules.require_evidence_grade(scan, "share_with_watch")
            if scan.location is None:
                raise ConflictError(
                    "This scan has no location, so it cannot contribute to a hotspot."
                )
        scan.shared_with_watch = shared
        self.db.flush()
        return scan

    def list_for_user(
        self, user: User, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[Scan], int]:
        from sqlalchemy import func

        owned = select(ScanOwnership.scan_id).where(ScanOwnership.user_id == user.id)
        total = self.db.scalar(
            select(func.count()).select_from(Scan).where(Scan.id.in_(owned))
        )
        rows = list(
            self.db.scalars(
                select(Scan)
                .where(Scan.id.in_(owned))
                .order_by(Scan.captured_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, int(total or 0)
