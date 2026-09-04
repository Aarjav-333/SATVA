"""SATVA Shelf, Direct and Node schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.core.constants import ShelfAction
from app.schemas.common import SatvaModel


# --- Shelf -------------------------------------------------------------------
class OutletOut(SatvaModel):
    id: UUID
    outlet_code: str
    name: str
    ward_code: str | None
    district: str | None
    donation_partner: str | None
    is_synthetic: bool


class InventoryItemCreate(SatvaModel):
    sku: str = Field(max_length=48)
    crop: str = Field(max_length=40)
    quantity_kg: float = Field(ge=0, le=100_000)
    cultivar: str | None = Field(default=None, max_length=60)
    lot_id: UUID | None = None
    unit_price_inr: float | None = Field(default=None, ge=0)
    received_at: datetime | None = None
    display_location: str | None = Field(default=None, max_length=80)
    ble_tag_ref: str | None = Field(default=None, max_length=64)


class ShelfPredictionOut(SatvaModel):
    id: UUID
    inventory_item_id: UUID
    ripeness_index: float | None
    freshness_score: float | None
    remaining_shelf_life_days: float | None
    confidence_low_days: float | None
    confidence_high_days: float | None
    mean_temperature_c: float | None
    accumulated_degree_hours: float | None
    temperature_source: str | None
    recommended_action: ShelfAction
    suggested_markdown_pct: float | None
    rationale: dict | None
    model_id: str | None
    is_synthetic: bool
    created_at: datetime


class InventoryItemOut(SatvaModel):
    id: UUID
    sku: str
    crop: str
    cultivar: str | None
    quantity_kg: float
    unit_price_inr: float | None
    received_at: datetime
    display_location: str | None
    ble_tag_ref: str | None
    lot_id: UUID | None
    lot_code: str | None = None
    trace_status: str = "no_trace"
    days_in_stock: float | None = None
    latest_prediction: ShelfPredictionOut | None = None
    is_synthetic: bool


class TemperatureReadingIn(SatvaModel):
    tag_ref: str = Field(max_length=64)
    temperature_c: float = Field(ge=-40, le=80)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    recorded_at: datetime | None = None
    lot_id: UUID | None = None
    source: str = Field(default="ble_tag", max_length=24)
    is_simulated: bool = True


class ShelfSummaryOut(SatvaModel):
    outlet: OutletOut
    total_items: int
    total_kg: float
    action_counts: dict[str, int]
    at_risk_value_inr: float
    traced_item_count: int
    generated_at: datetime
    demo_data_included: bool


class SpoilageTrendPoint(SatvaModel):
    day: date
    discarded_kg: float
    donated_kg: float
    marked_down_kg: float
    value_inr: float


class SpoilageAnalyticsOut(SatvaModel):
    outlet_id: UUID
    window_days: int
    points: list[SpoilageTrendPoint]
    total_discarded_kg: float
    total_donated_kg: float
    total_value_inr: float
    demo_data_included: bool


# --- Direct ------------------------------------------------------------------
class TrustScoreOut(SatvaModel):
    farm_id: UUID
    farm_name: str | None = None
    season: str
    score: float
    scan_pass_component: float
    trace_completeness_component: float
    certification_component: float
    buyer_rating_component: float
    decay_factor: float
    sample_size: int
    computed_at: datetime
    breakdown: dict | None
    is_synthetic: bool


class ListingCreate(SatvaModel):
    farm_id: UUID
    title: str = Field(max_length=160)
    crop: str = Field(max_length=40)
    quantity_kg: float = Field(gt=0)
    price_inr_per_kg: float = Field(gt=0)
    cultivar: str | None = Field(default=None, max_length=60)
    lot_id: UUID | None = None
    available_from: date | None = None
    description: str | None = Field(default=None, max_length=2000)


class ListingOut(SatvaModel):
    id: UUID
    farm_id: UUID
    farm_name: str | None = None
    title: str
    crop: str
    cultivar: str | None
    quantity_kg: float
    price_inr_per_kg: float
    available_from: date | None
    description: str | None
    is_active: bool
    lot_id: UUID | None
    trust_score: float | None = None
    trace_available: bool = False
    is_synthetic: bool
    created_at: datetime


class BuyerRatingCreate(SatvaModel):
    farm_id: UUID
    rating: int = Field(ge=1, le=5)
    listing_id: UUID | None = None
    comment: str | None = Field(default=None, max_length=1000)


# --- Node --------------------------------------------------------------------
class NodeRegisterRequest(SatvaModel):
    node_uid: str = Field(max_length=64)
    label: str | None = Field(default=None, max_length=120)
    firmware_version: str | None = Field(default=None, max_length=32)
    hardware_revision: str | None = Field(default=None, max_length=32)
    market_name: str | None = Field(default=None, max_length=160)
    ward_code: str | None = Field(default=None, max_length=32)
    district: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    mq_r0_ohms: float | None = Field(default=None, gt=0)
    is_simulated: bool = True


class NodeTelemetryIn(SatvaModel):
    """One MQTT sample. Matches hardware/docs/mqtt_payload_schema.json."""

    node_uid: str = Field(max_length=64)
    sample_uid: str = Field(max_length=64)
    recorded_at: datetime | None = None
    temperature_c: float | None = Field(default=None, ge=-40, le=80)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    mq_raw_adc: int | None = Field(default=None, ge=0, le=65535)
    mq_rs_ohms: float | None = Field(default=None, gt=0)
    lot_id: UUID | None = None
    firmware_version: str | None = Field(default=None, max_length=32)


class NodeTelemetryOut(SatvaModel):
    id: UUID
    node_uid: str
    sample_uid: str
    recorded_at: datetime
    temperature_c: float | None
    humidity_pct: float | None
    mq_rs_r0_ratio: float | None
    advisory_flag: bool
    advisory_reason: str | None
    is_synthetic: bool
    evidence_note: str


class NodeOut(SatvaModel):
    id: UUID
    node_uid: str
    label: str | None
    market_name: str | None
    ward_code: str | None
    district: str | None
    is_active: bool
    is_simulated: bool
    last_seen_at: datetime | None
    recent_advisory_count: int = 0
