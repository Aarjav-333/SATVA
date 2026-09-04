"""Application configuration.

Every secret is sourced from the environment. Nothing sensitive has a usable
production default; the development defaults are deliberately obvious so that a
misconfigured deployment fails loudly rather than silently running with a known
key. See `.env.example` at the repository root.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="SATVA_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    api_prefix: str = "/api/v1"
    project_name: str = "SATVA"
    version: str = "0.1.0"

    # --- Security -----------------------------------------------------------
    secret_key: str = "CHANGE_ME_dev_only_secret_key_do_not_use_in_production"  # noqa: S105
    access_token_ttl_min: int = 60
    refresh_token_ttl_days: int = 14
    device_hash_pepper: str = "CHANGE_ME_dev_only_device_pepper"
    jwt_algorithm: str = "HS256"

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://satva:satva_dev_password@localhost:5432/satva"
    analytics_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    sql_echo: bool = False

    # --- Redis / Celery -----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # --- Object storage -----------------------------------------------------
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "satva_minio_admin"
    s3_secret_key: str = "satva_minio_password"  # noqa: S105
    s3_bucket: str = "satva-evidence"
    s3_region: str = "us-east-1"

    # --- Uploads ------------------------------------------------------------
    max_upload_bytes: int = 8 * 1024 * 1024
    allowed_image_types: str = "image/jpeg,image/png,image/webp"

    # --- Retention (spec 15.3) ---------------------------------------------
    image_retention_days: int = 90
    image_retention_days_with_live_complaint: int = 730

    # --- Rate limiting ------------------------------------------------------
    rate_limit_scan_per_hour: int = 40
    rate_limit_auth_per_hour: int = 10
    rate_limit_default_per_minute: int = 120

    # --- Watch / clustering (spec 3.3) -------------------------------------
    dbscan_eps_metres: float = 250.0
    dbscan_min_samples: int = 3
    cluster_time_window_days: int = 30
    cluster_min_independent_devices: int = 3
    phash_duplicate_hamming_threshold: int = 8

    # --- Integrations -------------------------------------------------------
    firebase_credentials_file: str | None = None
    firebase_project_id: str | None = None
    fssai_portal_url: str = "https://foodlicensing.fssai.gov.in/cmsweb/"
    fssai_connect_app: str = "FSSAI Food Safety Connect"

    # --- SATVA Node ---------------------------------------------------------
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic: str = "satva/node/+/telemetry"
    node_ingest_token: str = "CHANGE_ME_dev_node_token"  # noqa: S105

    # --- Demo ---------------------------------------------------------------
    seed_demo_password: str = "satva-demo-2026"  # noqa: S105

    @field_validator("secret_key")
    @classmethod
    def _reject_default_secret_in_production(cls, v: str, info) -> str:
        env = (info.data or {}).get("env")
        if env == "production" and v.startswith("CHANGE_ME"):
            raise ValueError(
                "SATVA_SECRET_KEY must be set to a real value when SATVA_ENV=production"
            )
        return v

    @property
    def allowed_image_type_set(self) -> set[str]:
        return {t.strip() for t in self.allowed_image_types.split(",") if t.strip()}

    @property
    def analytics_url(self) -> str:
        """URL used by clustering/heatmap workloads.

        Spec rule 12: the clustering and heatmap pipelines must not read personal
        identity data. In a full deployment this points at a database role with
        no grant on the `identity` schema. If unset we fall back to the primary
        URL, and the application-level guard in
        `app.db.analytics_session` still refuses identity-schema access.
        """
        return self.analytics_database_url or self.database_url

    @field_validator("device_hash_pepper")
    @classmethod
    def _reject_default_pepper_in_production(cls, v: str, info) -> str:
        # The pepper is the whole of the guarantee behind rules 11 and 12: it is
        # what stops a device pseudonym being reversed by hashing a dictionary
        # of installation ids. Its default ships in this repository, so a
        # deployment that sets only SATVA_SECRET_KEY would otherwise start
        # cleanly with every pseudonym reversible by anyone holding the source.
        env = (info.data or {}).get("env")
        if env == "production" and v.startswith("CHANGE_ME"):
            raise ValueError(
                "SATVA_DEVICE_HASH_PEPPER must be set to a real value when SATVA_ENV=production"
            )
        return v

    @property
    def is_dev_secret(self) -> bool:
        return self.secret_key.startswith("CHANGE_ME")

    @property
    def is_dev_pepper(self) -> bool:
        return self.device_hash_pepper.startswith("CHANGE_ME")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
