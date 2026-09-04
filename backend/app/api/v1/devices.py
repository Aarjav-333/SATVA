"""/devices -- SATVA Node field-hardware ingestion.

Honest scope
------------
The ingestion path is real: an ESP32 publishes to MQTT, the bridge in
`app/workers/mqtt_bridge.py` forwards each message here, and readings are
persisted. Firmware that produces these payloads is in `hardware/firmware/`.

What is **not** claimed: an MQ-series gas sensor is a broad reducing-gas
detector, not a selective acetylene assay. Humidity and temperature shift its
baseline, and many ordinary market volatiles will move it. Node telemetry is
therefore recorded as an **advisory** signal that can prompt a confirmatory
strip test on a crate. It is never evidence, and `app.services.evidence` will
not promote a scan on node data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, require_roles
from app.core.config import settings
from app.core.constants import Role
from app.core.errors import AuthenticationError, NotFoundError
from app.core.security import constant_time_compare
from app.db.session import get_db
from app.models.node import DeviceRecord, NodeTelemetry
from app.schemas.retail import (
    NodeOut,
    NodeRegisterRequest,
    NodeTelemetryIn,
    NodeTelemetryOut,
)

router = APIRouter(prefix="/devices", tags=["devices"])

EVIDENCE_NOTE = (
    "Advisory signal only. An MQ-series gas sensor responds to a broad class of reducing "
    "gases, not specifically to acetylene, and its baseline shifts with temperature and "
    "humidity. A raised reading is a reason to perform a confirmatory strip test on the "
    "crate; it is never treated as a chemical confirmation and cannot promote a scan to "
    "evidence grade."
)

# Rs/R0 above this is unusual enough to be worth a strip test. An engineering
# threshold from the sensor's datasheet response curves, not a calibrated
# detection limit for any specific gas.
ADVISORY_RATIO_THRESHOLD = 2.5


def _require_node_token(x_satva_node_token: str | None) -> None:
    """Shared-secret auth for the MQTT bridge.

    A shared secret is adequate for a bridge running inside the deployment's own
    network. Per-device credentials with mutual TLS would be required before
    exposing this to nodes on untrusted networks; that is noted in
    docs/known_limitations.md.
    """
    if not x_satva_node_token or not constant_time_compare(
        x_satva_node_token, settings.node_ingest_token
    ):
        raise AuthenticationError("A valid node ingestion token is required.")


@router.post(
    "/register",
    response_model=NodeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a SATVA Node unit",
)
def register_node(
    payload: NodeRegisterRequest,
    principal: Principal = Depends(require_roles(Role.OFFICER, Role.RETAILER)),
    db: Session = Depends(get_db),
) -> NodeOut:
    device = db.scalar(select(DeviceRecord).where(DeviceRecord.node_uid == payload.node_uid))
    if device is None:
        device = DeviceRecord(node_uid=payload.node_uid)
        db.add(device)

    for field_name in (
        "label", "firmware_version", "hardware_revision", "market_name", "ward_code",
        "district", "state", "latitude", "longitude", "mq_r0_ohms", "is_simulated",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            setattr(device, field_name, value)
    if payload.mq_r0_ohms:
        device.calibrated_at = datetime.now(UTC)

    db.commit()
    db.refresh(device)
    return NodeOut.model_validate(device)


@router.post(
    "/telemetry",
    response_model=NodeTelemetryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest one node telemetry sample",
    description=(
        "Called by the MQTT bridge. Authenticated with a shared node token rather than a user "
        "session. Idempotent on (node_uid, sample_uid) so an at-least-once MQTT delivery does "
        "not double-count a reading."
    ),
)
def ingest_telemetry(
    payload: NodeTelemetryIn,
    x_satva_node_token: str | None = Header(default=None, alias="X-SATVA-Node-Token"),
    db: Session = Depends(get_db),
) -> NodeTelemetryOut:
    _require_node_token(x_satva_node_token)

    device = db.scalar(select(DeviceRecord).where(DeviceRecord.node_uid == payload.node_uid))
    if device is None:
        raise NotFoundError(f"Node '{payload.node_uid}' is not registered.")

    existing = db.scalar(
        select(NodeTelemetry).where(
            NodeTelemetry.device_id == device.id,
            NodeTelemetry.sample_uid == payload.sample_uid,
        )
    )
    if existing is not None:
        return _telemetry_out(device, existing)

    ratio = None
    if payload.mq_rs_ohms and device.mq_r0_ohms:
        ratio = round(payload.mq_rs_ohms / device.mq_r0_ohms, 4)

    advisory = False
    reason = None
    if ratio is not None and ratio >= ADVISORY_RATIO_THRESHOLD:
        advisory = True
        reason = "mq_ratio_above_advisory_threshold"
    elif ratio is None and payload.mq_raw_adc is not None:
        reason = "no_r0_baseline_recorded_for_this_node"

    reading = NodeTelemetry(
        device_id=device.id,
        sample_uid=payload.sample_uid,
        recorded_at=payload.recorded_at or datetime.now(UTC),
        temperature_c=payload.temperature_c,
        humidity_pct=payload.humidity_pct,
        mq_raw_adc=payload.mq_raw_adc,
        mq_rs_ohms=payload.mq_rs_ohms,
        mq_rs_r0_ratio=ratio,
        advisory_flag=advisory,
        advisory_reason=reason,
        lot_id=payload.lot_id,
        raw_payload=payload.model_dump(mode="json"),
        is_synthetic=device.is_simulated,
    )
    db.add(reading)
    device.last_seen_at = datetime.now(UTC)
    if payload.firmware_version:
        device.firmware_version = payload.firmware_version
    db.commit()
    db.refresh(reading)
    return _telemetry_out(device, reading)


def _telemetry_out(device: DeviceRecord, reading: NodeTelemetry) -> NodeTelemetryOut:
    return NodeTelemetryOut(
        id=reading.id,
        node_uid=device.node_uid,
        sample_uid=reading.sample_uid,
        recorded_at=reading.recorded_at,
        temperature_c=reading.temperature_c,
        humidity_pct=reading.humidity_pct,
        mq_rs_r0_ratio=reading.mq_rs_r0_ratio,
        advisory_flag=reading.advisory_flag,
        advisory_reason=reading.advisory_reason,
        is_synthetic=reading.is_synthetic,
        evidence_note=EVIDENCE_NOTE,
    )


@router.get("", response_model=list[NodeOut], summary="Registered nodes")
def list_nodes(
    principal: Principal = Depends(require_roles(Role.OFFICER, Role.RETAILER)),
    db: Session = Depends(get_db),
) -> list[NodeOut]:
    since = datetime.now(UTC) - timedelta(days=7)
    results = []
    for device in db.scalars(select(DeviceRecord).order_by(DeviceRecord.node_uid)):
        advisories = db.scalar(
            select(func.count())
            .select_from(NodeTelemetry)
            .where(
                NodeTelemetry.device_id == device.id,
                NodeTelemetry.advisory_flag.is_(True),
                NodeTelemetry.recorded_at >= since,
            )
        )
        out = NodeOut.model_validate(device)
        out.recent_advisory_count = int(advisories or 0)
        results.append(out)
    return results


@router.get(
    "/{node_uid}/telemetry",
    response_model=list[NodeTelemetryOut],
    summary="Recent telemetry for a node",
)
def node_telemetry(
    node_uid: str,
    limit: int = Query(100, ge=1, le=1000),
    principal: Principal = Depends(require_roles(Role.OFFICER, Role.RETAILER)),
    db: Session = Depends(get_db),
) -> list[NodeTelemetryOut]:
    device = db.scalar(select(DeviceRecord).where(DeviceRecord.node_uid == node_uid))
    if device is None:
        raise NotFoundError("No such node.")
    readings = db.scalars(
        select(NodeTelemetry)
        .where(NodeTelemetry.device_id == device.id)
        .order_by(NodeTelemetry.recorded_at.desc())
        .limit(limit)
    )
    return [_telemetry_out(device, r) for r in readings]
