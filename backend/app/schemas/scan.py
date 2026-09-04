"""Scan, colorimetry and sync schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.constants import (
    EvidenceGrade,
    ReagentAssay,
    ScreeningVerdict,
)
from app.schemas.common import Disclaimer, GeoPoint, SatvaModel


class SaliencyRegion(SatvaModel):
    """One region the screening model attended to, in normalised coordinates."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    weight: float = Field(ge=0, le=1)
    label: str | None = None


class VisionResultIn(SatvaModel):
    """Layer A output, computed on the handset.

    The image itself is deliberately NOT uploaded for Layer A (spec 5.1 and the
    privacy stance in slide 10: SATVA should not become a company that stores a
    photograph of everything a consumer buys). Only the score and a small
    saliency summary travel.
    """

    anomaly_score: float = Field(ge=0, le=100)
    verdict: ScreeningVerdict
    model_id: str = Field(max_length=80)
    model_kind: str = Field(max_length=32, description="tflite_int8 | dev_heuristic")
    inference_ms: int | None = Field(default=None, ge=0)
    ripeness_index: float | None = Field(default=None, ge=0, le=1)
    saliency: list[SaliencyRegion] = Field(default_factory=list, max_length=16)


class ScanCreate(SatvaModel):
    """Create a scan record from the handset.

    `client_scan_uid` is the offline queue's idempotency key: re-sending the
    same scan after a failed sync must not create a second record.
    """

    client_scan_uid: str = Field(min_length=8, max_length=64)
    crop: str = Field(max_length=40)
    cultivar: str | None = Field(default=None, max_length=60)
    captured_at: datetime
    captured_offline: bool = False
    vision: VisionResultIn | None = None
    location: GeoPoint | None = None
    ward_code: str | None = Field(default=None, max_length=32)
    merchant_ref: str | None = Field(default=None, max_length=48)
    lot_id: UUID | None = None
    shared_with_watch: bool = False
    perceptual_hash: str | None = Field(default=None, min_length=16, max_length=32)
    embedding: list[float] | None = Field(default=None)
    notes: str | None = Field(default=None, max_length=1000)
    app_version: str | None = Field(default=None, max_length=32)

    @field_validator("crop")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("perceptual_hash")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            int(v, 16)
        except ValueError as exc:
            raise ValueError("perceptual_hash must be hexadecimal") from exc
        return v.lower()

    @model_validator(mode="after")
    def _watch_needs_location(self):
        if self.shared_with_watch and self.location is None:
            raise ValueError(
                "a scan shared with SATVA Watch must carry a location, "
                "otherwise it cannot contribute to a hotspot"
            )
        return self


class ChemicalReadingOut(SatvaModel):
    id: UUID
    assay: str
    calibration_id: str
    accepted: bool
    reject_reason: str | None = None
    reject_detail: str | None = None
    concentration_value: float | None = None
    concentration_unit: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    band_label: str | None = None
    exceeds_action_threshold: bool = False
    is_lab_validated: bool = False
    at_range_limit: bool = False
    delta_e_nearest: float | None = None
    correction_residual_de: float | None = None
    computed_on: str
    pipeline_version: str
    created_at: datetime


class EvidenceCapabilities(SatvaModel):
    """What this scan is currently allowed to do.

    Returned explicitly so the mobile UI never has to guess. If
    `attach_to_complaint` is false, the button is disabled and the reason is
    shown -- rather than the user discovering the restriction after filling in a
    form.
    """

    view_own: bool = True
    share_with_watch: bool = False
    participate_in_clustering: bool = False
    attach_to_complaint: bool = False
    appear_in_officer_worklist: bool = False
    reason: str | None = None


