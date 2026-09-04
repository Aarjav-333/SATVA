"""Identity schema: everything that can identify a person.

Nothing in this module is readable by the clustering or public-heatmap
pipelines. The only bridge between a human being and an analytical scan row is
`ScanOwnership`, which lives here rather than in `analytics` precisely so that
an identity-blind database role cannot traverse it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import Role
from app.db.base import IDENTITY_SCHEMA, Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An account. Consumers may exist with a phone number and nothing else."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("phone_number", name="uq_users_phone_number"),
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "phone_number IS NOT NULL OR email IS NOT NULL",
            name="at_least_one_identifier",
        ),
        Index("ix_users_role_active", "role", "is_active"),
        {"schema": IDENTITY_SCHEMA},
    )

    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.CONSUMER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en-IN")

    # Officers only. Used to scope the FSO worklist to a jurisdiction.
    officer_designation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    officer_jurisdiction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    officer_employee_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Reputation weighting for Watch submissions (spec 3.3, risk table).
    reputation: Mapped[float] = mapped_column(nullable=False, default=1.0)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    devices: Mapped[list[Device]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Device(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A phone installation.

    `device_pseudonym` is the keyed hash that analytics rows carry. It is stored
    here so the reverse lookup exists exactly once, under identity control.
    """

    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("device_pseudonym", name="uq_devices_device_pseudonym"),
        Index("ix_devices_user", "user_id"),
        {"schema": IDENTITY_SCHEMA},
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    device_pseudonym: Mapped[str] = mapped_column(String(48), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="android")
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Device attestation state (spec 3.3 manipulation resistance). Attestation
    # itself is out of hackathon scope; the field records what we would carry.
    attestation_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified")
    trust_weight: Mapped[float] = mapped_column(nullable=False, default=1.0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="devices")


class ScanOwnership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The single bridge between a person and an analytical scan.

    Deliberately has no database-level foreign key to `analytics.scans`: the two
    schemas are meant to be separable, and in a hardened deployment they may live
    in different databases entirely. Referential integrity for this edge is
    enforced in the service layer.
    """

    __tablename__ = "scan_ownership"
    __table_args__ = (
        UniqueConstraint("scan_id", name="uq_scan_ownership_scan_id"),
        Index("ix_scan_ownership_user", "user_id"),
        {"schema": IDENTITY_SCHEMA},
    )

    scan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{IDENTITY_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{IDENTITY_SCHEMA}.devices.id", ondelete="SET NULL"),
        nullable=True,
    )


class OtpChallenge(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Phone OTP challenge.

    The code is stored only as a phone-bound HMAC, so a database read cannot be
    replayed as a login.
    """

    __tablename__ = "otp_challenges"
    __table_args__ = (
        Index("ix_otp_challenges_phone_active", "phone_number", "consumed_at"),
        {"schema": IDENTITY_SCHEMA},
    )

    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False, default="dev")


class Merchant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A vendor or market stall.

    This is identifying information about a business and therefore lives in the
    identity schema. Non-negotiable rules 4 and 5: merchant rows are never
    exposed on a public surface, only through the authenticated officer view.
    Analytics rows reference a merchant by an opaque `merchant_ref` and the join
    is performed exclusively inside officer-scoped services.
    """

    __tablename__ = "merchants"
    __table_args__ = (
        UniqueConstraint("merchant_ref", name="uq_merchants_merchant_ref"),
        Index("ix_merchants_ward", "ward_code"),
        {"schema": IDENTITY_SCHEMA},
    )

    merchant_ref: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    stall_identifier: Mapped[str | None] = mapped_column(String(80), nullable=True)
    market_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    address_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    ward_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    district: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fssai_licence_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only record of sensitive actions.

    Written whenever vendor-level data is viewed, a complaint package is
    generated, evidence is downloaded, or a role is changed.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_actor_time", "actor_user_id", "created_at"),
        Index("ix_audit_log_action_time", "action", "created_at"),
        {"schema": IDENTITY_SCHEMA},
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
