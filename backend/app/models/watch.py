"""Analytics schema: hotspot clusters and FSSAI complaint packages."""

from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
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

from app.core.constants import ClusterSeverity, ComplaintStatus
from app.db.base import (
    ANALYTICS_SCHEMA,
    Base,
    SyntheticDataMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class HotspotCluster(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A DBSCAN cluster of confirmed readings.

    `is_publishable` is the gate for the public heatmap. It is true only when
    the cluster meets the independent-device corroboration threshold
    (spec 3.3), and even then the public API returns ward-level aggregates
    rather than the centroid or the member scans.
    """

    __tablename__ = "hotspot_clusters"
    __table_args__ = (
        CheckConstraint("member_count >= 0", name="member_count_non_negative"),
        CheckConstraint(
            "independent_device_count <= member_count", name="devices_le_members"
        ),
        Index("ix_hotspot_clusters_centroid", "centroid", postgresql_using="gist"),
        Index("ix_hotspot_clusters_window", "window_start", "window_end"),
        Index("ix_hotspot_clusters_publishable", "is_publishable", "severity"),
        Index("ix_hotspot_clusters_ward", "ward_code"),
        {"schema": ANALYTICS_SCHEMA},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    crop: Mapped[str | None] = mapped_column(String(40), nullable=True)
    assay: Mapped[str | None] = mapped_column(String(40), nullable=True)

    centroid = mapped_column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    radius_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ward_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    independent_device_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_concentration: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    max_concentration: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    exceedance_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ClusterSeverity.ADVISORY
    )
    inspection_priority: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    is_publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    members: Mapped[list[ClusterMember]] = relationship(
        back_populates="cluster", cascade="all, delete-orphan"
    )


class ClusterMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Membership edge between a cluster and a confirmed scan."""

    __tablename__ = "cluster_members"
    __table_args__ = (
        UniqueConstraint("cluster_id", "scan_id", name="uq_cluster_members_cluster_id"),
        Index("ix_cluster_members_scan", "scan_id"),
        {"schema": ANALYTICS_SCHEMA},
    )

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.hotspot_clusters.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    device_pseudonym: Mapped[str] = mapped_column(String(48), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    cluster: Mapped[HotspotCluster] = relationship(back_populates="members")


class ClusterRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One execution of the clustering job -- kept for auditability."""

    __tablename__ = "cluster_runs"
    __table_args__ = ({"schema": ANALYTICS_SCHEMA},)

    eps_metres: Mapped[float] = mapped_column(Float, nullable=False)
    min_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    time_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    min_independent_devices: Mapped[int] = mapped_column(Integer, nullable=False)
    input_reading_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publishable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Complaint(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A prepared FSSAI evidence package.

    SATVA never claims to have filed anything. Status begins at DRAFT, becomes
    PACKAGE_READY when the document is generated, and only moves to
    SUBMITTED_BY_USER when the human confirms they filed it through the
    statutory channel (spec 9.3, non-negotiable rule 14).
    """

    __tablename__ = "complaints"
    __table_args__ = (
        CheckConstraint(
            "submitted_by_user_at IS NULL OR fssai_reference IS NOT NULL "
            "OR submission_channel IS NOT NULL",
            name="user_submission_needs_channel_or_ref",
        ),
        Index("ix_complaints_status_created", "status", "created_at"),
        Index("ix_complaints_scan", "scan_id"),
        Index("ix_complaints_ward", "ward_code"),
        {"schema": ANALYTICS_SCHEMA},
    )

    reference_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reading_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ComplaintStatus.DRAFT
    )

    # Complainant details are optional and, when supplied, stored on the
    # identity side. Only the flag lives here.
    complainant_provided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    crop: Mapped[str | None] = mapped_column(String(40), nullable=True)
    merchant_ref: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    incident_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Frozen snapshot of the evidence at generation time, so a later edit
    # elsewhere cannot silently change what was filed.
    evidence_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    package_object_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    package_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    submission_channel: Mapped[str | None] = mapped_column(String(60), nullable=True)
    fssai_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    submitted_by_user_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    officer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MerkleRoot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Daily anchor over the custody chain (spec 3.2).

    Publishing a root means even SATVA's own operators cannot silently rewrite
    history: any edit to a covered custody row changes the recomputed root.
    """

    __tablename__ = "merkle_roots"
    __table_args__ = (
        UniqueConstraint("period_date", name="uq_merkle_roots_period_date"),
        {"schema": ANALYTICS_SCHEMA},
    )

    period_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    root_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    leaf_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    algorithm: Mapped[str] = mapped_column(String(24), nullable=False, default="sha256-merkle-v1")
    covered_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    covered_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_anchor: Mapped[str | None] = mapped_column(Text, nullable=True)
