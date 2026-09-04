"""SATVA Trust Score (spec 3.4).

Four components, weighted, then decayed:

    score = 100 * decay * ( 0.40 * scan_pass
                          + 0.25 * trace_completeness
                          + 0.20 * certification
                          + 0.15 * buyer_rating )

Design choices worth stating
----------------------------
* **Scan pass rate carries the most weight** because it is the only component
  backed by an independent chemical measurement. Certifications and ratings are
  claims; a confirmed strip reading is evidence.
* **Small samples are shrunk toward the mean.** A farm with two clean scans has
  not demonstrated the same thing as a farm with sixty, and letting two scans
  produce a perfect score would make the number trivially gameable. A
  Bayesian-style prior handles this: the score approaches the observed rate only
  as evidence accumulates.
* **Decay is seasonal, not linear-forever.** The spec requires scores to be
  re-earned each season, so the decay half-life is set to roughly one season and
  a farm with no recent activity drifts back toward the prior rather than
  sitting on a stale reputation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import EvidenceGrade
from app.models.retail import BuyerRating
from app.models.scan import ChemicalReading, Scan
from app.models.trace import CustodyEvent, Farm, ProduceLot

WEIGHTS = {
    "scan_pass": 0.40,
    "trace_completeness": 0.25,
    "certification": 0.20,
    "buyer_rating": 0.15,
}

# Prior strength: how many observations it takes before the observed rate
# dominates the prior. Ten is enough to stop a handful of scans producing a
# perfect score, without being so strong that a genuinely good grower cannot
# climb.
PRIOR_STRENGTH = 10.0
PRIOR_PASS_RATE = 0.75
PRIOR_RATING = 3.5

DECAY_HALF_LIFE_DAYS = 120.0  # roughly one season

# Certifications SATVA recognises, and what each is worth. Nothing here is
# verified by SATVA: these are self-declared and the breakdown says so.
CERTIFICATION_VALUES = {
    "india_organic": 1.0,
    "npop": 1.0,
    "pgs_india": 0.8,
    "globalgap": 0.9,
    "fssai_registered": 0.4,
    "fpo_member": 0.3,
}


@dataclass
class TrustScoreResult:
    score: float
    scan_pass_component: float
    trace_completeness_component: float
    certification_component: float
    buyer_rating_component: float
    decay_factor: float
    sample_size: int
    breakdown: dict = field(default_factory=dict)


def current_season(now: datetime | None = None) -> str:
    """Indian agricultural seasons: kharif, rabi, zaid."""
    reference = now or datetime.now(UTC)
    month = reference.month
    if 6 <= month <= 10:
        season = "kharif"
    elif month >= 11 or month <= 3:
        season = "rabi"
    else:
        season = "zaid"
    year = reference.year if month >= 6 else reference.year - 1
    return f"{year}-{season}"


def _shrink(observed: float, count: int, prior: float, strength: float = PRIOR_STRENGTH) -> float:
    """Pull a rate toward the prior in proportion to how little evidence backs it."""
    return (observed * count + prior * strength) / (count + strength)


def compute_trust_score(
    db: Session, farm: Farm, *, now: datetime | None = None
) -> TrustScoreResult:
    reference = now or datetime.now(UTC)

    # --- 1. Scan pass rate ---------------------------------------------------
    # Confirmed readings on lots traced to this farm. Only accepted readings
    # count; a refused measurement is not evidence either way.
    farm_lot_ids = select(ProduceLot.id).where(ProduceLot.farm_id == farm.id)
    rows = db.execute(
        select(ChemicalReading.exceeds_action_threshold, func.count())
        .join(Scan, Scan.id == ChemicalReading.scan_id)
        .where(
            Scan.lot_id.in_(farm_lot_ids),
            Scan.evidence_grade == EvidenceGrade.CONFIRMATORY,
            ChemicalReading.accepted.is_(True),
        )
        .group_by(ChemicalReading.exceeds_action_threshold)
    ).all()

    failed = sum(count for exceeded, count in rows if exceeded)
    passed = sum(count for exceeded, count in rows if not exceeded)
    total_scans = failed + passed
    observed_pass = (passed / total_scans) if total_scans else PRIOR_PASS_RATE
    scan_pass = _shrink(observed_pass, total_scans, PRIOR_PASS_RATE)

    # --- 2. Trace completeness ----------------------------------------------
    # What fraction of this farm's lots carry a custody chain that goes beyond
    # bare registration. A lot registered and then never handed over records
    # nothing useful about the supply chain.
    lots = list(db.scalars(select(ProduceLot).where(ProduceLot.farm_id == farm.id)))
    if lots:
        complete = 0
        for lot in lots:
            event_count = db.scalar(
                select(func.count())
                .select_from(CustodyEvent)
                .where(CustodyEvent.lot_id == lot.id)
            )
            # Registration plus at least two downstream handovers.
            if (event_count or 0) >= 3:
                complete += 1
        trace_completeness = complete / len(lots)
    else:
        trace_completeness = 0.0

    # --- 3. Certifications ---------------------------------------------------
    declared = [str(c).lower() for c in (farm.certifications or [])]
    certification = min(1.0, sum(CERTIFICATION_VALUES.get(c, 0.1) for c in declared))

    # --- 4. Buyer ratings ----------------------------------------------------
    rating_rows = db.execute(
        select(func.avg(BuyerRating.rating), func.count()).where(BuyerRating.farm_id == farm.id)
    ).first()
    average_rating = (
        float(rating_rows[0]) if rating_rows and rating_rows[0] is not None else PRIOR_RATING
    )
    rating_count = int(rating_rows[1]) if rating_rows else 0
    shrunk_rating = _shrink(average_rating, rating_count, PRIOR_RATING)
    buyer_rating = max(0.0, min(1.0, (shrunk_rating - 1.0) / 4.0))

    # --- Decay ---------------------------------------------------------------
    last_activity = db.scalar(
        select(func.max(ProduceLot.created_at)).where(ProduceLot.farm_id == farm.id)
    )
    if last_activity is None:
        decay = 0.5
        days_since = None
    else:
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=UTC)
        days_since = (reference - last_activity).total_seconds() / 86400.0
        decay = 0.5 ** (max(0.0, days_since) / DECAY_HALF_LIFE_DAYS)
        # Floor the decay so a dormant but previously good grower is not driven
        # to zero, which would make returning to the platform pointless.
        decay = max(0.35, decay)

    raw = (
        WEIGHTS["scan_pass"] * scan_pass
        + WEIGHTS["trace_completeness"] * trace_completeness
        + WEIGHTS["certification"] * certification
        + WEIGHTS["buyer_rating"] * buyer_rating
    )
    score = round(100.0 * raw * decay, 2)

    return TrustScoreResult(
        score=max(0.0, min(100.0, score)),
        scan_pass_component=round(scan_pass, 4),
        trace_completeness_component=round(trace_completeness, 4),
        certification_component=round(certification, 4),
        buyer_rating_component=round(buyer_rating, 4),
        decay_factor=round(decay, 4),
        sample_size=total_scans,
        breakdown={
            "weights": WEIGHTS,
            "confirmed_scans": total_scans,
            "scans_within_threshold": passed,
            "scans_exceeding_threshold": failed,
            "observed_pass_rate": round(observed_pass, 4) if total_scans else None,
            "prior_applied": total_scans < PRIOR_STRENGTH,
            "prior_note": (
                "Scores from few observations are shrunk toward a neutral prior, so a small "
                "number of clean scans cannot produce a perfect score."
            ),
            "lots_total": len(lots),
            "declared_certifications": declared,
            "certifications_note": "Self-declared; SATVA does not verify certification bodies.",
            "buyer_rating_count": rating_count,
            "average_buyer_rating": round(average_rating, 2) if rating_count else None,
            "days_since_last_lot": round(days_since, 1) if days_since is not None else None,
            "decay_half_life_days": DECAY_HALF_LIFE_DAYS,
            "season": current_season(reference),
        },
    )
