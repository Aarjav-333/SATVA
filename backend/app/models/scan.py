"""Analytics schema: scans, images, and chemical readings.

No column in this module identifies a person. A scan carries a
`device_pseudonym` (keyed hash, see `app.core.security.pseudonymise_device`) so
that Watch can require corroboration from independent devices without ever
learning who those devices belong to.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from app.core.constants import EvidenceGrade, ScreeningVerdict
from app.db.base import (
    ANALYTICS_SCHEMA,
    Base,
    SyntheticDataMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# Dimensionality of the MobileNetV3-Small penultimate feature vector used for
# near-duplicate detection. Must match ml/export/onnx_export.py.
EMBEDDING_DIM = 576


class Scan(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """One screening event.

    `evidence_grade` is the single gate that the rest of the system consults.
    A scan that has only completed Layer A is SCREENING_ONLY and can never be
    published, clustered, or attached to a complaint (non-negotiable rules 1
    and 2). It is promoted to CONFIRMATORY only by an accepted colorimetric
    reading, and the promotion happens in one place:
    `app.services.evidence.promote_to_confirmatory`.
    """

    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("client_scan_uid", name="uq_scans_client_scan_uid"),
        CheckConstraint(
            "vision_anomaly_score IS NULL OR (vision_anomaly_score >= 0 "
            "AND vision_anomaly_score <= 100)",
            name="anomaly_score_range",
        ),
        Index("ix_scans_location", "location", postgresql_using="gist"),
        Index("ix_scans_crop_captured", "crop", "captured_at"),
        Index("ix_scans_grade_captured", "evidence_grade", "captured_at"),
        Index("ix_scans_device_captured", "device_pseudonym", "captured_at"),
        Index("ix_scans_ward", "ward_code"),
        {"schema": ANALYTICS_SCHEMA},
    )

    # Idempotency key generated on the handset. The offline queue may retry a
    # sync indefinitely; this unique constraint makes re-upload a no-op.
    client_scan_uid: Mapped[str] = mapped_column(String(64), nullable=False)

    device_pseudonym: Mapped[str] = mapped_column(String(48), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    crop: Mapped[str] = mapped_column(String(40), nullable=False)
    cultivar: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # --- Layer A: vision screening (advisory only) --------------------------
    vision_anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    vision_verdict: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScreeningVerdict.INCONCLUSIVE
    )
    vision_model_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    vision_model_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vision_inference_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ripeness_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    saliency_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Evidence state -----------------------------------------------------
    evidence_grade: Mapped[str] = mapped_column(
        String(24), nullable=False, default=EvidenceGrade.SCREENING_ONLY, index=True
    )

    # --- Location (generalised for public surfaces) -------------------------
    location = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ward_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Opaque merchant reference. Resolving it to a name requires the identity
    # schema and an officer role (non-negotiable rules 4 and 5).
    merchant_ref: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Watch participation is opt-in per scan.
    shared_with_watch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Duplicate-resistance (spec 3.3).
    perceptual_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    duplicate_of_scan_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    images: Mapped[list[ScanImage]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )
    readings: Mapped[list[ChemicalReading]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )

    @property
    def is_evidence(self) -> bool:
        """True only for scans that carry a confirmed chemical reading."""
        return self.evidence_grade == EvidenceGrade.CONFIRMATORY


class ScanImage(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """Object-store pointer for an image belonging to a scan.

    Spec 15.3 limits retention: `retain_until` is set at write time and the
    housekeeping worker deletes expired objects unless a live complaint extends
    the deadline.
    """

    __tablename__ = "scan_images"
    __table_args__ = (
        Index("ix_scan_images_scan", "scan_id"),
        Index("ix_scan_images_retention", "retain_until"),
        {"schema": ANALYTICS_SCHEMA},
    )

    scan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # produce | strip | reference
    object_key: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(60), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scan: Mapped[Scan] = relationship(back_populates="images")


class ChemicalReading(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """Layer B colorimetric result -- the only output SATVA treats as evidence.

    A refused measurement is still stored (with `accepted = false` and a
    `reject_reason`) because refusals are diagnostically useful and because the
    system must be able to prove that it declined rather than guessed.
    """

    __tablename__ = "chemical_readings"
    __table_args__ = (
        CheckConstraint(
            "(accepted = false) OR (concentration_value IS NOT NULL)",
            name="accepted_reading_needs_value",
        ),
        CheckConstraint(
            "(accepted = true) OR (reject_reason IS NOT NULL)",
            name="rejected_reading_needs_reason",
        ),
        Index("ix_chemical_readings_scan", "scan_id"),
        Index("ix_chemical_readings_assay_accepted", "assay", "accepted"),
        {"schema": ANALYTICS_SCHEMA},
    )

    scan_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    assay: Mapped[str] = mapped_column(String(40), nullable=False)
    calibration_id: Mapped[str] = mapped_column(String(60), nullable=False)

    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reject_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reject_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Result. Units are carried explicitly because reagent series differ.
    concentration_value: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    concentration_unit: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ci_low: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    ci_high: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    band_label: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exceeds_action_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Measurement provenance -- everything needed to re-derive the number.
    delta_e_nearest: Mapped[float | None] = mapped_column(Float, nullable=True)
    lab_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    lab_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    lab_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    correction_residual_de: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposure_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    illuminant_tint: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    computed_on: Mapped[str] = mapped_column(String(16), nullable=False, default="device")
    pipeline_version: Mapped[str] = mapped_column(String(24), nullable=False, default="1.0.0")

    scan: Mapped[Scan] = relationship(back_populates="readings")
