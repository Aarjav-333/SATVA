"""Initial SATVA schema.

Creates the two schemas that carry the privacy boundary, installs the required
extensions, builds every table, and then adds the two protections that must live
in the database rather than in application code:

* an append-only trigger on ``analytics.custody_events``, so tamper-evidence
  does not rely on the application remembering not to issue an UPDATE; and
* a ``satva_analytics`` role with no grant on the ``identity`` schema, so the
  clustering and heatmap workloads are structurally unable to read personal
  data (non-negotiable rule 12).

Revision ID: 0001
"""

from __future__ import annotations

import geoalchemy2
import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

IDENTITY = "identity"
ANALYTICS = "analytics"


def upgrade() -> None:
    # --- Extensions and schemas --------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {IDENTITY}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {ANALYTICS}")

    # ======================================================================
    # identity schema -- personally identifying information
    # ======================================================================
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("phone_verified", sa.Boolean(), nullable=False),
        sa.Column("locale", sa.String(10), nullable=False),
        sa.Column("officer_designation", sa.String(120), nullable=True),
        sa.Column("officer_jurisdiction", sa.String(120), nullable=True),
        sa.Column("officer_employee_ref", sa.String(64), nullable=True),
        sa.Column("reputation", sa.Float(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "phone_number IS NOT NULL OR email IS NOT NULL",
            name="ck_users_at_least_one_identifier",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("phone_number", name="uq_users_phone_number"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema=IDENTITY,
    )
    op.create_index("ix_users_role_active", "users", ["role", "is_active"], schema=IDENTITY)
    op.create_index("ix_identity_users_created_at", "users", ["created_at"], schema=IDENTITY)

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("device_pseudonym", sa.String(48), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("model_name", sa.String(80), nullable=True),
        sa.Column("attestation_state", sa.String(24), nullable=False),
        sa.Column("trust_weight", sa.Float(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{IDENTITY}.users.id"], name="fk_devices_user_id_users", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_devices"),
        sa.UniqueConstraint("device_pseudonym", name="uq_devices_device_pseudonym"),
        schema=IDENTITY,
    )
    op.create_index("ix_devices_user", "devices", ["user_id"], schema=IDENTITY)
    op.create_index("ix_identity_devices_created_at", "devices", ["created_at"], schema=IDENTITY)

    op.create_table(
        "scan_ownership",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{IDENTITY}.users.id"], name="fk_scan_ownership_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], [f"{IDENTITY}.devices.id"], name="fk_scan_ownership_device_id_devices", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_scan_ownership"),
        sa.UniqueConstraint("scan_id", name="uq_scan_ownership_scan_id"),
        schema=IDENTITY,
    )
    op.create_index("ix_scan_ownership_user", "scan_ownership", ["user_id"], schema=IDENTITY)
    op.create_index("ix_identity_scan_ownership_created_at", "scan_ownership", ["created_at"], schema=IDENTITY)

    op.create_table(
        "otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_number", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_otp_challenges"),
        schema=IDENTITY,
    )
    op.create_index("ix_otp_challenges_phone_active", "otp_challenges", ["phone_number", "consumed_at"], schema=IDENTITY)
    op.create_index("ix_identity_otp_challenges_created_at", "otp_challenges", ["created_at"], schema=IDENTITY)

    op.create_table(
        "merchants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_ref", sa.String(48), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("stall_identifier", sa.String(80), nullable=True),
        sa.Column("market_name", sa.String(160), nullable=True),
        sa.Column("address_line", sa.Text(), nullable=True),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("fssai_licence_no", sa.String(32), nullable=True),
        sa.Column("contact_phone", sa.String(20), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_merchants"),
        sa.UniqueConstraint("merchant_ref", name="uq_merchants_merchant_ref"),
        schema=IDENTITY,
    )
    op.create_index("ix_merchants_ward", "merchants", ["ward_code"], schema=IDENTITY)
    op.create_index("ix_identity_merchants_created_at", "merchants", ["created_at"], schema=IDENTITY)

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("object_type", sa.String(60), nullable=True),
        sa.Column("object_id", sa.String(80), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        schema=IDENTITY,
    )
    op.create_index("ix_audit_log_actor_time", "audit_log", ["actor_user_id", "created_at"], schema=IDENTITY)
    op.create_index("ix_audit_log_action_time", "audit_log", ["action", "created_at"], schema=IDENTITY)

    # ======================================================================
    # analytics schema -- no personally identifying information
    # ======================================================================
    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_scan_uid", sa.String(64), nullable=False),
        sa.Column("device_pseudonym", sa.String(48), nullable=False),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("crop", sa.String(40), nullable=False),
        sa.Column("cultivar", sa.String(60), nullable=True),
        sa.Column("vision_anomaly_score", sa.Float(), nullable=True),
        sa.Column("vision_verdict", sa.String(32), nullable=False),
        sa.Column("vision_model_id", sa.String(80), nullable=True),
        sa.Column("vision_model_kind", sa.String(32), nullable=True),
        sa.Column("vision_inference_ms", sa.Integer(), nullable=True),
        sa.Column("ripeness_index", sa.Float(), nullable=True),
        sa.Column("saliency_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("evidence_grade", sa.String(24), nullable=False),
        sa.Column("location", geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, from_text="ST_GeogFromText", name="geography"), nullable=True),
        sa.Column("location_accuracy_m", sa.Float(), nullable=True),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("ward_name", sa.String(120), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("merchant_ref", sa.String(48), nullable=True),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_offline", sa.Boolean(), nullable=False),
        sa.Column("shared_with_watch", sa.Boolean(), nullable=False),
        sa.Column("perceptual_hash", sa.String(32), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(576), nullable=True),
        sa.Column("duplicate_of_scan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "vision_anomaly_score IS NULL OR (vision_anomaly_score >= 0 AND vision_anomaly_score <= 100)",
            name="ck_scans_anomaly_score_range",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scans"),
        sa.UniqueConstraint("client_scan_uid", name="uq_scans_client_scan_uid"),
        schema=ANALYTICS,
    )
    op.create_index("ix_scans_location", "scans", ["location"], postgresql_using="gist", schema=ANALYTICS)
    op.create_index("ix_scans_crop_captured", "scans", ["crop", "captured_at"], schema=ANALYTICS)
    op.create_index("ix_scans_grade_captured", "scans", ["evidence_grade", "captured_at"], schema=ANALYTICS)
    op.create_index("ix_scans_device_captured", "scans", ["device_pseudonym", "captured_at"], schema=ANALYTICS)
    op.create_index("ix_scans_ward", "scans", ["ward_code"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_perceptual_hash", "scans", ["perceptual_hash"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_merchant_ref", "scans", ["merchant_ref"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_captured_at", "scans", ["captured_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_created_at", "scans", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_evidence_grade", "scans", ["evidence_grade"], schema=ANALYTICS)
    op.create_index("ix_analytics_scans_is_synthetic", "scans", ["is_synthetic"], schema=ANALYTICS)
    # Approximate-nearest-neighbour index for near-duplicate image detection.
    op.execute(
        f"CREATE INDEX ix_scans_embedding_cosine ON {ANALYTICS}.scans "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "scan_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("object_key", sa.String(400), nullable=False),
        sa.Column("content_type", sa.String(60), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["scan_id"], [f"{ANALYTICS}.scans.id"], name="fk_scan_images_scan_id_scans", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_scan_images"),
        schema=ANALYTICS,
    )
    op.create_index("ix_scan_images_scan", "scan_images", ["scan_id"], schema=ANALYTICS)
    op.create_index("ix_scan_images_retention", "scan_images", ["retain_until"], schema=ANALYTICS)
    op.create_index("ix_analytics_scan_images_sha256", "scan_images", ["sha256"], schema=ANALYTICS)
    op.create_index("ix_analytics_scan_images_created_at", "scan_images", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_scan_images_is_synthetic", "scan_images", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "chemical_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assay", sa.String(40), nullable=False),
        sa.Column("calibration_id", sa.String(60), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reject_reason", sa.String(48), nullable=True),
        sa.Column("reject_detail", sa.Text(), nullable=True),
        sa.Column("concentration_value", sa.Numeric(10, 4), nullable=True),
        sa.Column("concentration_unit", sa.String(48), nullable=True),
        sa.Column("ci_low", sa.Numeric(10, 4), nullable=True),
        sa.Column("ci_high", sa.Numeric(10, 4), nullable=True),
        sa.Column("band_label", sa.String(40), nullable=True),
        sa.Column("exceeds_action_threshold", sa.Boolean(), nullable=False),
        sa.Column("delta_e_nearest", sa.Float(), nullable=True),
        sa.Column("lab_l", sa.Float(), nullable=True),
        sa.Column("lab_a", sa.Float(), nullable=True),
        sa.Column("lab_b", sa.Float(), nullable=True),
        sa.Column("correction_residual_de", sa.Float(), nullable=True),
        sa.Column("exposure_score", sa.Float(), nullable=True),
        sa.Column("illuminant_tint", sa.Float(), nullable=True),
        sa.Column("quality_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("computed_on", sa.String(16), nullable=False),
        sa.Column("pipeline_version", sa.String(24), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # These two constraints are the evidence rule expressed in the schema:
        # an accepted reading must carry a number, and a refusal must carry a
        # reason. Neither state can be created by any code path.
        sa.CheckConstraint("(accepted = false) OR (concentration_value IS NOT NULL)", name="ck_chemical_readings_accepted_reading_needs_value"),
        sa.CheckConstraint("(accepted = true) OR (reject_reason IS NOT NULL)", name="ck_chemical_readings_rejected_reading_needs_reason"),
        sa.ForeignKeyConstraint(["scan_id"], [f"{ANALYTICS}.scans.id"], name="fk_chemical_readings_scan_id_scans", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_chemical_readings"),
        schema=ANALYTICS,
    )
    op.create_index("ix_chemical_readings_scan", "chemical_readings", ["scan_id"], schema=ANALYTICS)
    op.create_index("ix_chemical_readings_assay_accepted", "chemical_readings", ["assay", "accepted"], schema=ANALYTICS)
    op.create_index("ix_analytics_chemical_readings_created_at", "chemical_readings", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_chemical_readings_is_synthetic", "chemical_readings", ["is_synthetic"], schema=ANALYTICS)

    # --- Trace -------------------------------------------------------------
    op.create_table(
        "farms",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("farm_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("village", sa.String(120), nullable=True),
        sa.Column("block", sa.String(120), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("certifications", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fpo_member_count", sa.Integer(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_farms"),
        sa.UniqueConstraint("farm_code", name="uq_farms_farm_code"),
        schema=ANALYTICS,
    )
    op.create_index("ix_farms_district", "farms", ["district"], schema=ANALYTICS)
    op.create_index("ix_analytics_farms_created_at", "farms", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_farms_is_synthetic", "farms", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "lots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_code", sa.String(40), nullable=False),
        sa.Column("qr_token", sa.String(64), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crop", sa.String(40), nullable=False),
        sa.Column("cultivar", sa.String(60), nullable=True),
        sa.Column("plot_identifier", sa.String(80), nullable=True),
        sa.Column("harvest_date", sa.Date(), nullable=True),
        sa.Column("declared_ripening_method", sa.String(32), nullable=False),
        sa.Column("quantity_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("grade", sa.String(24), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("head_hash", sa.String(64), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity_kg > 0", name="ck_lots_quantity_positive"),
        sa.ForeignKeyConstraint(["farm_id"], [f"{ANALYTICS}.farms.id"], name="fk_lots_farm_id_farms", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_lot_id"], [f"{ANALYTICS}.lots.id"], name="fk_lots_parent_lot_id_lots", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_lots"),
        sa.UniqueConstraint("lot_code", name="uq_lots_lot_code"),
        sa.UniqueConstraint("qr_token", name="uq_lots_qr_token"),
        schema=ANALYTICS,
    )
    op.create_index("ix_lots_parent", "lots", ["parent_lot_id"], schema=ANALYTICS)
    op.create_index("ix_lots_crop_harvest", "lots", ["crop", "harvest_date"], schema=ANALYTICS)
    op.create_index("ix_analytics_lots_created_at", "lots", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_lots_is_synthetic", "lots", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "custody_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_kind", sa.String(24), nullable=False),
        sa.Column("actor_ref", sa.String(64), nullable=True),
        sa.Column("actor_display_name", sa.String(160), nullable=True),
        sa.Column("location_name", sa.String(160), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("quantity_kg", sa.Numeric(10, 2), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("hash_algorithm", sa.String(24), nullable=False),
        sa.Column("merkle_root_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sequence_no >= 0", name="ck_custody_events_sequence_non_negative"),
        sa.ForeignKeyConstraint(["lot_id"], [f"{ANALYTICS}.lots.id"], name="fk_custody_events_lot_id_lots", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_custody_events"),
        sa.UniqueConstraint("lot_id", "sequence_no", name="uq_custody_events_lot_id"),
        sa.UniqueConstraint("event_hash", name="uq_custody_events_event_hash"),
        schema=ANALYTICS,
    )
    op.create_index("ix_custody_events_lot_seq", "custody_events", ["lot_id", "sequence_no"], schema=ANALYTICS)
    op.create_index("ix_custody_events_occurred", "custody_events", ["occurred_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_custody_events_created_at", "custody_events", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_custody_events_is_synthetic", "custody_events", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "temperature_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tag_ref", sa.String(64), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], [f"{ANALYTICS}.lots.id"], name="fk_temperature_readings_lot_id_lots", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_temperature_readings"),
        schema=ANALYTICS,
    )
    op.create_index("ix_temperature_readings_lot_time", "temperature_readings", ["lot_id", "recorded_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_temperature_readings_created_at", "temperature_readings", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_temperature_readings_is_synthetic", "temperature_readings", ["is_synthetic"], schema=ANALYTICS)

    # --- Watch -------------------------------------------------------------
    op.create_table(
        "cluster_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eps_metres", sa.Float(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("time_window_days", sa.Integer(), nullable=False),
        sa.Column("min_independent_devices", sa.Integer(), nullable=False),
        sa.Column("input_reading_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False),
        sa.Column("publishable_count", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cluster_runs"),
        schema=ANALYTICS,
    )
    op.create_index("ix_analytics_cluster_runs_created_at", "cluster_runs", ["created_at"], schema=ANALYTICS)

    op.create_table(
        "hotspot_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("crop", sa.String(40), nullable=True),
        sa.Column("assay", sa.String(40), nullable=True),
        sa.Column("centroid", geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, from_text="ST_GeogFromText", name="geography"), nullable=False),
        sa.Column("radius_m", sa.Float(), nullable=False),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("ward_name", sa.String(120), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("independent_device_count", sa.Integer(), nullable=False),
        sa.Column("mean_concentration", sa.Numeric(10, 4), nullable=True),
        sa.Column("max_concentration", sa.Numeric(10, 4), nullable=True),
        sa.Column("exceedance_rate", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("inspection_priority", sa.Float(), nullable=False),
        sa.Column("is_publishable", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(80), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("member_count >= 0", name="ck_hotspot_clusters_member_count_non_negative"),
        sa.CheckConstraint("independent_device_count <= member_count", name="ck_hotspot_clusters_devices_le_members"),
        sa.PrimaryKeyConstraint("id", name="pk_hotspot_clusters"),
        schema=ANALYTICS,
    )
    op.create_index("ix_hotspot_clusters_centroid", "hotspot_clusters", ["centroid"], postgresql_using="gist", schema=ANALYTICS)
    op.create_index("ix_hotspot_clusters_window", "hotspot_clusters", ["window_start", "window_end"], schema=ANALYTICS)
    op.create_index("ix_hotspot_clusters_publishable", "hotspot_clusters", ["is_publishable", "severity"], schema=ANALYTICS)
    op.create_index("ix_hotspot_clusters_ward", "hotspot_clusters", ["ward_code"], schema=ANALYTICS)
    op.create_index("ix_analytics_hotspot_clusters_run_id", "hotspot_clusters", ["run_id"], schema=ANALYTICS)
    op.create_index("ix_analytics_hotspot_clusters_created_at", "hotspot_clusters", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_hotspot_clusters_is_synthetic", "hotspot_clusters", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "cluster_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_pseudonym", sa.String(48), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], [f"{ANALYTICS}.hotspot_clusters.id"], name="fk_cluster_members_cluster_id_hotspot_clusters", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_cluster_members"),
        sa.UniqueConstraint("cluster_id", "scan_id", name="uq_cluster_members_cluster_id"),
        schema=ANALYTICS,
    )
    op.create_index("ix_cluster_members_scan", "cluster_members", ["scan_id"], schema=ANALYTICS)
    op.create_index("ix_analytics_cluster_members_created_at", "cluster_members", ["created_at"], schema=ANALYTICS)

    op.create_table(
        "complaints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_code", sa.String(32), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reading_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("complainant_provided", sa.Boolean(), nullable=False),
        sa.Column("crop", sa.String(40), nullable=True),
        sa.Column("merchant_ref", sa.String(48), nullable=True),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("incident_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("package_object_key", sa.String(400), nullable=True),
        sa.Column("package_sha256", sa.String(64), nullable=True),
        sa.Column("submission_channel", sa.String(60), nullable=True),
        sa.Column("fssai_reference", sa.String(80), nullable=True),
        sa.Column("submitted_by_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("officer_notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "submitted_by_user_at IS NULL OR fssai_reference IS NOT NULL OR submission_channel IS NOT NULL",
            name="ck_complaints_user_submission_needs_channel_or_ref",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_complaints"),
        sa.UniqueConstraint("reference_code", name="uq_complaints_reference_code"),
        schema=ANALYTICS,
    )
    op.create_index("ix_complaints_status_created", "complaints", ["status", "created_at"], schema=ANALYTICS)
    op.create_index("ix_complaints_scan", "complaints", ["scan_id"], schema=ANALYTICS)
    op.create_index("ix_complaints_ward", "complaints", ["ward_code"], schema=ANALYTICS)
    op.create_index("ix_analytics_complaints_created_at", "complaints", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_complaints_is_synthetic", "complaints", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "merkle_roots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("root_hash", sa.String(64), nullable=False),
        sa.Column("leaf_count", sa.Integer(), nullable=False),
        sa.Column("algorithm", sa.String(24), nullable=False),
        sa.Column("covered_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("covered_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_anchor", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_merkle_roots"),
        sa.UniqueConstraint("period_date", name="uq_merkle_roots_period_date"),
        schema=ANALYTICS,
    )
    op.create_index("ix_analytics_merkle_roots_created_at", "merkle_roots", ["created_at"], schema=ANALYTICS)

    # --- Retail / Shelf / Direct -------------------------------------------
    op.create_table(
        "retail_outlets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outlet_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("address_line", sa.Text(), nullable=True),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("donation_partner", sa.String(160), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_retail_outlets"),
        sa.UniqueConstraint("outlet_code", name="uq_retail_outlets_outlet_code"),
        schema=ANALYTICS,
    )
    op.create_index("ix_analytics_retail_outlets_created_at", "retail_outlets", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_retail_outlets_is_synthetic", "retail_outlets", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "inventory_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outlet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sku", sa.String(48), nullable=False),
        sa.Column("crop", sa.String(40), nullable=False),
        sa.Column("cultivar", sa.String(60), nullable=True),
        sa.Column("quantity_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit_price_inr", sa.Numeric(10, 2), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_location", sa.String(80), nullable=True),
        sa.Column("ble_tag_ref", sa.String(64), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity_kg >= 0", name="ck_inventory_items_quantity_non_negative"),
        sa.ForeignKeyConstraint(["outlet_id"], [f"{ANALYTICS}.retail_outlets.id"], name="fk_inventory_items_outlet_id_retail_outlets", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lot_id"], [f"{ANALYTICS}.lots.id"], name="fk_inventory_items_lot_id_lots", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_items"),
        sa.UniqueConstraint("outlet_id", "sku", name="uq_inventory_items_outlet_id"),
        schema=ANALYTICS,
    )
    op.create_index("ix_inventory_items_outlet", "inventory_items", ["outlet_id"], schema=ANALYTICS)
    op.create_index("ix_inventory_items_lot", "inventory_items", ["lot_id"], schema=ANALYTICS)
    op.create_index("ix_analytics_inventory_items_created_at", "inventory_items", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_inventory_items_is_synthetic", "inventory_items", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "shelf_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ripeness_index", sa.Float(), nullable=True),
        sa.Column("freshness_score", sa.Float(), nullable=True),
        sa.Column("remaining_shelf_life_days", sa.Float(), nullable=True),
        sa.Column("confidence_low_days", sa.Float(), nullable=True),
        sa.Column("confidence_high_days", sa.Float(), nullable=True),
        sa.Column("mean_temperature_c", sa.Float(), nullable=True),
        sa.Column("accumulated_degree_hours", sa.Float(), nullable=True),
        sa.Column("temperature_source", sa.String(24), nullable=True),
        sa.Column("recommended_action", sa.String(24), nullable=False),
        sa.Column("suggested_markdown_pct", sa.Float(), nullable=True),
        sa.Column("rationale", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_id", sa.String(80), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("ripeness_index IS NULL OR (ripeness_index >= 0 AND ripeness_index <= 1)", name="ck_shelf_predictions_ripeness_index_range"),
        sa.ForeignKeyConstraint(["inventory_item_id"], [f"{ANALYTICS}.inventory_items.id"], name="fk_shelf_predictions_inventory_item_id_inventory_items", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_shelf_predictions"),
        schema=ANALYTICS,
    )
    op.create_index("ix_shelf_predictions_item_time", "shelf_predictions", ["inventory_item_id", "created_at"], schema=ANALYTICS)
    op.create_index("ix_shelf_predictions_action", "shelf_predictions", ["recommended_action"], schema=ANALYTICS)
    op.create_index("ix_analytics_shelf_predictions_created_at", "shelf_predictions", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_shelf_predictions_is_synthetic", "shelf_predictions", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "spoilage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outlet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inventory_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crop", sa.String(40), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("quantity_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.Column("value_inr", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["outlet_id"], [f"{ANALYTICS}.retail_outlets.id"], name="fk_spoilage_events_outlet_id_retail_outlets", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_spoilage_events"),
        schema=ANALYTICS,
    )
    op.create_index("ix_spoilage_events_outlet_date", "spoilage_events", ["outlet_id", "occurred_on"], schema=ANALYTICS)
    op.create_index("ix_analytics_spoilage_events_created_at", "spoilage_events", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_spoilage_events_is_synthetic", "spoilage_events", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "trust_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("season", sa.String(16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("scan_pass_component", sa.Float(), nullable=False),
        sa.Column("trace_completeness_component", sa.Float(), nullable=False),
        sa.Column("certification_component", sa.Float(), nullable=False),
        sa.Column("buyer_rating_component", sa.Float(), nullable=False),
        sa.Column("decay_factor", sa.Float(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_trust_scores_score_range"),
        sa.ForeignKeyConstraint(["farm_id"], [f"{ANALYTICS}.farms.id"], name="fk_trust_scores_farm_id_farms", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_trust_scores"),
        sa.UniqueConstraint("farm_id", "season", name="uq_trust_scores_farm_id"),
        schema=ANALYTICS,
    )
    op.create_index("ix_analytics_trust_scores_created_at", "trust_scores", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_trust_scores_is_synthetic", "trust_scores", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "marketplace_listings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("crop", sa.String(40), nullable=False),
        sa.Column("cultivar", sa.String(60), nullable=True),
        sa.Column("quantity_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("price_inr_per_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("available_from", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price_inr_per_kg > 0", name="ck_marketplace_listings_price_positive"),
        sa.ForeignKeyConstraint(["farm_id"], [f"{ANALYTICS}.farms.id"], name="fk_marketplace_listings_farm_id_farms", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lot_id"], [f"{ANALYTICS}.lots.id"], name="fk_marketplace_listings_lot_id_lots", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_marketplace_listings"),
        schema=ANALYTICS,
    )
    op.create_index("ix_marketplace_listings_crop_active", "marketplace_listings", ["crop", "is_active"], schema=ANALYTICS)
    op.create_index("ix_analytics_marketplace_listings_created_at", "marketplace_listings", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_marketplace_listings_is_synthetic", "marketplace_listings", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "buyer_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("farm_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_buyer_ratings_rating_range"),
        sa.ForeignKeyConstraint(["farm_id"], [f"{ANALYTICS}.farms.id"], name="fk_buyer_ratings_farm_id_farms", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_buyer_ratings"),
        schema=ANALYTICS,
    )
    op.create_index("ix_buyer_ratings_farm", "buyer_ratings", ["farm_id"], schema=ANALYTICS)
    op.create_index("ix_analytics_buyer_ratings_created_at", "buyer_ratings", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_buyer_ratings_is_synthetic", "buyer_ratings", ["is_synthetic"], schema=ANALYTICS)

    # --- Node --------------------------------------------------------------
    op.create_table(
        "device_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_uid", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("firmware_version", sa.String(32), nullable=True),
        sa.Column("hardware_revision", sa.String(32), nullable=True),
        sa.Column("market_name", sa.String(160), nullable=True),
        sa.Column("ward_code", sa.String(32), nullable=True),
        sa.Column("district", sa.String(80), nullable=True),
        sa.Column("state", sa.String(80), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("mq_r0_ohms", sa.Float(), nullable=True),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_device_records"),
        sa.UniqueConstraint("node_uid", name="uq_device_records_node_uid"),
        schema=ANALYTICS,
    )
    op.create_index("ix_device_records_market", "device_records", ["market_name"], schema=ANALYTICS)
    op.create_index("ix_analytics_device_records_created_at", "device_records", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_device_records_is_synthetic", "device_records", ["is_synthetic"], schema=ANALYTICS)

    op.create_table(
        "node_telemetry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sample_uid", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("mq_raw_adc", sa.Integer(), nullable=True),
        sa.Column("mq_rs_ohms", sa.Float(), nullable=True),
        sa.Column("mq_rs_r0_ratio", sa.Float(), nullable=True),
        sa.Column("advisory_flag", sa.Boolean(), nullable=False),
        sa.Column("advisory_reason", sa.String(80), nullable=True),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("humidity_pct IS NULL OR (humidity_pct >= 0 AND humidity_pct <= 100)", name="ck_node_telemetry_humidity_range"),
        sa.ForeignKeyConstraint(["device_id"], [f"{ANALYTICS}.device_records.id"], name="fk_node_telemetry_device_id_device_records", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_node_telemetry"),
        sa.UniqueConstraint("device_id", "sample_uid", name="uq_node_telemetry_device_id"),
        schema=ANALYTICS,
    )
    op.create_index("ix_node_telemetry_device_time", "node_telemetry", ["device_id", "recorded_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_node_telemetry_created_at", "node_telemetry", ["created_at"], schema=ANALYTICS)
    op.create_index("ix_analytics_node_telemetry_is_synthetic", "node_telemetry", ["is_synthetic"], schema=ANALYTICS)

    # ======================================================================
    # Protections that must live in the database
    # ======================================================================

    # 1. Append-only custody events.
    #
    # Tamper-evidence that depends on the application never issuing an UPDATE is
    # only as strong as the application. This trigger makes the guarantee hold
    # even against a direct psql session or a compromised service account.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {ANALYTICS}.reject_custody_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'analytics.custody_events is append-only: % is not permitted. '
                'Correct a mistaken record by appending a compensating event.',
                TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_custody_events_append_only
        BEFORE UPDATE OR DELETE ON {ANALYTICS}.custody_events
        FOR EACH ROW EXECUTE FUNCTION {ANALYTICS}.reject_custody_mutation();
        """
    )

    # 2. Identity-blind analytics role (non-negotiable rule 12).
    #
    # The clustering and heatmap workloads connect as this role. It has no grant
    # on the identity schema at all, so an accidental join to a PII table fails
    # with a permission error instead of quietly succeeding.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'satva_analytics') THEN
                CREATE ROLE satva_analytics LOGIN PASSWORD 'satva_analytics_password';
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA {ANALYTICS} TO satva_analytics")
    op.execute(f"GRANT USAGE ON SCHEMA public TO satva_analytics")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {ANALYTICS} "
        "TO satva_analytics"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {ANALYTICS} "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO satva_analytics"
    )
    # Explicit revoke, belt and braces: PUBLIC has USAGE on schemas by default
    # in some configurations, and this boundary is too important to leave to a
    # default.
    op.execute(f"REVOKE ALL ON SCHEMA {IDENTITY} FROM satva_analytics")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {IDENTITY} FROM satva_analytics")
    op.execute(f"REVOKE ALL ON SCHEMA {IDENTITY} FROM PUBLIC")


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_custody_events_append_only ON {ANALYTICS}.custody_events")
    op.execute(f"DROP FUNCTION IF EXISTS {ANALYTICS}.reject_custody_mutation()")

    for table in (
        "node_telemetry", "device_records", "buyer_ratings", "marketplace_listings",
        "trust_scores", "spoilage_events", "shelf_predictions", "inventory_items",
        "retail_outlets", "merkle_roots", "complaints", "cluster_members",
        "hotspot_clusters", "cluster_runs", "temperature_readings", "custody_events",
        "lots", "farms", "chemical_readings", "scan_images", "scans",
    ):
        op.drop_table(table, schema=ANALYTICS)

    for table in ("audit_log", "merchants", "otp_challenges", "scan_ownership", "devices", "users"):
        op.drop_table(table, schema=IDENTITY)

    op.execute(f"DROP SCHEMA IF EXISTS {ANALYTICS} CASCADE")
    op.execute(f"DROP SCHEMA IF EXISTS {IDENTITY} CASCADE")
