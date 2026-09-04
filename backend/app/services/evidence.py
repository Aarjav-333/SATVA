"""The evidence gate.

This module is the programmatic expression of SATVA's central design principle:

    A photograph alone must never condemn a vendor.

Every operation that would give a scan consequence beyond the scanner's own
screen -- publishing it, clustering it into a hotspot, attaching it to an FSSAI
complaint -- must pass through `require_evidence_grade` here. There is exactly
one function that can promote a scan to CONFIRMATORY, and it demands an accepted
colorimetric reading to do so.

Keeping this in one small module is deliberate. The rule is easy to state and
easy to violate accidentally from a dozen call sites; centralising it means the
rule can be read, reviewed and tested as a unit, and a new endpoint that forgets
to call it fails its test rather than quietly shipping.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    DISCLAIMER_LONG,
    SUPPORTED_CROPS,
    UNVALIDATED_CROPS,
    EvidenceGrade,
    ScreeningVerdict,
)
from app.core.errors import EvidenceRuleViolation, UnsupportedCropError
from app.core.logging import get_logger
from app.models.scan import ChemicalReading, Scan, ScanImage

log = get_logger("satva.evidence")

# What each grade is permitted to do. Referenced by tests so the policy cannot
# drift without a test noticing.
EVIDENCE_CAPABILITIES: dict[str, set[str]] = {
    EvidenceGrade.SCREENING_ONLY: {"view_own"},
    EvidenceGrade.REJECTED: {"view_own"},
    EvidenceGrade.CONFIRMATORY: {
        "view_own",
        "share_with_watch",
        "participate_in_clustering",
        "attach_to_complaint",
        "appear_in_officer_worklist",
    },
}

ACTIONS_REQUIRING_CONFIRMATION = (
    EVIDENCE_CAPABILITIES[EvidenceGrade.CONFIRMATORY]
    - EVIDENCE_CAPABILITIES[EvidenceGrade.SCREENING_ONLY]
)


@dataclass(frozen=True)
class ScreeningBands:
    """Where the advisory verdict boundaries sit on the 0-100 anomaly score.

    These are triage boundaries, not diagnostic thresholds. The only decision
    they drive is whether the app suggests spending five rupees on a strip test.
    """

    not_suspicious_below: float = 35.0
    suspicious_at_or_above: float = 60.0


SCREENING_BANDS = ScreeningBands()


def classify_vision_score(
    score: float, bands: ScreeningBands = SCREENING_BANDS
) -> ScreeningVerdict:
    """Map an anomaly score to an advisory band.

    Note the middle band is INCONCLUSIVE rather than being folded into either
    neighbour. A model that is unsure should say so; collapsing uncertainty into
    "not suspicious" hides risk, and collapsing it into "suspicious" sends people
    to buy strips they do not need.
    """
    if score >= bands.suspicious_at_or_above:
        return ScreeningVerdict.SUSPICIOUS
    if score < bands.not_suspicious_below:
        return ScreeningVerdict.NOT_SUSPICIOUS
    return ScreeningVerdict.INCONCLUSIVE


def assert_crop_supported(crop: str) -> None:
    """Non-negotiable rule 15: refuse unsupported crops rather than guess."""
    key = (crop or "").strip().lower()
    if key in SUPPORTED_CROPS:
        return
    if key in UNVALIDATED_CROPS:
        raise UnsupportedCropError(
            f"SATVA has not been validated for {key.replace('_', ' ')} yet, so it will not "
            "score it. Scoring an unvalidated crop would be a guess dressed up as a "
            "measurement.",
            details={
                "crop": key,
                "reason": "awaiting_controlled_validation",
                "supported_crops": sorted(SUPPORTED_CROPS),
            },
        )
    raise UnsupportedCropError(
        f"'{crop}' is not a crop SATVA recognises.",
        details={
            "crop": crop,
            "reason": "unknown_crop",
            "supported_crops": sorted(SUPPORTED_CROPS),
        },
    )


def is_cultivar_supported(crop: str, cultivar: str | None) -> bool:
    entry = SUPPORTED_CROPS.get((crop or "").strip().lower())
    if entry is None:
        return False
    if cultivar is None:
        return True
    return cultivar.strip().lower() in entry["cultivars"]


def require_evidence_grade(scan: Scan, action: str) -> None:
    """Gate an action on a scan's evidence grade.

    Raises `EvidenceRuleViolation` with an explanation aimed at a developer,
    because reaching this branch means a caller tried to do something the
    product rules forbid.
    """
    permitted = EVIDENCE_CAPABILITIES.get(scan.evidence_grade, set())
    if action in permitted:
        return

    if action in ACTIONS_REQUIRING_CONFIRMATION:
        raise EvidenceRuleViolation(
            "This scan has only been screened visually. A photograph alone is never "
            "treated as evidence in SATVA: complete the confirmatory strip test before "
            f"attempting to {action.replace('_', ' ')}.",
            details={
                "scan_id": str(scan.id),
                "evidence_grade": scan.evidence_grade,
                "action": action,
                "required_grade": EvidenceGrade.CONFIRMATORY.value,
                "disclaimer": DISCLAIMER_LONG,
            },
        )

    raise EvidenceRuleViolation(
        f"Action '{action}' is not permitted for a scan graded '{scan.evidence_grade}'.",
        details={"scan_id": str(scan.id), "evidence_grade": scan.evidence_grade, "action": action},
    )


def can(scan: Scan, action: str) -> bool:
    """Non-raising form, for building response payloads."""
    return action in EVIDENCE_CAPABILITIES.get(scan.evidence_grade, set())


def strip_image_stored(db: Session, scan_id: uuid.UUID) -> bool:
    """Whether the strip photograph a reading was derived from is on file."""
    return (
        db.scalar(
            select(ScanImage.id)
            .where(
                ScanImage.scan_id == scan_id,
                ScanImage.kind == "strip",
                ScanImage.deleted_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def promote_to_confirmatory(db: Session, scan: Scan, reading: ChemicalReading) -> Scan:
    """The only path from SCREENING_ONLY to CONFIRMATORY.

    Requires an *accepted* reading that belongs to this scan, carrying a
    numeric value, and backed by the stored strip photograph it was derived
    from. A refused reading grades the scan REJECTED, which is a deliberate
    outcome rather than a failure: it records that SATVA declined to measure,
    which is itself meaningful and must not be silently discarded.

    The photograph requirement is what keeps a device-computed number
    accountable. The handset computes offline and the server takes its word for
    the value, so the only thing separating a measurement from an assertion is
    that the image remains on file and can be put back through the same
    pipeline (`POST /colorimetry/scans/{id}/recompute`). Promote without it and
    a fabricated reading would be indistinguishable from a real one, for good.
    """
    if reading.scan_id != scan.id:
        raise EvidenceRuleViolation(
            "The chemical reading does not belong to this scan.",
            details={"scan_id": str(scan.id), "reading_scan_id": str(reading.scan_id)},
        )

    if not reading.accepted:
        scan.evidence_grade = EvidenceGrade.REJECTED
        db.flush()
        log.info(
            "scan_graded_rejected",
            scan_id=str(scan.id),
            reason=reading.reject_reason,
        )
        return scan

    if reading.concentration_value is None:
        # Defended by a database CHECK constraint too, but an accepted reading
        # with no value would be a contract violation serious enough to fail
        # loudly here as well.
        raise EvidenceRuleViolation(
            "An accepted chemical reading must carry a numeric value.",
            details={"reading_id": str(reading.id)},
        )

    if not strip_image_stored(db, scan.id):
        # Not an error: the handset attaches the reading before it uploads the
        # photograph, so this is the ordinary state mid-sync. The reading is
        # kept and the scan stays advisory; `POST /scans/{id}/images` runs this
        # gate again once the strip image lands. A scan whose image never
        # arrives simply never becomes evidence.
        log.info(
            "scan_promotion_deferred",
            scan_id=str(scan.id),
            reading_id=str(reading.id),
            reason="no_strip_image",
        )
        return scan

    scan.evidence_grade = EvidenceGrade.CONFIRMATORY
    db.flush()
    log.info(
        "scan_promoted_to_confirmatory",
        scan_id=str(scan.id),
        reading_id=str(reading.id),
        assay=reading.assay,
        exceeds_action_threshold=reading.exceeds_action_threshold,
    )
    return scan


def accepted_reading_for(db: Session, scan_id: uuid.UUID) -> ChemicalReading | None:
    """The most recent accepted reading for a scan, if any."""
    return db.scalar(
        select(ChemicalReading)
        .where(ChemicalReading.scan_id == scan_id, ChemicalReading.accepted.is_(True))
        .order_by(ChemicalReading.created_at.desc())
        .limit(1)
    )


def eligible_for_watch(scan: Scan, reading: ChemicalReading | None) -> tuple[bool, str | None]:
    """Whether a scan may contribute to hotspot clustering.

    Four conditions, all required:
      1. the scan carries a confirmatory grade,
      2. the user opted in to sharing,
      3. a geotag exists (a reading with no location cannot form a hotspot),
      4. the reading is not a known duplicate of an earlier submission.
    """
    if scan.evidence_grade != EvidenceGrade.CONFIRMATORY:
        return False, "not_confirmatory"
    if not scan.shared_with_watch:
        return False, "not_shared_by_user"
    if scan.location is None:
        return False, "no_location"
    if scan.duplicate_of_scan_id is not None:
        return False, "duplicate_submission"
    if reading is None or not reading.accepted:
        return False, "no_accepted_reading"
    return True, None


def retention_deadline(
    now: datetime | None = None, *, has_live_complaint: bool = False
) -> datetime:
    """When a scan's images may be deleted (spec 15.3).

    Images are kept only as long as an associated complaint remains live. With
    no complaint, the short default applies.
    """
    from datetime import timedelta

    from app.core.config import settings

    base = now or datetime.now(UTC)
    days = (
        settings.image_retention_days_with_live_complaint
        if has_live_complaint
        else settings.image_retention_days
    )
    return base + timedelta(days=days)
