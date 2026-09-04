"""/marketplace -- SATVA Direct.

Scoped as P2 in the build plan. Listings, trust scores, ratings and score decay
are implemented and queryable; transactions, payments and settlement are not
built and are documented as out of scope in docs/known_limitations.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_current_principal, require_roles
from app.core.constants import Role
from app.core.errors import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.retail import BuyerRating, MarketplaceListing, TrustScore
from app.models.trace import Farm, ProduceLot
from app.schemas.retail import (
    BuyerRatingCreate,
    ListingCreate,
    ListingOut,
    TrustScoreOut,
)
from app.services.direct.trust import compute_trust_score, current_season

router = APIRouter(prefix="/marketplace", tags=["marketplace"])
require_seller = require_roles(Role.FARMER, Role.RETAILER)


def _listing_out(db: Session, listing: MarketplaceListing) -> ListingOut:
    farm = db.get(Farm, listing.farm_id)
    score = db.scalar(
        select(TrustScore)
        .where(TrustScore.farm_id == listing.farm_id)
        .order_by(TrustScore.computed_at.desc())
        .limit(1)
    )
    return ListingOut(
        id=listing.id,
        farm_id=listing.farm_id,
        farm_name=farm.name if farm else None,
        title=listing.title,
        crop=listing.crop,
        cultivar=listing.cultivar,
        quantity_kg=float(listing.quantity_kg),
        price_inr_per_kg=float(listing.price_inr_per_kg),
        available_from=listing.available_from,
        description=listing.description,
        is_active=listing.is_active,
        lot_id=listing.lot_id,
        trust_score=round(score.score, 1) if score else None,
        trace_available=listing.lot_id is not None,
        is_synthetic=listing.is_synthetic,
        created_at=listing.created_at,
    )


@router.get("/listings", response_model=list[ListingOut], summary="Browse produce listings")
def list_listings(
    crop: str | None = Query(None),
    min_trust_score: float | None = Query(None, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[ListingOut]:
    statement = (
        select(MarketplaceListing)
        .where(MarketplaceListing.is_active.is_(True))
        .order_by(MarketplaceListing.created_at.desc())
        .limit(limit)
    )
    if crop:
        statement = statement.where(MarketplaceListing.crop == crop.lower())

    results = [_listing_out(db, listing) for listing in db.scalars(statement)]
    if min_trust_score is not None:
        results = [
            r for r in results if r.trust_score is not None and r.trust_score >= min_trust_score
        ]
    return results


@router.post(
    "/listings",
    response_model=ListingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a listing",
)
def create_listing(
    payload: ListingCreate,
    principal: Principal = Depends(require_seller),
    db: Session = Depends(get_db),
) -> ListingOut:
    farm = db.get(Farm, payload.farm_id)
    if farm is None:
        raise NotFoundError("No such farm or FPO.")
    if (
        principal.role is Role.FARMER
        and farm.owner_user_id is not None
        and farm.owner_user_id != principal.id
    ):
        raise PermissionDeniedError("That farm belongs to a different account.")

    if payload.lot_id is not None and db.get(ProduceLot, payload.lot_id) is None:
        raise NotFoundError("No such produce lot.")

    listing = MarketplaceListing(
        farm_id=payload.farm_id,
        lot_id=payload.lot_id,
        title=payload.title,
        crop=payload.crop.lower(),
        cultivar=payload.cultivar,
        quantity_kg=payload.quantity_kg,
        price_inr_per_kg=payload.price_inr_per_kg,
        available_from=payload.available_from,
        description=payload.description,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return _listing_out(db, listing)


@router.get(
    "/farms/{farm_id}/trust-score",
    response_model=TrustScoreOut,
    summary="SATVA Trust Score for a farm or FPO",
    description=(
        "Recomputed on request from four components: scan pass rate, Trace completeness, "
        "third-party certifications and buyer ratings, multiplied by a time-decay factor so a "
        "score must be re-earned each season. The component breakdown is returned so a seller "
        "can see exactly which term is holding their score down."
    ),
)
def trust_score(
    farm_id: uuid.UUID, db: Session = Depends(get_db)
) -> TrustScoreOut:
    farm = db.get(Farm, farm_id)
    if farm is None:
        raise NotFoundError("No such farm or FPO.")

    result = compute_trust_score(db, farm)
    season = current_season()

    existing = db.scalar(
        select(TrustScore).where(TrustScore.farm_id == farm_id, TrustScore.season == season)
    )
    if existing is None:
        existing = TrustScore(farm_id=farm_id, season=season)
        db.add(existing)

    existing.score = result.score
    existing.scan_pass_component = result.scan_pass_component
    existing.trace_completeness_component = result.trace_completeness_component
    existing.certification_component = result.certification_component
    existing.buyer_rating_component = result.buyer_rating_component
    existing.decay_factor = result.decay_factor
    existing.sample_size = result.sample_size
    existing.computed_at = datetime.now(UTC)
    existing.breakdown = result.breakdown
    existing.is_synthetic = farm.is_synthetic
    db.commit()
    db.refresh(existing)

    out = TrustScoreOut.model_validate(existing)
    out.farm_name = farm.name
    return out


@router.post(
    "/ratings", status_code=status.HTTP_201_CREATED, summary="Rate a seller after a purchase"
)
def rate_seller(
    payload: BuyerRatingCreate,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(Farm, payload.farm_id) is None:
        raise NotFoundError("No such farm or FPO.")
    rating = BuyerRating(
        farm_id=payload.farm_id,
        listing_id=payload.listing_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    db.add(rating)
    db.commit()
    return {"id": str(rating.id), "rating": rating.rating}
