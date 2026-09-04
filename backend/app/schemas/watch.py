"""SATVA Watch schemas: public hotspots, officer worklist, complaints.

The two hotspot schemas below are the API-level expression of non-negotiable
rules 4 and 5. `PublicHotspotOut` has no centroid, no member list, no merchant
reference and no device detail -- there is no field on it that could identify a
vendor, so a public endpoint cannot leak one even by mistake.
`OfficerHotspotOut` carries that detail and is only ever returned from
officer-scoped routes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.constants import ClusterSeverity, ComplaintStatus
from app.schemas.common import Disclaimer, GeoPoint, SatvaModel


class WardAggregate(SatvaModel):
    """Ward-level public aggregate. The finest granularity a public surface gets."""

    ward_code: str
    ward_name: str | None
    district: str | None
    state: str | None
    # Ward outline for map rendering. A representative point is supplied when no
    # boundary polygon is available; it is the ward's own centroid, never a
    # cluster centroid, so it cannot be used to locate a vendor.
    representative_point: GeoPoint
    boundary: dict | None = None
    confirmed_reading_count: int
    cluster_count: int
    highest_severity: ClusterSeverity
    crops: list[str] = Field(default_factory=list)
    last_reading_at: datetime | None = None
    contains_demo_data: bool = False


class PublicHotspotOut(SatvaModel):
    """Ward-level view. Deliberately carries nothing vendor-identifying."""

    ward_code: str
    ward_name: str | None
    district: str | None
    state: str | None
    representative_point: GeoPoint
    severity: ClusterSeverity
    confirmed_reading_count: int
    independent_device_count: int
    crops: list[str] = Field(default_factory=list)
    window_start: datetime
    window_end: datetime
    contains_demo_data: bool = False


class PublicHotspotResponse(SatvaModel):
    wards: list[WardAggregate]
    hotspots: list[PublicHotspotOut]
    window_days: int
    total_confirmed_readings: int
    generated_at: datetime
    disclaimer: Disclaimer
    notice: str


class ClusterMemberOut(SatvaModel):
    """Officer-only view of one cluster member."""

    scan_id: UUID
    captured_at: datetime
    concentration_value: float | None
    concentration_unit: str | None
    exceeds_action_threshold: bool
    crop: str | None
    device_pseudonym: str
    location: GeoPoint | None
    merchant_ref: str | None
    is_synthetic: bool


class MerchantDetailOut(SatvaModel):
    """Vendor-level data. Officer-authenticated routes only (rules 4 and 5)."""

    merchant_ref: str
    name: str
    stall_identifier: str | None
    market_name: str | None
    address_line: str | None
    ward_code: str | None
    district: str | None
    fssai_licence_no: str | None
    location: GeoPoint | None
    confirmed_reading_count: int
    is_synthetic: bool


class OfficerHotspotOut(SatvaModel):
    id: UUID
    centroid: GeoPoint
    radius_m: float
    ward_code: str | None
    ward_name: str | None
    district: str | None
    state: str | None
    crop: str | None
    assay: str | None
    window_start: datetime
    window_end: datetime
    member_count: int
    independent_device_count: int
    mean_concentration: float | None
    max_concentration: float | None
    exceedance_rate: float
    severity: ClusterSeverity
    inspection_priority: float
    is_publishable: bool
    suppression_reason: str | None
    contains_demo_data: bool
    diagnostics: dict = Field(default_factory=dict)


class OfficerHotspotDetail(OfficerHotspotOut):
    members: list[ClusterMemberOut] = Field(default_factory=list)
    merchants: list[MerchantDetailOut] = Field(default_factory=list)
    complaint_count: int = 0


class ClusterRunOut(SatvaModel):
    id: UUID
    eps_metres: float
    min_samples: int
    time_window_days: int
    min_independent_devices: int
    input_reading_count: int
    cluster_count: int
    publishable_count: int
    duration_ms: int | None
    created_at: datetime


class ComplaintCreate(SatvaModel):
    scan_id: UUID
    # Optional complainant and vendor details. Everything here is supplied by
    # the user; SATVA never asserts a vendor identity on its own.
    complainant_name: str | None = Field(default=None, max_length=160)
    complainant_phone: str | None = Field(default=None, max_length=20)
    complainant_email: str | None = Field(default=None, max_length=255)
    merchant_name: str | None = Field(default=None, max_length=200)
    merchant_address: str | None = Field(default=None, max_length=500)
    merchant_fssai_licence: str | None = Field(default=None, max_length=32)
    place_description: str | None = Field(default=None, max_length=300)
    narrative: str | None = Field(default=None, max_length=2000)
    include_images: bool = True


class ComplaintOut(SatvaModel):
    id: UUID
    reference_code: str
    scan_id: UUID
    reading_id: UUID
    status: ComplaintStatus
    crop: str | None
    ward_code: str | None
    district: str | None
    state: str | None
    incident_at: datetime | None
    package_sha256: str | None
    submission_channel: str | None
    fssai_reference: str | None
    submitted_by_user_at: datetime | None
    is_synthetic: bool
    created_at: datetime
    download_url: str | None = None
    filing_instructions: str
    submission_notice: str


class ComplaintMarkSubmitted(SatvaModel):
    """Recorded when the user tells SATVA they filed it themselves."""

    submission_channel: str = Field(max_length=60)
    fssai_reference: str | None = Field(default=None, max_length=80)
    submitted_at: datetime | None = None


class WatchStatsOut(SatvaModel):
    confirmed_readings: int
    published_clusters: int
    suppressed_clusters: int
    wards_covered: int
    complaints_prepared: int
    window_days: int
    demo_data_included: bool
