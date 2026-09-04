"""Simulate a SATVA Node so the hardware path can be demonstrated without hardware.

    python scripts/simulate_node.py --node-uid node-demo-001 --mqtt
    python scripts/simulate_node.py --node-uid node-demo-001 --http --samples 30

Two modes:

* ``--mqtt`` publishes to the broker exactly as the ESP32 firmware does, so the
  whole path (broker → bridge → API → database) is exercised end to end.
* ``--http`` posts straight to the ingest endpoint, for when no broker is
  running.

The generated signal is **simulated**, and it is labelled as such at every step:
the device is registered with `is_simulated=true`, and every row it produces
carries `is_synthetic=true`. Nothing here should ever be presented as a real
sensor reading.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

DEFAULT_API = "http://localhost:8000/api/v1"


def build_sample(
    node_uid: str,
    index: int,
    *,
    r0_ohms: float,
    elevated: bool,
    rng: random.Random,
) -> dict:
    """One plausible telemetry sample.

    The diurnal temperature cycle and correlated humidity are there so the
    demo looks like a market rather than a random-number generator, and so the
    shelf-life model downstream receives a realistic thermal history.
    """
    hour = (index % 24) + rng.uniform(-0.3, 0.3)
    temperature = 27.0 + 5.0 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.uniform(-0.8, 0.8)
    humidity = 72.0 - 0.9 * (temperature - 27.0) + rng.uniform(-4, 4)

    ratio = rng.uniform(2.9, 3.8) if elevated else rng.uniform(0.75, 1.85)
    rs = r0_ohms * ratio

    # Recover a plausible raw ADC value from Rs, so the field is consistent
    # with mq_rs_ohms rather than an independent random number.
    load = 10_000.0
    voltage = 3.3 * load / (rs + load)
    raw_adc = int(max(0, min(4095, voltage / 3.3 * 4095)))

    return {
        "node_uid": node_uid,
        "sample_uid": f"{node_uid}-{index:05d}",
        "recorded_at": datetime.now(UTC).isoformat(),
        "firmware_version": "0.1.0-sim",
        "temperature_c": round(temperature, 1),
        "humidity_pct": round(max(0.0, min(100.0, humidity)), 1),
        "mq_raw_adc": raw_adc,
        "mq_rs_ohms": round(rs, 1),
    }


def post_http(api_base: str, token: str, sample: dict) -> None:
    request = urllib.request.Request(
        f"{api_base}/devices/telemetry",
        data=json.dumps(sample).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-SATVA-Node-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
            flag = " ADVISORY" if body.get("advisory_flag") else ""
            print(
                f"  {sample['sample_uid']}  "
                f"{sample['temperature_c']}C {sample['humidity_pct']}%  "
                f"Rs/R0={body.get('mq_rs_r0_ratio')}{flag}"
            )
    except urllib.error.HTTPError as exc:
        print(f"  {sample['sample_uid']}  HTTP {exc.code}: {exc.read()[:160].decode()}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {sample['sample_uid']}  failed: {exc}")


def publish_mqtt(host: str, port: int, sample: dict) -> None:
    import paho.mqtt.publish as publish

    publish.single(
        f"satva/node/{sample['node_uid']}/telemetry",
        payload=json.dumps(sample),
        qos=1,
        hostname=host,
        port=port,
    )
    print(f"  published {sample['sample_uid']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate a SATVA Node.")
    parser.add_argument("--node-uid", default="node-demo-001")
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--interval", type=float, default=0.4, help="seconds between samples")
    parser.add_argument("--r0", type=float, default=11000.0, help="clean-air baseline, ohms")
    parser.add_argument(
        "--elevated-from",
        type=int,
        default=10,
        help="index at which the simulated gas reading rises",
    )
    parser.add_argument(
        "--elevated-until",
        type=int,
        default=16,
        help="index at which it returns to baseline",
    )
    parser.add_argument("--mqtt", action="store_true", help="publish to the broker")
    parser.add_argument("--http", action="store_true", help="post directly to the API")
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--api-base", default=DEFAULT_API)
    parser.add_argument("--token", default="CHANGE_ME_dev_node_token")
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()

    if not args.mqtt and not args.http:
        args.http = True

    rng = random.Random(args.seed)

    print(f"\nSimulating {args.node_uid} — {args.samples} samples")
    print("SIMULATED DATA. Rows are stored with is_synthetic=true and are not")
    print("sensor readings from any physical device.\n")

    for index in range(args.samples):
        elevated = args.elevated_from <= index < args.elevated_until
        sample = build_sample(args.node_uid, index, r0_ohms=args.r0, elevated=elevated, rng=rng)

        if args.mqtt:
            publish_mqtt(args.mqtt_host, args.mqtt_port, sample)
        if args.http:
            post_http(args.api_base, args.token, sample)

        if index < args.samples - 1:
            time.sleep(args.interval)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
