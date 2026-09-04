"""/hotspots -- the public, ward-level SATVA Watch map.

Every route in this module is public and therefore governed by non-negotiable
rule 4: areas, never named vendors. The response models carry no centroid, no
merchant reference, no device pseudonym and no member scan ids. Vendor-level
detail lives exclusively under /officers, behind an authenticated, audited
dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.constants import DISCLAIMER_LONG, DISCLAIMER_SHORT, ClusterSeverity
from app.db.session import get_db
from app.schemas.common import Disclaimer, GeoPoint
from app.schemas.watch import (
    PublicHotspotOut,
    PublicHotspotResponse,
    WardAggregate,
    WatchStatsOut,
)
from app.services.watch.service import PUBLIC_NOTICE, WatchService

router = APIRouter(prefix="/hotspots", tags=["watch"])

DISCLAIMER = Disclaimer(short=DISCLAIMER_SHORT, long=DISCLAIMER_LONG)


@router.get(
    "",
    response_model=PublicHotspotResponse,
    summary="Public ward-level hotspot map",
    description=(
        "Ward-level aggregates of confirmed strip readings. A ward appears only when at least "
        "one cluster in it met the independent-device corroboration threshold. No individual "
        "trader, stall, coordinate or device is exposed here under any circumstances -- that "
        "detail is available only to authenticated Food Safety Officers."
    ),
)
def public_hotspots(
    window_days: int = Query(30, ge=1, le=365),
    district: str | None = Query(None, max_length=80),
    db: Session = Depends(get_db),
) -> PublicHotspotResponse:
    service = WatchService(db)
    wards = service.public_wards(window_days=window_days)
    if district:
        wards = [w for w in wards if (w.get("district") or "").lower() == district.lower()]

    aggregates = [
        WardAggregate(
            ward_code=w["ward_code"],
            ward_name=w["ward_name"],
            district=w["district"],
            state=w["state"],
            representative_point=GeoPoint(**w["representative_point"]),
            confirmed_reading_count=w["confirmed_reading_count"],
            cluster_count=w["cluster_count"],
            highest_severity=ClusterSeverity(w["highest_severity"]),
            crops=w["crops"],
            last_reading_at=w["last_reading_at"],
            contains_demo_data=w["contains_demo_data"],
        )
        for w in wards
    ]

    hotspots = [
        PublicHotspotOut(
            ward_code=w["ward_code"],
            ward_name=w["ward_name"],
            district=w["district"],
            state=w["state"],
            representative_point=GeoPoint(**w["representative_point"]),
            severity=ClusterSeverity(w["highest_severity"]),
            confirmed_reading_count=w["confirmed_reading_count"],
            independent_device_count=w["independent_device_count"],
            crops=w["crops"],
            window_start=w["last_reading_at"] or datetime.now(UTC),
            window_end=w["last_reading_at"] or datetime.now(UTC),
            contains_demo_data=w["contains_demo_data"],
        )
        for w in wards
    ]

    return PublicHotspotResponse(
        wards=aggregates,
        hotspots=hotspots,
        window_days=window_days,
        total_confirmed_readings=sum(w["confirmed_reading_count"] for w in wards),
        generated_at=datetime.now(UTC),
        disclaimer=DISCLAIMER,
        notice=PUBLIC_NOTICE,
    )


@router.get(
    "/stats",
    response_model=WatchStatsOut,
    summary="Aggregate Watch statistics",
    description=(
        "Counts only. `suppressed_clusters` is published deliberately: it shows how often "
        "SATVA declined to raise a hotspot for want of corroboration, which is evidence that "
        "the restraint rules are actually operating."
    ),
)
def watch_stats(
    window_days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)
) -> WatchStatsOut:
    return WatchStatsOut(**WatchService(db).stats(window_days=window_days))
