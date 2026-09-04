"""Seed realistic demonstration data for SATVA.

    cd backend && python -m scripts.seed_demo          # seed
    cd backend && python -m scripts.seed_demo --reset  # wipe demo data first

Honesty rule
------------
**Every row this script creates has `is_synthetic = True`.** The API echoes that
flag, both dashboards render a visible "demo data" badge, and the officer
worklist marks affected clusters. Nothing seeded here may be mistaken for
collected evidence -- the specification is explicit that presentation figures
must not be passed off as real findings, and this flag is how that promise is
kept mechanically rather than by convention.

What is generated
-----------------
The Palakkad/Thrissur setting from the specification, with:

* one genuine hotspot in Palakkad Municipal Market -- enough independent devices
  to publish, which is what the demo heatmap shows;
* one *suppressed* cluster where a single device submitted many readings. This
  one matters as much as the visible hotspot: it is the demonstration that a
  competitor cannot manufacture a case;
* clean readings elsewhere, so the map is not uniformly alarming;
* full custody chains, split lots, retail inventory with shelf predictions,
  marketplace listings and trust scores.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.constants import (  # noqa: E402
    ComplaintStatus,
    CustodyEventType,
    EvidenceGrade,
    ReagentAssay,
    RipeningMethod,
    Role,
    ScreeningVerdict,
)
from app.core.security import hash_password, pseudonymise_device  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.models.identity import Device, Merchant, ScanOwnership, User  # noqa: E402
from app.models.node import DeviceRecord, NodeTelemetry  # noqa: E402
from app.models.retail import (  # noqa: E402
    BuyerRating,
    InventoryItem,
    MarketplaceListing,
    RetailOutlet,
    ShelfPrediction,
    SpoilageEvent,
)
from app.models.scan import ChemicalReading, Scan  # noqa: E402
from app.models.trace import CustodyEvent, Farm, ProduceLot, TemperatureTagReading  # noqa: E402
from app.models.watch import Complaint  # noqa: E402
from app.services.colorimetry.calibration import TURMERIC_CARBIDE  # noqa: E402
from app.services.direct.trust import compute_trust_score, current_season  # noqa: E402
from app.services.shelf.predictor import predict  # noqa: E402
from app.services.trace.service import TraceService  # noqa: E402
from app.services.watch.wards import WARD_TABLE  # noqa: E402

# Fixed seed: the demo must look the same in rehearsal and on stage.
RNG = random.Random(20260904)
NP_RNG = np.random.default_rng(20260904)
NOW = datetime.now(UTC)
EMBEDDING_DIM = 576

DEMO_PASSWORD = settings.seed_demo_password


def ward(code: str) -> dict:
    return next(w for w in WARD_TABLE if w["ward_code"] == code)


def jitter(lat: float, lon: float, metres: float) -> tuple[float, float]:
    import math

    bearing = RNG.uniform(0, 2 * math.pi)
    distance = RNG.uniform(0, metres)
    d_lat = (distance * math.cos(bearing)) / 111_320.0
    d_lon = (distance * math.sin(bearing)) / (111_320.0 * math.cos(math.radians(lat)))
    return lat + d_lat, lon + d_lon


def fake_embedding(seed_text: str) -> list[float]:
    """Deterministic pseudo-embedding.

    NOT a model output. Seeded from the scan's own identifier so that duplicate
    detection has something stable to work with in the demo, and so re-running
    the seeder reproduces the same vectors.
    """
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    vector = rng.normal(size=EMBEDDING_DIM)
    return (vector / np.linalg.norm(vector)).round(6).tolist()


def fake_phash(seed_text: str) -> str:
    return hashlib.sha256(seed_text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
def reset(db) -> None:
    """Remove only synthetic rows. Real data, if any, is left untouched."""
    print("  resetting demo data (synthetic rows only)...")

    # The append-only trigger has to be lifted to remove seeded custody events.
    # This is the one legitimate use, and it is scoped to synthetic rows.
    db.execute(
        text("ALTER TABLE analytics.custody_events DISABLE TRIGGER trg_custody_events_append_only")
    )
    db.execute(delete(CustodyEvent).where(CustodyEvent.is_synthetic.is_(True)))
    db.execute(
        text("ALTER TABLE analytics.custody_events ENABLE TRIGGER trg_custody_events_append_only")
    )

    for model in (
        NodeTelemetry, DeviceRecord, Complaint, ShelfPrediction, SpoilageEvent,
        InventoryItem, RetailOutlet, BuyerRating, MarketplaceListing,
        TemperatureTagReading, ChemicalReading,
    ):
        db.execute(delete(model).where(model.is_synthetic.is_(True)))

    synthetic_scans = select(Scan.id).where(Scan.is_synthetic.is_(True))
    db.execute(delete(ScanOwnership).where(ScanOwnership.scan_id.in_(synthetic_scans)))
    db.execute(delete(Scan).where(Scan.is_synthetic.is_(True)))

    db.execute(text("DELETE FROM analytics.cluster_members"))
    db.execute(text("DELETE FROM analytics.hotspot_clusters"))
    db.execute(text("DELETE FROM analytics.cluster_runs"))
    db.execute(text("DELETE FROM analytics.trust_scores"))

    db.execute(delete(ProduceLot).where(ProduceLot.is_synthetic.is_(True)))
    db.execute(delete(Farm).where(Farm.is_synthetic.is_(True)))
    db.execute(delete(Merchant).where(Merchant.is_synthetic.is_(True)))

    demo_emails = [
        "officer@satva.demo", "retailer@satva.demo", "farmer@satva.demo", "admin@satva.demo",
    ]
    demo_users = select(User.id).where(User.email.in_(demo_emails))
    db.execute(delete(ScanOwnership).where(ScanOwnership.user_id.in_(demo_users)))
    db.execute(delete(Device).where(Device.user_id.in_(demo_users)))

    # Devices outlive their users: the foreign key is ON DELETE SET NULL, so
    # deleting demo accounts orphans the handsets rather than removing them, and
    # the next seed collides on the unique device_pseudonym. The demo pseudonyms
    # are deterministic, so they can be named exactly.
    demo_pseudonyms = [
        pseudonymise_device(f"satva-demo-handset-{i:03d}") for i in range(24)
    ]
    db.execute(delete(Device).where(Device.device_pseudonym.in_(demo_pseudonyms)))
    db.execute(delete(User).where(User.email.in_(demo_emails)))
    db.execute(delete(User).where(User.phone_number.like("+9199000%")))
    db.flush()


def seed_users(db) -> dict:
    print("  users and demo accounts...")
    accounts = {}
    staff = [
        ("officer@satva.demo", Role.OFFICER, "K. Ramesh (demo)",
         {"officer_designation": "Food Safety Officer", "officer_jurisdiction": "Palakkad"}),
        ("retailer@satva.demo", Role.RETAILER, "Green Basket Stores (demo)", {}),
        ("farmer@satva.demo", Role.FARMER, "Attappady Organic FPO (demo)", {}),
        ("admin@satva.demo", Role.ADMIN, "SATVA Administrator (demo)", {}),
    ]
    for email, role, name, extra in staff:
        user = User(
            email=email,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            display_name=name,
            is_active=True,
            **extra,
        )
        db.add(user)
        accounts[role.value] = user

    # Consumer handsets. Each is a distinct device pseudonym, which is what
    # makes independent-device corroboration meaningful in the seeded clusters.
    consumers = []
    for i in range(14):
        user = User(
            phone_number=f"+91990000{i:04d}",
            role=Role.CONSUMER,
            phone_verified=True,
            is_active=True,
            display_name=None,
        )
        db.add(user)
        db.flush()
        device = Device(
            user_id=user.id,
            device_pseudonym=pseudonymise_device(f"satva-demo-handset-{i:03d}"),
            platform="android",
            app_version="0.1.0",
            model_name=RNG.choice(["Redmi 12C", "Realme C53", "Samsung M04", "Moto G14"]),
            attestation_state="unverified",
        )
        db.add(device)
        consumers.append((user, device))

    db.flush()
    accounts["consumers"] = consumers
    return accounts


def seed_merchants(db) -> list[Merchant]:
    print("  merchants (identity schema, officer-only)...")
    specs = [
        ("PKD-14", "Stall 12, Palakkad Municipal Market", "Palakkad Municipal Market", "S-12"),
        ("PKD-14", "Stall 27, Palakkad Municipal Market", "Palakkad Municipal Market", "S-27"),
        ("PKD-14", "Fruit Corner, Market Road", "Palakkad Municipal Market", "S-31"),
        ("PKD-22", "Kalpathy Fruit Mandi", "Kalpathy Market", "K-04"),
        ("PKD-31", "Chittur Roadside Vendor", "Chittur Bazaar", "C-09"),
        ("TSR-09", "Shakthan Wholesale Stall 4", "Shakthan Thampuran Market", "ST-04"),
        ("TSR-09", "Shakthan Wholesale Stall 9", "Shakthan Thampuran Market", "ST-09"),
        ("TSR-02", "Round South Fruit Shop", "Thrissur Round", "R-15"),
    ]
    merchants = []
    for index, (ward_code, name, market, stall) in enumerate(specs):
        w = ward(ward_code)
        lat, lon = jitter(w["lat"], w["lon"], 220)
        merchant = Merchant(
            merchant_ref=f"mch_demo_{index:03d}",
            name=name,
            stall_identifier=stall,
            market_name=market,
            address_line=f"{market}, {w['district']}, Kerala",
            ward_code=ward_code,
            district=w["district"],
            state="Kerala",
            fssai_licence_no=f"1{RNG.randint(10**10, 10**11 - 1)}",
            latitude=lat,
            longitude=lon,
            is_synthetic=True,
        )
        db.add(merchant)
        merchants.append(merchant)
    db.flush()
    return merchants


def seed_farms(db) -> list[Farm]:
    print("  farms and FPOs...")
    specs = [
        ("Attappady Organic FPO", "fpo", "Agali", "Palakkad", 11.1780, 76.6400,
         ["india_organic", "pgs_india", "fpo_member"], 340),
        ("Nelliyampathy Growers Collective", "fpo", "Nelliyampathy", "Palakkad", 10.5340, 76.6890,
         ["npop", "fpo_member"], 180),
        ("Kollengode Mango Farm", "farmer", "Kollengode", "Palakkad", 10.6100, 76.6900,
         ["fssai_registered"], None),
        ("Chalakudy Banana Growers", "fpo", "Chalakudy", "Thrissur", 10.3070, 76.3350,
         ["pgs_india", "fpo_member"], 220),
        ("Vadakkancherry Farm Trust", "farmer", "Vadakkancherry", "Palakkad", 10.6000, 76.4500,
         [], None),
    ]
    farms = []
    for index, (name, kind, village, district, lat, lon, certs, members) in enumerate(specs):
        farm = Farm(
            farm_code=f"FRM-DEMO{index:02d}",
            name=name,
            entity_type=kind,
            village=village,
            district=district,
            state="Kerala",
            latitude=lat,
            longitude=lon,
            certifications=certs,
            fpo_member_count=members,
            is_synthetic=True,
        )
        db.add(farm)
        farms.append(farm)
    db.flush()
    return farms


def seed_trace(db, farms: list[Farm]) -> list[ProduceLot]:
    print("  produce lots and custody chains...")
    service = TraceService(db)
    lots: list[ProduceLot] = []

    plans = [
        (farms[0], "mango", "banganapalli", 480.0, RipeningMethod.NATURAL, 9),
        (farms[0], "mango", "alphonso", 260.0, RipeningMethod.ETHYLENE_PERMITTED, 6),
        (farms[1], "banana", "nendran", 640.0, RipeningMethod.NATURAL, 12),
        (farms[2], "mango", "neelam", 320.0, RipeningMethod.ETHYLENE_PERMITTED, 5),
        (farms[3], "banana", "robusta", 720.0, RipeningMethod.NATURAL, 7),
        (farms[4], "tomato", "unspecified", 180.0, RipeningMethod.NATURAL, 3),
        (farms[1], "papaya", "red_lady", 210.0, RipeningMethod.NATURAL, 10),
    ]

    for farm, crop, cultivar, quantity, method, days_ago in plans:
        harvested = NOW - timedelta(days=days_ago)
        lot = service.register_lot(
            crop=crop,
            quantity_kg=quantity,
            farm_id=farm.id,
            cultivar=cultivar,
            plot_identifier=f"PLOT-{RNG.randint(1, 24):02d}",
            harvest_date=harvested.date(),
            declared_ripening_method=method,
            grade=RNG.choice(["A", "A", "B"]),
            actor_display_name=farm.name,
            location_name=farm.village,
            latitude=farm.latitude,
            longitude=farm.longitude,
            occurred_at=harvested,
            is_synthetic=True,
        )

        # A realistic chain: aggregation, transport with a temperature log,
        # then retail receipt.
        timeline = [
            (CustodyEventType.AGGREGATOR_SCAN_IN, "aggregator", "Palakkad Collection Centre", 8),
            (CustodyEventType.AGGREGATOR_SCAN_OUT, "aggregator", "Palakkad Collection Centre", 14),
            (CustodyEventType.TRANSPORT_DEPART, "transporter", "Palakkad Collection Centre", 16),
            (CustodyEventType.TEMPERATURE_READING, "transporter", "In transit, NH-544", 20),
            (CustodyEventType.TRANSPORT_ARRIVE, "transporter", "Thrissur Wholesale Market", 26),
            (CustodyEventType.RETAILER_RECEIVED, "retailer", "Green Basket Stores, Palakkad", 30),
        ]
        for event_type, actor, place, hours in timeline:
            if hours / 24.0 > days_ago:
                continue
            service.append_event(
                lot=lot,
                event_type=event_type,
                actor_kind=actor,
                actor_display_name=place,
                location_name=place,
                quantity_kg=quantity,
                temperature_c=(
                    round(RNG.uniform(22.0, 31.5), 1)
                    if event_type == CustodyEventType.TEMPERATURE_READING
                    else None
                ),
                humidity_pct=(
                    round(RNG.uniform(62, 88), 1)
                    if event_type == CustodyEventType.TEMPERATURE_READING
                    else None
                ),
                occurred_at=harvested + timedelta(hours=hours),
                is_synthetic=True,
            )
        lots.append(lot)

    # Split two lots into shelf units so child-QR inheritance is demonstrable.
    for parent in lots[:2]:
        for _ in range(2):
            child = service.split_lot(
                parent=parent,
                quantity_kg=round(float(parent.quantity_kg) / 6, 2),
                actor_display_name="Green Basket Stores",
                location_name="Green Basket Stores, Palakkad",
                occurred_at=NOW - timedelta(hours=RNG.randint(6, 40)),
                is_synthetic=True,
            )
            lots.append(child)

    db.flush()
    return lots


def _add_scan(
    db,
    *,
    device_pseudonym: str,
    user: User | None,
    device: Device | None,
    crop: str,
    lat: float,
    lon: float,
    ward_code: str,
    captured_at: datetime,
    merchant_ref: str | None,
    concentration: float | None,
    confirmed: bool,
    shared: bool,
    lot_id: uuid.UUID | None = None,
    anomaly: float | None = None,
) -> Scan:
    """Create one seeded scan, with its reading when confirmed."""
    w = ward(ward_code)
    uid = f"demo-{uuid.uuid4().hex[:16]}"

    if anomaly is None:
        anomaly = (
            RNG.uniform(62, 94) if (confirmed and concentration and concentration >= 1.0)
            else RNG.uniform(8, 34)
        )
    verdict = (
        ScreeningVerdict.SUSPICIOUS if anomaly >= 60
        else ScreeningVerdict.NOT_SUSPICIOUS if anomaly < 35
        else ScreeningVerdict.INCONCLUSIVE
    )

    scan = Scan(
        client_scan_uid=uid,
        device_pseudonym=device_pseudonym,
        app_version="0.1.0",
        crop=crop,
        cultivar=None,
        vision_anomaly_score=round(anomaly, 1),
        vision_verdict=verdict,
        vision_model_id="satva-screen-dev-0.1",
        vision_model_kind="dev_heuristic",
        vision_inference_ms=RNG.randint(48, 168),
        ripeness_index=round(RNG.uniform(0.35, 0.92), 3),
        evidence_grade=EvidenceGrade.CONFIRMATORY if confirmed else EvidenceGrade.SCREENING_ONLY,
        location=from_shape(Point(lon, lat), srid=4326),
        location_accuracy_m=round(RNG.uniform(4, 18), 1),
        ward_code=ward_code,
        ward_name=w["ward_name"],
        district=w["district"],
        state="Kerala",
        merchant_ref=merchant_ref,
        lot_id=lot_id,
        captured_at=captured_at,
        synced_at=captured_at + timedelta(minutes=RNG.randint(0, 240)),
        captured_offline=RNG.random() < 0.35,
        shared_with_watch=shared and confirmed,
        perceptual_hash=fake_phash(uid),
        embedding=fake_embedding(uid),
        is_synthetic=True,
    )
    db.add(scan)
    db.flush()

    if user is not None or device is not None:
        db.add(
            ScanOwnership(
                scan_id=scan.id,
                user_id=user.id if user else None,
                device_id=device.id if device else None,
            )
        )

    if confirmed and concentration is not None:
        # Interval width mirrors what the real pipeline produces at these levels.
        half = max(0.08, concentration * 0.09)
        ci_low = round(max(0.0, concentration - half), 4)
        ci_high = round(min(10.0, concentration + half), 4)
        db.add(
            ChemicalReading(
                scan_id=scan.id,
                assay=str(ReagentAssay.TURMERIC_CARBIDE),
                calibration_id=TURMERIC_CARBIDE.calibration_id,
                accepted=True,
                concentration_value=Decimal(str(round(concentration, 4))),
                concentration_unit=TURMERIC_CARBIDE.unit,
                ci_low=Decimal(str(ci_low)),
                ci_high=Decimal(str(ci_high)),
                band_label=TURMERIC_CARBIDE.band_label(concentration),
                exceeds_action_threshold=ci_low >= TURMERIC_CARBIDE.action_threshold,
                delta_e_nearest=round(RNG.uniform(0.4, 3.2), 3),
                correction_residual_de=round(RNG.uniform(0.3, 1.4), 3),
                exposure_score=round(RNG.uniform(180, 236), 1),
                illuminant_tint=round(RNG.uniform(0.6, 5.5), 2),
                quality_report={
                    "residual_de_mean": round(RNG.uniform(0.3, 1.4), 3),
                    "brightest_neutral": round(RNG.uniform(180, 236), 1),
                    "neutral_chroma_mean": round(RNG.uniform(0.6, 5.5), 2),
                    "strip_l_std": round(RNG.uniform(0.7, 3.6), 2),
                    "seeded": True,
                },
                computed_on="device",
                pipeline_version="1.0.0",
                is_synthetic=True,
            )
        )
    db.flush()
    return scan


def seed_scans(db, accounts: dict, merchants: list[Merchant], lots: list[ProduceLot]) -> dict:
    print("  scans and confirmatory readings...")
    consumers = accounts["consumers"]
    counts = {"confirmed": 0, "screening_only": 0, "refused": 0}

    # --- 1. The genuine hotspot: Palakkad Municipal Market ------------------
    # Nine devices over three weeks, most exceeding the action threshold. This
    # is the cluster the public map is meant to show.
    w14 = ward("PKD-14")
    for i in range(22):
        user, device = consumers[i % 9]
        lat, lon = jitter(w14["lat"], w14["lon"], 190)
        exceeds = RNG.random() < 0.72
        _add_scan(
            db,
            device_pseudonym=device.device_pseudonym,
            user=user,
            device=device,
            crop=RNG.choice(["mango", "mango", "banana"]),
            lat=lat,
            lon=lon,
            ward_code="PKD-14",
            captured_at=NOW - timedelta(days=RNG.uniform(0.4, 21), hours=RNG.uniform(0, 12)),
            merchant_ref=RNG.choice(merchants[:3]).merchant_ref,
            concentration=(
                RNG.uniform(1.6, 6.4) if exceeds else RNG.uniform(0.0, 0.55)
            ),
            confirmed=True,
            shared=True,
        )
        counts["confirmed"] += 1

    # --- 2. A second, smaller hotspot: Shakthan Market, Thrissur -----------
    w09 = ward("TSR-09")
    for i in range(11):
        user, device = consumers[(i % 5) + 9]
        lat, lon = jitter(w09["lat"], w09["lon"], 170)
        exceeds = RNG.random() < 0.6
        _add_scan(
            db,
            device_pseudonym=device.device_pseudonym,
            user=user,
            device=device,
            crop=RNG.choice(["banana", "papaya"]),
            lat=lat,
            lon=lon,
            ward_code="TSR-09",
            captured_at=NOW - timedelta(days=RNG.uniform(0.5, 16), hours=RNG.uniform(0, 12)),
            merchant_ref=RNG.choice(merchants[5:7]).merchant_ref,
            concentration=RNG.uniform(1.2, 4.1) if exceeds else RNG.uniform(0.0, 0.6),
            confirmed=True,
            shared=True,
        )
        counts["confirmed"] += 1

    # --- 3. The suppressed cluster: one device, many readings --------------
    # This demonstrates the coordinated-reporting defence. It clusters
    # geometrically but must NOT publish, because a single device cannot
    # corroborate itself.
    w31 = ward("PKD-31")
    solo_user, solo_device = consumers[13]
    for _ in range(9):
        lat, lon = jitter(w31["lat"], w31["lon"], 140)
        _add_scan(
            db,
            device_pseudonym=solo_device.device_pseudonym,
            user=solo_user,
            device=solo_device,
            crop="mango",
            lat=lat,
            lon=lon,
            ward_code="PKD-31",
            captured_at=NOW - timedelta(days=RNG.uniform(0.5, 9)),
            merchant_ref=merchants[4].merchant_ref,
            concentration=RNG.uniform(2.4, 5.8),
            confirmed=True,
            shared=True,
        )
        counts["confirmed"] += 1

    # --- 4. Clean readings elsewhere ---------------------------------------
    # A map that is alarming everywhere tells nobody anything.
    for ward_code in ("PKD-01", "PKD-22", "PKD-38", "PKD-52", "TSR-02", "TSR-17", "TSR-31"):
        w = ward(ward_code)
        for _ in range(RNG.randint(3, 6)):
            user, device = consumers[RNG.randrange(len(consumers))]
            lat, lon = jitter(w["lat"], w["lon"], 400)
            _add_scan(
                db,
                device_pseudonym=device.device_pseudonym,
                user=user,
                device=device,
                crop=RNG.choice(["mango", "banana", "tomato", "papaya"]),
                lat=lat,
                lon=lon,
                ward_code=ward_code,
                captured_at=NOW - timedelta(days=RNG.uniform(0.5, 27)),
                merchant_ref=None,
                concentration=RNG.uniform(0.0, 0.7),
                confirmed=True,
                shared=True,
            )
            counts["confirmed"] += 1

    # --- 5. Screening-only scans (never eligible for Watch) ----------------
    for _ in range(26):
        user, device = consumers[RNG.randrange(len(consumers))]
        w = ward(RNG.choice([x["ward_code"] for x in WARD_TABLE]))
        lat, lon = jitter(w["lat"], w["lon"], 500)
        _add_scan(
            db,
            device_pseudonym=device.device_pseudonym,
            user=user,
            device=device,
            crop=RNG.choice(["mango", "banana", "tomato", "papaya"]),
            lat=lat,
            lon=lon,
            ward_code=w["ward_code"],
            captured_at=NOW - timedelta(days=RNG.uniform(0, 29)),
            merchant_ref=None,
            concentration=None,
            confirmed=False,
            shared=False,
        )
        counts["screening_only"] += 1

    # --- 6. Refused measurements -------------------------------------------
    # Recorded because refusals are meaningful: they show SATVA declining to
    # guess rather than silently dropping a scan.
    refusals = [
        ("reference_card_not_found", "The SATVA reference card was not found in the photograph."),
        ("exposure_out_of_range", "Reference white is at 253/255 - the frame is over-exposed."),
        ("illuminant_too_tinted", "Grey patches show a colour cast under sodium lighting."),
        ("correction_residual_too_high", "Colour correction left a mean error of dE 4.8."),
        ("strip_region_not_uniform", "The strip well is not uniform - the strip may be folded."),
    ]
    for reason, detail in refusals:
        user, device = consumers[RNG.randrange(len(consumers))]
        w = ward("PKD-14")
        lat, lon = jitter(w["lat"], w["lon"], 200)
        scan = _add_scan(
            db,
            device_pseudonym=device.device_pseudonym,
            user=user,
            device=device,
            crop="mango",
            lat=lat,
            lon=lon,
            ward_code="PKD-14",
            captured_at=NOW - timedelta(days=RNG.uniform(0, 20)),
            merchant_ref=None,
            concentration=None,
            confirmed=False,
            shared=False,
            anomaly=RNG.uniform(64, 88),
        )
        scan.evidence_grade = EvidenceGrade.REJECTED
        db.add(
            ChemicalReading(
                scan_id=scan.id,
                assay=str(ReagentAssay.TURMERIC_CARBIDE),
                calibration_id=TURMERIC_CARBIDE.calibration_id,
                accepted=False,
                reject_reason=reason,
                reject_detail=detail,
                computed_on="device",
                pipeline_version="1.0.0",
                is_synthetic=True,
            )
        )
        counts["refused"] += 1

    # --- 7. A duplicate submission -----------------------------------------
    original = db.scalar(
        select(Scan).where(Scan.is_synthetic.is_(True), Scan.perceptual_hash.is_not(None)).limit(1)
    )
    if original is not None:
        user, device = consumers[3]
        duplicate = Scan(
            client_scan_uid=f"demo-dup-{uuid.uuid4().hex[:12]}",
            device_pseudonym=device.device_pseudonym,
            crop=original.crop,
            vision_anomaly_score=original.vision_anomaly_score,
            vision_verdict=original.vision_verdict,
            vision_model_id="satva-screen-dev-0.1",
            vision_model_kind="dev_heuristic",
            evidence_grade=EvidenceGrade.SCREENING_ONLY,
            location=original.location,
            ward_code=original.ward_code,
            ward_name=original.ward_name,
            district=original.district,
            state="Kerala",
            captured_at=NOW - timedelta(hours=3),
            perceptual_hash=original.perceptual_hash,
            embedding=original.embedding,
            duplicate_of_scan_id=original.id,
            shared_with_watch=False,
            is_synthetic=True,
        )
        db.add(duplicate)

    db.flush()
    return counts


def seed_complaint(db) -> Complaint | None:
    print("  complaint package...")
    row = db.execute(
        select(Scan, ChemicalReading)
        .join(ChemicalReading, ChemicalReading.scan_id == Scan.id)
        .where(
            Scan.is_synthetic.is_(True),
            Scan.evidence_grade == EvidenceGrade.CONFIRMATORY,
            ChemicalReading.accepted.is_(True),
            ChemicalReading.exceeds_action_threshold.is_(True),
            Scan.merchant_ref.is_not(None),
        )
        .limit(1)
    ).first()
    if row is None:
        return None

    scan, reading = row
    complaint = Complaint(
        reference_code="SATVA-2026-DEMO01",
        scan_id=scan.id,
        reading_id=reading.id,
        status=ComplaintStatus.PACKAGE_READY,
        complainant_provided=True,
        crop=scan.crop,
        merchant_ref=scan.merchant_ref,
        ward_code=scan.ward_code,
        district=scan.district,
        state=scan.state,
        incident_at=scan.captured_at,
        evidence_snapshot={
            "seeded": True,
            "note": "Demonstration package. The PDF is generated on demand via the API.",
            "concentration_value": float(reading.concentration_value),
            "concentration_unit": reading.concentration_unit,
        },
        is_synthetic=True,
    )
    db.add(complaint)
    db.flush()
    return complaint


def seed_retail(db, lots: list[ProduceLot], accounts: dict) -> None:
    print("  retail outlets, inventory and shelf predictions...")
    retailer = accounts[Role.RETAILER.value]
    outlet = RetailOutlet(
        outlet_code="OUT-DEMO01",
        name="Green Basket Stores, Palakkad",
        owner_user_id=retailer.id,
        address_line="Market Road, Palakkad, Kerala",
        ward_code="PKD-14",
        district="Palakkad",
        state="Kerala",
        latitude=10.7760,
        longitude=76.6551,
        donation_partner="Palakkad Food Bank (demo partner)",
        is_synthetic=True,
    )
    db.add(outlet)
    db.flush()

    crops = ["mango", "banana", "papaya", "tomato"]
    for index in range(14):
        crop = crops[index % len(crops)]
        # Retail turnover is fast: most stock on a shelf arrived in the last day
        # or two, with a tail of older items. Drawing uniformly over a week made
        # every single item need action, which tells a manager nothing.
        received = NOW - timedelta(
            days=RNG.uniform(0.1, 1.6) if RNG.random() < 0.6 else RNG.uniform(1.6, 5.5)
        )
        lot = lots[index % len(lots)] if index % 3 != 2 else None
        tag = f"BLE-{index:03d}" if index % 3 != 2 else None

        item = InventoryItem(
            outlet_id=outlet.id,
            lot_id=lot.id if lot and lot.crop == crop else None,
            sku=f"SKU-{crop[:3].upper()}-{index:03d}",
            crop=crop,
            quantity_kg=Decimal(str(round(RNG.uniform(8, 65), 2))),
            unit_price_inr=Decimal(str(round(RNG.uniform(28, 140), 2))),
            received_at=received,
            display_location=RNG.choice(
                ["Cold shelf", "Cold shelf", "Front rack", "Back store", "Window"]
            ),
            ble_tag_ref=tag,
            is_synthetic=True,
        )
        db.add(item)
        db.flush()

        readings: list[tuple[datetime, float]] = []
        if tag:
            # A plausible daily temperature cycle, warmer for items left out.
            hours = int((NOW - received).total_seconds() / 3600)
            # A cold shelf holds produce near the crop's reference temperature;
            # the front rack and the window sit at market ambient. That
            # difference is most of what drives the shelf-life spread.
            base = (
                RNG.uniform(11.0, 15.0)
                if item.display_location == "Cold shelf"
                else RNG.uniform(24.0, 31.0)
            )
            for hour in range(0, max(1, hours), 2):
                stamp = received + timedelta(hours=hour)
                temperature = round(
                    base + 3.5 * np.sin(hour / 24 * 2 * np.pi) + RNG.uniform(-1, 1), 2
                )
                readings.append((stamp, temperature))
                db.add(
                    TemperatureTagReading(
                        lot_id=item.lot_id,
                        tag_ref=tag,
                        temperature_c=temperature,
                        humidity_pct=round(RNG.uniform(58, 86), 1),
                        recorded_at=stamp,
                        source="ble_tag",
                        is_simulated=True,
                        is_synthetic=True,
                    )
                )

        # Ripeness follows age rather than being drawn at random. Physically
        # that is what happens, and it also gives the dashboard a realistic
        # spread of actions: a shelf where every item is flagged tells a manager
        # nothing.
        days_held = (NOW - received).total_seconds() / 86400.0
        typical_life = {"mango": 9.0, "banana": 7.0, "papaya": 6.5, "tomato": 8.0}[crop]
        ripeness = min(0.97, 0.18 + (days_held / typical_life) * 0.72 + RNG.uniform(-0.06, 0.06))

        result = predict(
            crop=crop,
            ripeness_index=round(max(0.05, ripeness), 3),
            received_at=received,
            temperature_readings=readings,
        )
        db.add(
            ShelfPrediction(
                inventory_item_id=item.id,
                ripeness_index=result.ripeness_index,
                freshness_score=result.freshness_score,
                remaining_shelf_life_days=result.remaining_shelf_life_days,
                confidence_low_days=result.confidence_low_days,
                confidence_high_days=result.confidence_high_days,
                mean_temperature_c=result.mean_temperature_c,
                accumulated_degree_hours=result.accumulated_degree_hours,
                temperature_source=result.temperature_source,
                recommended_action=result.recommended_action,
                suggested_markdown_pct=result.suggested_markdown_pct,
                rationale=result.rationale,
                model_id=result.model_id,
                is_synthetic=True,
            )
        )

    # Spoilage history, showing a downward trend after Shelf was adopted.
    for days_back in range(45, 0, -1):
        day = (NOW - timedelta(days=days_back)).date()
        improvement = max(0.35, 1.0 - (45 - days_back) / 70.0)
        for disposition, scale in (("discarded", 9.0), ("donated", 4.0), ("marked_down", 6.5)):
            quantity = round(RNG.uniform(0.5, scale) * improvement, 2)
            if quantity <= 0.2:
                continue
            db.add(
                SpoilageEvent(
                    outlet_id=outlet.id,
                    crop=RNG.choice(crops),
                    occurred_on=day,
                    quantity_kg=Decimal(str(quantity)),
                    disposition=disposition,
                    value_inr=Decimal(str(round(quantity * RNG.uniform(30, 90), 2))),
                    is_synthetic=True,
                )
            )
    db.flush()


def seed_marketplace(db, farms: list[Farm], lots: list[ProduceLot]) -> None:
    print("  marketplace listings, ratings and trust scores...")
    titles = [
        ("Naturally ripened Banganapalli mangoes", "mango", "banganapalli", 96.0),
        ("Nendran bananas, no carbide", "banana", "nendran", 62.0),
        ("Red Lady papaya, organic plot", "papaya", "red_lady", 48.0),
        ("Alphonso mangoes, ethylene ripened (permitted)", "mango", "alphonso", 148.0),
        ("Farm tomatoes, same-day harvest", "tomato", "unspecified", 34.0),
    ]
    for index, (title, crop, cultivar, price) in enumerate(titles):
        farm = farms[index % len(farms)]
        matching = [lot for lot in lots if lot.crop == crop and lot.farm_id == farm.id]
        db.add(
            MarketplaceListing(
                farm_id=farm.id,
                lot_id=matching[0].id if matching else None,
                title=title,
                crop=crop,
                cultivar=cultivar,
                quantity_kg=Decimal(str(round(RNG.uniform(60, 400), 2))),
                price_inr_per_kg=Decimal(str(price)),
                available_from=date.today(),
                description=(
                    "Demonstration listing. Trace records are available for this lot where a "
                    "lot is linked."
                ),
                is_active=True,
                is_synthetic=True,
            )
        )

    for farm in farms:
        for _ in range(RNG.randint(2, 9)):
            db.add(
                BuyerRating(
                    farm_id=farm.id,
                    rating=RNG.choices([5, 4, 3, 2], weights=[5, 4, 2, 1])[0],
                    comment=None,
                    is_synthetic=True,
                )
            )
    db.flush()

    from app.models.retail import TrustScore

    season = current_season()
    for farm in farms:
        result = compute_trust_score(db, farm)
        db.add(
            TrustScore(
                farm_id=farm.id,
                season=season,
                score=result.score,
                scan_pass_component=result.scan_pass_component,
                trace_completeness_component=result.trace_completeness_component,
                certification_component=result.certification_component,
                buyer_rating_component=result.buyer_rating_component,
                decay_factor=result.decay_factor,
                sample_size=result.sample_size,
                computed_at=NOW,
                breakdown=result.breakdown,
                is_synthetic=True,
            )
        )
    db.flush()


def seed_nodes(db) -> None:
    print("  SATVA Node devices and telemetry (simulated)...")
    specs = [
        ("node-pkd-market-01", "Palakkad Municipal Market, crate bay 1", "PKD-14", "Palakkad"),
        ("node-tsr-shakthan-01", "Shakthan Thampuran Market, ripening shed", "TSR-09", "Thrissur"),
    ]
    for node_uid, label, ward_code, district in specs:
        w = ward(ward_code)
        device = DeviceRecord(
            node_uid=node_uid,
            label=label,
            firmware_version="0.1.0",
            hardware_revision="rev-A",
            market_name=w["ward_name"],
            ward_code=ward_code,
            district=district,
            state="Kerala",
            latitude=w["lat"],
            longitude=w["lon"],
            mq_r0_ohms=round(RNG.uniform(8_000, 14_000), 1),
            calibrated_at=NOW - timedelta(days=12),
            is_active=True,
            is_simulated=True,
            last_seen_at=NOW - timedelta(minutes=RNG.randint(2, 55)),
            notes=(
                "Simulated device. No physical hardware is deployed; telemetry is generated "
                "by scripts/simulate_node.py."
            ),
            is_synthetic=True,
        )
        db.add(device)
        db.flush()

        for hour in range(72):
            stamp = NOW - timedelta(hours=72 - hour)
            # An elevated window mid-series, so the advisory path is visible.
            elevated = 30 <= hour <= 38
            ratio = round(RNG.uniform(2.8, 3.9) if elevated else RNG.uniform(0.7, 1.9), 4)
            db.add(
                NodeTelemetry(
                    device_id=device.id,
                    sample_uid=f"{node_uid}-{hour:04d}",
                    recorded_at=stamp,
                    temperature_c=round(RNG.uniform(24, 34), 2),
                    humidity_pct=round(RNG.uniform(55, 88), 1),
                    mq_raw_adc=RNG.randint(320, 2600),
                    mq_rs_ohms=round(device.mq_r0_ohms * ratio, 1),
                    mq_rs_r0_ratio=ratio,
                    advisory_flag=ratio >= 2.5,
                    advisory_reason="mq_ratio_above_advisory_threshold" if ratio >= 2.5 else None,
                    raw_payload={"simulated": True},
                    is_synthetic=True,
                )
            )
    db.flush()


def run_clustering(db) -> dict:
    print("  running DBSCAN clustering over seeded readings...")
    from app.services.watch.service import WatchService

    run = WatchService(db).run_clustering()
    return {
        "input_readings": run.input_reading_count,
        "clusters": run.cluster_count,
        "publishable": run.publishable_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed SATVA demonstration data.")
    parser.add_argument("--reset", action="store_true", help="remove existing demo data first")
    args = parser.parse_args()

    print("\nSATVA demo seeder")
    print("=" * 62)
    print("Every row created is flagged is_synthetic=True and is surfaced as")
    print("demo data by the API and both dashboards. None of it is evidence.")
    print("=" * 62)

    with session_scope() as db:
        if args.reset:
            reset(db)

        accounts = seed_users(db)
        merchants = seed_merchants(db)
        farms = seed_farms(db)
        lots = seed_trace(db, farms)
        counts = seed_scans(db, accounts, merchants, lots)
        seed_complaint(db)
        seed_retail(db, lots, accounts)
        seed_marketplace(db, farms, lots)
        seed_nodes(db)
        cluster_stats = run_clustering(db)

    print("\nSeed complete.")
    print(f"  farms                 {len(farms)}")
    print(f"  lots (incl. children) {len(lots)}")
    print(f"  merchants             {len(merchants)}")
    print(f"  confirmed scans       {counts['confirmed']}")
    print(f"  screening-only scans  {counts['screening_only']}")
    print(f"  refused measurements  {counts['refused']}")
    print(f"  clustering input      {cluster_stats['input_readings']} readings")
    print(f"  clusters found        {cluster_stats['clusters']}")
    print(f"  publishable clusters  {cluster_stats['publishable']}")
    print("\nDemo sign-in (dashboard):")
    for email in (
        "officer@satva.demo", "retailer@satva.demo", "farmer@satva.demo", "admin@satva.demo"
    ):
        print(f"  {email:26s} {DEMO_PASSWORD}")
    print("\nConsumer sign-in (mobile): any +9199000XXXX number; the dev OTP")
    print("provider returns the code in the API response.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
