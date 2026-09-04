"""MQTT-to-API bridge for SATVA Node.

    python -m app.workers.mqtt_bridge

Subscribes to `satva/node/+/telemetry` and forwards each message to
`POST /devices/telemetry`.

Why a bridge rather than writing to the database directly
---------------------------------------------------------
Everything that enters SATVA goes through the same validation, the same
idempotency check and the same audit path. A worker with its own database
session would be a second, unvalidated way in, and the first schema change would
break it silently. The extra hop costs a few milliseconds on a once-a-minute
sample.

Delivery
--------
MQTT QoS 1 is at-least-once, so redelivery is normal. `sample_uid` makes the
ingest endpoint idempotent, which is what makes redelivery harmless.
"""

from __future__ import annotations

import json
import signal
import sys
import time
from typing import Any

import paho.mqtt.client as mqtt
import urllib3

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
log = get_logger("satva.mqtt_bridge")

API_BASE = settings.__dict__.get("api_base") or "http://api:8000/api/v1"
INGEST_PATH = "/devices/telemetry"

_http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=3.0, read=8.0), retries=False)
_running = True


def _forward(payload: dict[str, Any]) -> None:
    """POST one sample to the API."""
    try:
        response = _http.request(
            "POST",
            f"{API_BASE}{INGEST_PATH}",
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-SATVA-Node-Token": settings.node_ingest_token,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("forward_failed", error=str(exc)[:200], node=payload.get("node_uid"))
        return

    if response.status in (200, 201):
        log.info(
            "sample_forwarded",
            node=payload.get("node_uid"),
            sample=payload.get("sample_uid"),
        )
    elif response.status == 404:
        # An unregistered node. Logged rather than retried: registering a device
        # is a deliberate human act, and silently creating one on first sight
        # would let anything on the network inject readings.
        log.warning(
            "node_not_registered",
            node=payload.get("node_uid"),
            hint="register it via POST /devices/register before it can report",
        )
    else:
        log.warning(
            "forward_rejected",
            status=response.status,
            body=response.data[:200].decode("utf-8", errors="replace"),
        )


def _on_connect(client: mqtt.Client, _userdata, _flags, reason_code, _properties=None) -> None:
    if reason_code == 0:
        client.subscribe(settings.mqtt_topic, qos=1)
        log.info("mqtt_connected", topic=settings.mqtt_topic)
    else:
        log.error("mqtt_connect_failed", reason_code=str(reason_code))


def _on_message(_client: mqtt.Client, _userdata, message: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("bad_payload", topic=message.topic, error=str(exc)[:120])
        return

    if not isinstance(payload, dict) or "node_uid" not in payload:
        log.warning("payload_missing_node_uid", topic=message.topic)
        return

    # The topic carries the node id too. If they disagree, something is
    # publishing on someone else's topic; drop it rather than guess which is
    # authoritative.
    parts = message.topic.split("/")
    if len(parts) >= 3 and parts[2] != payload["node_uid"]:
        log.warning(
            "topic_payload_mismatch",
            topic_node=parts[2],
            payload_node=payload["node_uid"],
        )
        return

    _forward(payload)


def _on_disconnect(_client, _userdata, reason_code, _properties=None, _packet=None) -> None:
    log.warning("mqtt_disconnected", reason_code=str(reason_code))


def main() -> int:
    def _stop(_signum, _frame):
        global _running
        _running = False
        log.info("bridge_stopping")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="satva-bridge")
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect

    log.info("bridge_starting", host=settings.mqtt_host, port=settings.mqtt_port)

    while _running:
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
            client.loop_forever(retry_first_connection=False)
        except Exception as exc:  # noqa: BLE001
            if not _running:
                break
            log.warning("mqtt_loop_error", error=str(exc)[:200])
            time.sleep(5)

    client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
