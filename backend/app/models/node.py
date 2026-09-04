"""SATVA Node: field-hardware device registry and telemetry.

The ingestion path is real: an ESP32 publishes to MQTT, a bridge forwards to
`POST /devices/telemetry`, and readings land here. The *sensor science* is not
validated -- an MQ-series gas sensor gives a broad reducing-gas response, not a
selective acetylene assay, so node telemetry is recorded as an advisory signal
that can raise a crate for confirmatory strip testing. It is never treated as a
chemical confirmation, and `app.services.evidence` will not promote a scan on
node data alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    ANALYTICS_SCHEMA,
    Base,
    SyntheticDataMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class DeviceRecord(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A registered SATVA Node unit."""

    __tablename__ = "device_records"
    __table_args__ = (
        UniqueConstraint("node_uid", name="uq_device_records_node_uid"),
        Index("ix_device_records_market", "market_name"),
        {"schema": ANALYTICS_SCHEMA},
    )

    node_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hardware_revision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Baseline resistance in clean air, needed to interpret MQ-sensor ratios.
    mq_r0_ohms: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    telemetry: Mapped[list[NodeTelemetry]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )


class NodeTelemetry(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """One MQTT telemetry sample from a node."""

    __tablename__ = "node_telemetry"
    __table_args__ = (
        UniqueConstraint("device_id", "sample_uid", name="uq_node_telemetry_device_id"),
        CheckConstraint(
            "humidity_pct IS NULL OR (humidity_pct >= 0 AND humidity_pct <= 100)",
            name="humidity_range",
        ),
        Index("ix_node_telemetry_device_time", "device_id", "recorded_at"),
        {"schema": ANALYTICS_SCHEMA},
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.device_records.id", ondelete="CASCADE"),
        nullable=False,
    )
    sample_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # DHT22
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # MQ-series gas sensor
    mq_raw_adc: Mapped[int | None] = mapped_column(nullable=True)
    mq_rs_ohms: Mapped[float | None] = mapped_column(Float, nullable=True)
    mq_rs_r0_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Advisory only. Never an evidence-grade chemical result.
    advisory_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    advisory_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    lot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    device: Mapped[DeviceRecord] = relationship(back_populates="telemetry")
