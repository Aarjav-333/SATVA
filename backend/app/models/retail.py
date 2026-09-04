"""SATVA Shelf and SATVA Direct: retail inventory, shelf-life predictions,
marketplace listings, and trust scores.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ShelfAction
from app.db.base import (
    ANALYTICS_SCHEMA,
    Base,
    SyntheticDataMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class RetailOutlet(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    __tablename__ = "retail_outlets"
    __table_args__ = (
        UniqueConstraint("outlet_code", name="uq_retail_outlets_outlet_code"),
        {"schema": ANALYTICS_SCHEMA},
    )

    outlet_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    address_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    donation_partner: Mapped[str | None] = mapped_column(String(160), nullable=True)

    inventory: Mapped[list[InventoryItem]] = relationship(back_populates="outlet")


class InventoryItem(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A crate or shelf unit currently held by a retailer."""

    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("outlet_id", "sku", name="uq_inventory_items_outlet_id"),
        CheckConstraint("quantity_kg >= 0", name="quantity_non_negative"),
        Index("ix_inventory_items_outlet", "outlet_id"),
        Index("ix_inventory_items_lot", "lot_id"),
        {"schema": ANALYTICS_SCHEMA},
    )

    outlet_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.retail_outlets.id", ondelete="CASCADE"),
        nullable=False,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.lots.id", ondelete="SET NULL"),
        nullable=True,
    )
    sku: Mapped[str] = mapped_column(String(48), nullable=False)
    crop: Mapped[str] = mapped_column(String(40), nullable=False)
    cultivar: Mapped[str | None] = mapped_column(String(60), nullable=True)
    quantity_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    unit_price_inr: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    display_location: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ble_tag_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    outlet: Mapped[RetailOutlet] = relationship(back_populates="inventory")
    predictions: Mapped[list[ShelfPrediction]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ShelfPrediction(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """Remaining-shelf-life estimate for one inventory item.

    `ripeness_index` comes from the regression head sharing the MobileNetV3
    backbone with the adulteration head (spec 3.5). The temperature term is a
    documented degree-day model, not a learned one; see
    `app.services.shelf.predictor` for the exact formulation and its limits.
    """

    __tablename__ = "shelf_predictions"
    __table_args__ = (
        CheckConstraint(
            "ripeness_index IS NULL OR (ripeness_index >= 0 AND ripeness_index <= 1)",
            name="ripeness_index_range",
        ),
        Index("ix_shelf_predictions_item_time", "inventory_item_id", "created_at"),
        Index("ix_shelf_predictions_action", "recommended_action"),
        {"schema": ANALYTICS_SCHEMA},
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.inventory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    ripeness_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_shelf_life_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_low_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_high_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    mean_temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    accumulated_degree_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_source: Mapped[str | None] = mapped_column(String(24), nullable=True)

    recommended_action: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ShelfAction.SELL_NORMALLY
    )
    suggested_markdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    item: Mapped[InventoryItem] = relationship(back_populates="predictions")


class SpoilageEvent(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """Outcome record used to measure whether Shelf actually reduced waste."""

    __tablename__ = "spoilage_events"
    __table_args__ = (
        Index("ix_spoilage_events_outlet_date", "outlet_id", "occurred_on"),
        {"schema": ANALYTICS_SCHEMA},
    )

    outlet_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.retail_outlets.id", ondelete="CASCADE"),
        nullable=False,
    )
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    crop: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    quantity_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False, default="discarded")
    value_inr: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)


# --- SATVA Direct ------------------------------------------------------------
class TrustScore(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """SATVA Trust Score for a farm/FPO (spec 3.4).

    Components are stored separately from the composite so the score is
    explainable: a seller can be told exactly which term is holding them back.
    Scores decay with time and must be re-earned each season.
    """

    __tablename__ = "trust_scores"
    __table_args__ = (
        UniqueConstraint("farm_id", "season", name="uq_trust_scores_farm_id"),
        CheckConstraint("score >= 0 AND score <= 100", name="score_range"),
        {"schema": ANALYTICS_SCHEMA},
    )

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.farms.id", ondelete="CASCADE"),
        nullable=False,
    )
    season: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    scan_pass_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trace_completeness_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    certification_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    buyer_rating_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decay_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MarketplaceListing(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        CheckConstraint("price_inr_per_kg > 0", name="price_positive"),
        Index("ix_marketplace_listings_crop_active", "crop", "is_active"),
        {"schema": ANALYTICS_SCHEMA},
    )

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.farms.id", ondelete="CASCADE"),
        nullable=False,
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.lots.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    crop: Mapped[str] = mapped_column(String(40), nullable=False)
    cultivar: Mapped[str | None] = mapped_column(String(60), nullable=True)
    quantity_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    price_inr_per_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BuyerRating(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    __tablename__ = "buyer_ratings"
    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5", name="rating_range"),
        Index("ix_buyer_ratings_farm", "farm_id"),
        {"schema": ANALYTICS_SCHEMA},
    )

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.farms.id", ondelete="CASCADE"),
        nullable=False,
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
