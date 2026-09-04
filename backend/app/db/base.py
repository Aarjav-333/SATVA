"""Declarative base, shared column mixins, and the schema split.

SATVA stores personally identifying information and analytical scan data in two
separate PostgreSQL schemas. This is the structural enforcement of
non-negotiable rules 11 and 12:

    identity.*    people, phone numbers, sessions, audit trail
    analytics.*   scans, readings, clusters, hotspots  -- no PII columns

There is deliberately **no foreign key** from `analytics` to `identity`.
Linking a scan back to a person is possible only through
`identity.scan_ownership`, a narrow join table that lives on the identity side
and is never read by the clustering or heatmap pipelines.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

IDENTITY_SCHEMA = "identity"
ANALYTICS_SCHEMA = "analytics"

# Deterministic constraint naming keeps Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    """UUID primary keys everywhere.

    Scan and lot identifiers travel through QR codes, complaint documents and
    URLs. Sequential integers would leak volume and allow enumeration of other
    people's evidence, so every table uses an opaque UUID.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SyntheticDataMixin:
    """Marks rows created by the demo seeder.

    The specification is explicit that presentation figures must not be passed
    off as collected evidence. Every seeded row carries `is_synthetic = true`,
    the API echoes it in responses, and both dashboards render a visible
    "demo data" badge for such records.
    """

    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
