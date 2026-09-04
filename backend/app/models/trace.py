"""SATVA Trace: farms, produce lots, and the append-only custody hash chain.

Integrity model (spec 3.2): each custody row stores

    hash = SHA-256(canonical_payload || previous_hash)

so altering any historical row invalidates every hash after it. A daily Merkle
root over the day's leaf hashes is published as an external anchor. There is no
blockchain, by design: the requirement is tamper-evidence, not consensus.
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

from app.core.constants import RipeningMethod
from app.db.base import (
    ANALYTICS_SCHEMA,
    Base,
    SyntheticDataMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Farm(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A farmer or Farmer Producer Organisation."""

    __tablename__ = "farms"
    __table_args__ = (
        UniqueConstraint("farm_code", name="uq_farms_farm_code"),
        Index("ix_farms_district", "district"),
        {"schema": ANALYTICS_SCHEMA},
    )

    farm_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False, default="farmer")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    village: Mapped[str | None] = mapped_column(String(120), nullable=True)
    block: Mapped[str | None] = mapped_column(String(120), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    certifications: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    fpo_member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    lots: Mapped[list[ProduceLot]] = relationship(back_populates="farm")


class ProduceLot(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """A traceable quantity of produce.

    A lot may be split; children carry `parent_lot_id` and inherit the parent's
    verified chain, which is what makes retailer-created shelf QR codes
    meaningful without re-registering provenance.
    """

    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint("lot_code", name="uq_lots_lot_code"),
        UniqueConstraint("qr_token", name="uq_lots_qr_token"),
        CheckConstraint("quantity_kg > 0", name="quantity_positive"),
        Index("ix_lots_parent", "parent_lot_id"),
        Index("ix_lots_crop_harvest", "crop", "harvest_date"),
        {"schema": ANALYTICS_SCHEMA},
    )

    lot_code: Mapped[str] = mapped_column(String(40), nullable=False)
    qr_token: Mapped[str] = mapped_column(String(64), nullable=False)

    farm_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.farms.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.lots.id", ondelete="SET NULL"),
        nullable=True,
    )

    crop: Mapped[str] = mapped_column(String(40), nullable=False)
    cultivar: Mapped[str | None] = mapped_column(String(60), nullable=True)
    plot_identifier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    harvest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    declared_ripening_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RipeningMethod.UNDECLARED
    )
    quantity_kg: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    grade: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")

    # Denormalised chain state. Authoritative value is recomputed on verify.
    head_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    farm: Mapped[Farm | None] = relationship(back_populates="lots")
    parent_lot: Mapped[ProduceLot | None] = relationship(remote_side="ProduceLot.id")
    custody_events: Mapped[list[CustodyEvent]] = relationship(
        back_populates="lot",
        cascade="all, delete-orphan",
        order_by="CustodyEvent.sequence_no",
    )


class CustodyEvent(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """Append-only custody record.

    Rows are never updated or deleted through the application; a database
    trigger (see the initial migration) rejects UPDATE and DELETE outright, so
    the append-only property does not rely on application discipline alone.
    """

    __tablename__ = "custody_events"
    __table_args__ = (
        UniqueConstraint("lot_id", "sequence_no", name="uq_custody_events_lot_id"),
        UniqueConstraint("event_hash", name="uq_custody_events_event_hash"),
        CheckConstraint("sequence_no >= 0", name="sequence_non_negative"),
        Index("ix_custody_events_lot_seq", "lot_id", "sequence_no"),
        Index("ix_custody_events_occurred", "occurred_at"),
        {"schema": ANALYTICS_SCHEMA},
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.lots.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)

    actor_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="system")
    actor_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)

    location_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_kg: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(String(24), nullable=False, default="sha256-v1")

    merkle_root_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    lot: Mapped[ProduceLot] = relationship(back_populates="custody_events")


class TemperatureTagReading(UUIDPrimaryKeyMixin, TimestampMixin, SyntheticDataMixin, Base):
    """BLE crate-tag reading feeding SATVA Shelf.

    In the hackathon build these arrive either from the retailer dashboard's
    simulator or from the SATVA Node MQTT bridge; a real BLE tag integration is
    documented but not implemented.
    """

    __tablename__ = "temperature_readings"
    __table_args__ = (
        Index("ix_temperature_readings_lot_time", "lot_id", "recorded_at"),
        {"schema": ANALYTICS_SCHEMA},
    )

    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{ANALYTICS_SCHEMA}.lots.id", ondelete="CASCADE"),
        nullable=True,
    )
    tag_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="ble_tag")
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
