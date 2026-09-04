"""SATVA API application.

Scan · Analyse · Trace · Verify · Alert
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import (
    auth,
    colorimetry,
    complaints,
    devices,
    hotspots,
    marketplace,
    officers,
    retail,
    scans,
    trace,
)
from app.core.config import settings
from app.core.constants import DISCLAIMER_LONG, DISCLAIMER_SHORT
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import ping

configure_logging()
log = get_logger("satva.main")

DESCRIPTION = f"""
**SATVA** turns a smartphone into a food-safety screening instrument, and turns what it finds
into evidence a regulator can act on.

### The rule that shapes this API

> A photograph should never convict a vendor.

Screening happens in two layers, and the API keeps them strictly apart:

* **Layer A -- vision screening.** Runs entirely on the handset. Free, instant, offline. Its
  output is a 0-100 anomaly score whose only job is deciding whether a five-rupee chemical
  test is worth performing. It is **never** evidence. The produce photograph is not uploaded.
* **Layer B -- colorimetric confirmation.** A reagent strip photographed beside a printed
  reference card, read quantitatively in CIE L\\*a\\*b\\* space. This is the **only** result
  SATVA will publish, cluster, or attach to a complaint.

Attempting to use a Layer A result as evidence returns `409 evidence_rule_violation`.

### What the API refuses to do

* It **refuses to measure** when capture conditions are unusable, rather than returning a
  low-confidence number.
* It **refuses to score** crops outside its validated set, rather than guessing.
* It **never exposes a vendor identity** on a public surface. Ward-level aggregates only;
  vendor detail requires an authenticated Food Safety Officer and is written to an audit log.
* It **never submits a complaint**. SATVA prepares an evidence package; the citizen files it
  through the statutory FSSAI channel.

{DISCLAIMER_LONG}
"""

TAGS_METADATA = [
    {
        "name": "auth",
        "description": "Phone OTP for consumers; password sign-in for dashboard roles.",
    },
    {
        "name": "scans",
        "description": "Layer A screening records, offline sync, and evidence state.",
    },
    {"name": "colorimetry", "description": "Layer B. The only source of evidence-grade results."},
    {"name": "trace", "description": "Tamper-evident provenance. Optional; never blocks Scan."},
    {
        "name": "watch",
        "description": "Public ward-level hotspot map. No vendor is ever named here.",
    },
    {"name": "officers", "description": "Authenticated Food Safety Officer surface. Audited."},
    {"name": "complaints", "description": "FSSAI evidence packages. Prepared, never submitted."},
    {"name": "retail", "description": "SATVA Shelf: freshness and shelf-life intelligence."},
    {"name": "marketplace", "description": "SATVA Direct: verified farm-to-consumer listings."},
    {"name": "devices", "description": "SATVA Node field hardware. Advisory signal only."},
    {"name": "system", "description": "Health and metadata."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "satva_starting",
        env=settings.env,
        version=settings.version,
        database_reachable=ping(),
    )
    if settings.env == "production" and settings.is_dev_secret:
        raise RuntimeError(
            "Refusing to start in production with the development SECRET_KEY. "
            "Set SATVA_SECRET_KEY to a real value."
        )
    if settings.env == "production" and settings.is_dev_pepper:
        raise RuntimeError(
            "Refusing to start in production with the development device hash pepper. "
            "Every device pseudonym would be reversible by anyone with the source. "
            "Set SATVA_DEVICE_HASH_PEPPER to a real value."
        )
    if settings.env != "production":
        # Best-effort: the API is still useful without object storage, and a
        # developer without MinIO running should get a warning, not a crash.
        try:
            from app.services.storage import object_store

            object_store.ensure_bucket()
        except Exception as exc:  # noqa: BLE001
            log.warning("object_storage_unavailable", error=str(exc)[:200])
    yield
    log.info("satva_stopping")


app = FastAPI(
    title="SATVA API",
    version=settings.version,
    description=DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={"name": "Team Real Fighters", "url": "https://github.com/"},
    license_info={"name": "MIT"},
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    # Explicit origins rather than "*": credentials are sent on dashboard
    # requests, and a wildcard origin with credentials is both forbidden by the
    # spec and a real risk.
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
    ]
    if settings.env == "development"
    else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

register_exception_handlers(app)

system_router = APIRouter(tags=["system"])


@system_router.get("/health", summary="Liveness and dependency check")
def health() -> dict:
    return {
        "status": "ok",
        "version": settings.version,
        "environment": settings.env,
        "database": "up" if ping() else "down",
    }


@system_router.get("/meta", summary="Product metadata and standing disclaimers")
def meta() -> dict:
    from app.core.constants import SUPPORTED_CROPS, UNVALIDATED_CROPS

    return {
        "name": "SATVA",
        "tagline": "Scan · Analyse · Trace · Verify · Alert",
        "version": settings.version,
        "modules": {
            "scan": "implemented",
            "watch": "implemented",
            "trace": "implemented",
            "shelf": "implemented",
            "direct": "scaffolded",
            "node": "scaffolded (ingestion real, sensor science unvalidated)",
        },
        "disclaimer": {"short": DISCLAIMER_SHORT, "long": DISCLAIMER_LONG},
        "evidence_rule": (
            "Only a confirmatory colorimetric reading may be published, escalated, or attached "
            "to a complaint. A visual screening score is advisory and is never evidence."
        ),
        "supported_crops": sorted(SUPPORTED_CROPS),
        "refused_crops": sorted(UNVALIDATED_CROPS),
        "escalation_channel": settings.fssai_connect_app,
        "consumer_screening_cost": "free",
    }


app.include_router(system_router, prefix=settings.api_prefix)
for module in (
    auth,
    scans,
    colorimetry,
    trace,
    hotspots,
    officers,
    complaints,
    retail,
    marketplace,
    devices,
):
    app.include_router(module.router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": "SATVA API",
        "tagline": "Scan · Analyse · Trace · Verify · Alert",
        "docs": "/docs",
        "health": f"{settings.api_prefix}/health",
    }
