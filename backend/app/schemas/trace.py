"""SATVA Trace schemas."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.core.constants import CustodyEventType, RipeningMethod
from app.schemas.common import SatvaModel


class FarmCreate(SatvaModel):
    name: str = Field(max_length=200)
    entity_type: str = Field(default="farmer", max_length=24)
    village: str | None = Field(default=None, max_length=120)
    block: str | None = Field(default=None, max_length=120)
    district: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    certifications: list[str] = Field(default_factory=list)
    fpo_member_count: int | None = Field(default=None, ge=0)


class FarmOut(SatvaModel):
    id: UUID
    farm_code: str
    name: str
    entity_type: str
    village: str | None
    district: str | None
    state: str | None
    certifications: list[str] | None
    fpo_member_count: int | None
    is_synthetic: bool
    created_at: datetime


class LotCreate(SatvaModel):
    crop: str = Field(max_length=40)
    quantity_kg: float = Field(gt=0, le=100_000)
    farm_id: UUID | None = None
    cultivar: str | None = Field(default=None, max_length=60)
    plot_identifier: str | None = Field(default=None, max_length=80)
    harvest_date: date | None = None
    declared_ripening_method: RipeningMethod = RipeningMethod.UNDECLARED
    grade: str | None = Field(default=None, max_length=24)
    location_name: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class LotSplitRequest(SatvaModel):
    quantity_kg: float = Field(gt=0, le=100_000)
    grade: str | None = Field(default=None, max_length=24)
    location_name: str | None = Field(default=None, max_length=160)


class CustodyEventCreate(SatvaModel):
    event_type: CustodyEventType
    actor_kind: str = Field(max_length=24)
    actor_ref: str | None = Field(default=None, max_length=64)
    actor_display_name: str | None = Field(default=None, max_length=160)
    location_name: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    quantity_kg: float | None = Field(default=None, gt=0)
    temperature_c: float | None = Field(default=None, ge=-40, le=80)
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    occurred_at: datetime | None = None
    extra: dict = Field(default_factory=dict)


class CustodyEventOut(SatvaModel):
    id: UUID
    sequence_no: int
    event_type: str
    actor_kind: str
    actor_display_name: str | None
    location_name: str | None
    quantity_kg: float | None
    temperature_c: float | None
    occurred_at: datetime
    previous_hash: str
    event_hash: str
    hash_algorithm: str
    is_synthetic: bool


class ChainVerificationOut(SatvaModel):
    valid: bool
    length: int
    head_hash: str | None = None
    broken_at_sequence: int | None = None
    failure_kind: str | None = None
    detail: str | None = None


class AncestryVerificationOut(SatvaModel):
    valid: bool
    depth: int
    lots: list[dict]


class LotOut(SatvaModel):
    id: UUID
    lot_code: str
    # Null unless the caller is entitled to it. The lot routes are public by
    # design; the capability token that unlocks a chain is not.
    qr_token: str | None = None
    crop: str
    cultivar: str | None
    plot_identifier: str | None
    harvest_date: date | None
    declared_ripening_method: str
    quantity_kg: float
    grade: str | None
    status: str
    head_hash: str | None
    event_count: int
    parent_lot_id: UUID | None
    farm: FarmOut | None = None
    is_synthetic: bool
    created_at: datetime


class LotDetail(LotOut):
    custody_events: list[CustodyEventOut] = Field(default_factory=list)
    verification: ChainVerificationOut
    child_lot_ids: list[UUID] = Field(default_factory=list)


class JourneyStepOut(SatvaModel):
    sequence_no: int
    event_type: str
    title: str
    occurred_at: datetime
    actor: str | None
    location: str | None
    details: dict


class ConsumerJourneyOut(SatvaModel):
    """What a shopper sees after scanning a shelf QR."""

    lot_code: str
    crop: str
    cultivar: str | None
    harvest_date: date | None
    declared_ripening_method: str
    farm_name: str | None
    farm_location: str | None
    steps: list[JourneyStepOut]
    verification: AncestryVerificationOut
    days_since_harvest: int | None
    integrity_note: str
    trace_is_optional_note: str


class QrPayload(SatvaModel):
    """Contents encoded into a printed QR code."""

    version: int = 1
    lot_code: str
    qr_token: str
    url: str


class MerkleRootOut(SatvaModel):
    id: UUID
    period_date: datetime
    root_hash: str
    leaf_count: int
    algorithm: str
    covered_from: datetime
    covered_to: datetime
    published_at: datetime | None


class MerkleProofOut(SatvaModel):
    leaf_hash: str
    root_hash: str
    period_date: datetime
    proof: list[dict]
    verified: bool
    note: str