class ScanOut(SatvaModel):
    id: UUID
    client_scan_uid: str
    crop: str
    cultivar: str | None
    vision_anomaly_score: float | None
    vision_verdict: ScreeningVerdict
    vision_model_id: str | None
    vision_model_kind: str | None
    ripeness_index: float | None
    evidence_grade: EvidenceGrade
    captured_at: datetime
    captured_offline: bool
    shared_with_watch: bool
    ward_code: str | None
    ward_name: str | None
    district: str | None
    merchant_ref: str | None
    lot_id: UUID | None
    duplicate_of_scan_id: UUID | None
    is_synthetic: bool
    created_at: datetime

    readings: list[ChemicalReadingOut] = Field(default_factory=list)
    capabilities: EvidenceCapabilities
    disclaimer: Disclaimer


class ScanSyncItem(ScanCreate):
    """One entry in an offline batch sync."""


class ScanSyncRequest(SatvaModel):
    scans: list[ScanSyncItem] = Field(min_length=1, max_length=100)


class ScanSyncResult(SatvaModel):
    client_scan_uid: str
    status: str  # created | already_synced | rejected
    scan_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None


class ScanSyncResponse(SatvaModel):
    results: list[ScanSyncResult]
    created: int
    already_synced: int
    rejected: int


class ColorimetrySubmitRequest(SatvaModel):
    """Submit a colorimetric result computed on the handset.

    The device runs the same pipeline offline. The backend stores the result
    and, when the strip image is also uploaded, can independently recompute it
    -- which is what makes a disputed reading checkable rather than a matter of
    trusting the handset.
    """

    assay: ReagentAssay
    accepted: bool
    calibration_id: str = Field(max_length=60)
    pipeline_version: str = Field(max_length=24)
    reject_reason: str | None = Field(default=None, max_length=48)
    reject_detail: str | None = Field(default=None, max_length=500)
    concentration_value: float | None = None
    concentration_unit: str | None = Field(default=None, max_length=48)
    ci_low: float | None = None
    ci_high: float | None = None
    band_label: str | None = Field(default=None, max_length=40)
    exceeds_action_threshold: bool = False
    delta_e_nearest: float | None = None
    strip_lab: list[float] | None = Field(default=None, min_length=3, max_length=3)
    correction_residual_de: float | None = None
    exposure_score: float | None = None
    illuminant_tint: float | None = None
    quality: dict | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if self.accepted and self.concentration_value is None:
            raise ValueError("an accepted reading must carry a concentration value")
        if not self.accepted and not self.reject_reason:
            raise ValueError("a refused reading must carry a reject_reason")
        if self.accepted and (self.ci_low is None or self.ci_high is None):
            raise ValueError("an accepted reading must carry a confidence interval")
        if (
            self.accepted
            and self.ci_low is not None
            and self.ci_high is not None
            and self.ci_low > self.ci_high
        ):
            raise ValueError("ci_low cannot exceed ci_high")
        return self


class ColorimetryAnalyseResponse(SatvaModel):
    """Result of a server-side strip analysis."""

    accepted: bool
    assay: str
    calibration_id: str | None
    concentration_value: float | None = None
    concentration_unit: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    band_label: str | None = None
    exceeds_action_threshold: bool = False
    is_lab_validated: bool = False
    at_range_limit: bool = False
    range_limit_side: str | None = None
    reject_reason: str | None = None
    reject_detail: str | None = None
    strip_lab_corrected: list[float] | None = None
    delta_e_nearest_stop: float | None = None
    correction_residual_de: float | None = None
    elapsed_ms: int | None = None
    quality: dict = Field(default_factory=dict)
    disclaimer: Disclaimer


class SupportedCropOut(SatvaModel):
    key: str
    label: str
    cultivars: list[str]
    assays: list[str]
    calibrated: bool


class CropCatalogueOut(SatvaModel):
    """What the model will and will not score.

    The refused list is returned alongside the supported list on purpose: the
    app must be able to explain *why* it is declining a crop, which is more
    useful and more honest than an unexplained absence.
    """

    supported: list[SupportedCropOut]
    refused: list[str]
    note: str
