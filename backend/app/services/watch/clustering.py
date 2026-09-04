"""Hotspot detection: DBSCAN over space and time, with corroboration rules.

Why DBSCAN (spec 3.3, Ester et al. 1996)
----------------------------------------
Contamination hotspots are irregular in shape, unknown in number, and embedded
in noise from isolated one-off readings. k-means would need the cluster count in
advance and would force every reading into some cluster; DBSCAN needs neither
and labels sparse readings as noise, which is exactly the right treatment for a
single reading in an otherwise clean ward.

Distance metric
---------------
Clustering runs on the **haversine** metric over radians, not on raw
latitude/longitude Euclidean distance. Near Palakkad (10.8 degrees N) a degree of
longitude is about 1.7% shorter than a degree of latitude; treating them as
equal would stretch clusters east-west and silently change which readings
corroborate each other.

Time is handled by windowing rather than by adding a third dimension to the
metric, because there is no defensible exchange rate between metres and hours.
The window comes from the spec's 30-day hotspot view.

The corroboration rule
----------------------
A cluster is only publishable when enough **independent devices** contributed.
This is the structural defence against a competitor manufacturing a case, and it
is applied after clustering rather than during it, so the officer view can still
see sub-threshold clusters (with the suppression reason attached) while the
public map cannot.

Privacy
-------
This module reads `analytics` only. It never touches the identity schema; the
device pseudonyms it counts are keyed hashes that cannot be reversed to a person
without the identity table and the pepper. That is non-negotiable rule 12.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sklearn.cluster import DBSCAN

from app.core.constants import ClusterSeverity
from app.core.logging import get_logger

log = get_logger("satva.watch.clustering")

EARTH_RADIUS_M = 6_371_008.8


@dataclass
class ReadingPoint:
    """One confirmed reading, as the clusterer sees it.

    Deliberately contains no user id, no merchant name and no image: the
    clustering input is the minimum that the algorithm needs.
    """

    scan_id: uuid.UUID
    latitude: float
    longitude: float
    captured_at: datetime
    device_pseudonym: str
    concentration: float | None = None
    exceeds_action_threshold: bool = False
    crop: str | None = None
    assay: str | None = None
    ward_code: str | None = None
    ward_name: str | None = None
    district: str | None = None
    state: str | None = None
    weight: float = 1.0
    is_synthetic: bool = False


@dataclass
class ClusterResult:
    """A detected cluster, before it is persisted."""

    members: list[ReadingPoint]
    centroid_lat: float
    centroid_lon: float
    radius_m: float
    window_start: datetime
    window_end: datetime
    independent_device_count: int
    member_count: int
    mean_concentration: float | None
    max_concentration: float | None
    exceedance_rate: float
    severity: str
    inspection_priority: float
    is_publishable: bool
    suppression_reason: str | None
    crop: str | None
    assay: str | None
    ward_code: str | None
    ward_name: str | None
    district: str | None
    state: str | None
    contains_synthetic: bool = False
    diagnostics: dict = field(default_factory=dict)


@dataclass
class ClusteringParameters:
    eps_metres: float = 250.0
    min_samples: int = 3
    time_window_days: int = 30
    min_independent_devices: int = 3
    # A cluster made of readings that all arrive within a few minutes from
    # devices at the same spot is more likely to be one person than a genuine
    # pattern.
    min_temporal_spread_minutes: float = 30.0


def haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _centroid(points: list[ReadingPoint]) -> tuple[float, float]:
    """Spherical centroid.

    Averaging degrees directly is wrong across the antimeridian and slightly
    wrong everywhere else. Converting to unit vectors and back costs almost
    nothing and is correct everywhere.
    """
    vectors = np.array(
        [
            [
                math.cos(math.radians(p.latitude)) * math.cos(math.radians(p.longitude)),
                math.cos(math.radians(p.latitude)) * math.sin(math.radians(p.longitude)),
                math.sin(math.radians(p.latitude)),
            ]
            for p in points
        ]
    )
    mean = vectors.mean(axis=0)
    norm = np.linalg.norm(mean)
    if norm < 1e-12:
        return points[0].latitude, points[0].longitude
    mean = mean / norm
    return (
        math.degrees(math.asin(np.clip(mean[2], -1.0, 1.0))),
        math.degrees(math.atan2(mean[1], mean[0])),
    )


def _mode(values: list) -> object | None:
    present = [v for v in values if v]
    if not present:
        return None
    return max(set(present), key=present.count)


def _severity(exceedance_rate: float, devices: int, members: int) -> str:
    """Severity band for the officer worklist.

    Driven by how many readings exceeded the action threshold and by how much
    independent corroboration exists -- not by concentration alone, because a
    single high reading is weaker evidence than several moderate ones from
    different people.
    """
    if exceedance_rate >= 0.6 and devices >= 5 and members >= 6:
        return ClusterSeverity.HIGH
    if exceedance_rate >= 0.4 and devices >= 3:
        return ClusterSeverity.ELEVATED
    return ClusterSeverity.ADVISORY


def _inspection_priority(
    *,
    exceedance_rate: float,
    devices: int,
    members: int,
    recency_days: float,
    mean_conc: float | None,
) -> float:
    """Rank clusters for the officer worklist, 0-100.

    Weighted so that corroboration and recency dominate. An officer's time is
    the scarce resource, and the aim stated in the spec is a better hit rate
    than random sampling, which means ranking by "how likely is an inspection
    here to find something", not by "how alarming does this look".
    """
    corroboration = min(devices / 6.0, 1.0)
    volume = min(members / 10.0, 1.0)
    recency = max(0.0, 1.0 - recency_days / 30.0)
    magnitude = min((mean_conc or 0.0) / 5.0, 1.0)

    score = (
        0.34 * corroboration
        + 0.28 * exceedance_rate
        + 0.18 * recency
        + 0.12 * volume
        + 0.08 * magnitude
    )
    return round(min(100.0, score * 100.0), 2)


def cluster_readings(
    readings: list[ReadingPoint],
    parameters: ClusteringParameters | None = None,
    *,
    now: datetime | None = None,
) -> list[ClusterResult]:
    """Run DBSCAN over one time window and apply the corroboration rules."""
    params = parameters or ClusteringParameters()
    reference_time = now or datetime.now(UTC)
    window_start = reference_time - timedelta(days=params.time_window_days)

    in_window = [
        r for r in readings if r.captured_at >= window_start and r.captured_at <= reference_time
    ]
    if len(in_window) < params.min_samples:
        log.info("clustering_skipped", reason="insufficient_readings", count=len(in_window))
        return []

    coordinates = np.radians(np.array([[r.latitude, r.longitude] for r in in_window]))
    labels = DBSCAN(
        eps=params.eps_metres / EARTH_RADIUS_M,
        min_samples=params.min_samples,
        metric="haversine",
        algorithm="ball_tree",
    ).fit_predict(coordinates)

    clusters: list[ClusterResult] = []
    for label in sorted(set(labels) - {-1}):
        members = [r for r, lbl in zip(in_window, labels, strict=True) if lbl == label]
        clusters.append(_build_cluster(members, params, reference_time, window_start))

    clusters.sort(key=lambda c: c.inspection_priority, reverse=True)
    log.info(
        "clustering_complete",
        input_readings=len(in_window),
        clusters=len(clusters),
        publishable=sum(1 for c in clusters if c.is_publishable),
    )
    return clusters


def _build_cluster(
    members: list[ReadingPoint],
    params: ClusteringParameters,
    reference_time: datetime,
    window_start: datetime,
) -> ClusterResult:
    centroid_lat, centroid_lon = _centroid(members)
    radius = max(
        (haversine_metres(centroid_lat, centroid_lon, m.latitude, m.longitude) for m in members),
        default=0.0,
    )

    devices = {m.device_pseudonym for m in members}
    concentrations = [m.concentration for m in members if m.concentration is not None]
    mean_concentration = float(np.mean(concentrations)) if concentrations else None
    max_concentration = float(np.max(concentrations)) if concentrations else None
    exceedance_rate = sum(1 for m in members if m.exceeds_action_threshold) / len(members)

    timestamps = sorted(m.captured_at for m in members)
    temporal_spread_minutes = (timestamps[-1] - timestamps[0]).total_seconds() / 60.0
    recency_days = (reference_time - timestamps[-1]).total_seconds() / 86400.0

    # Corroboration gate. Order matters: report the *first* unmet condition so
    # the officer view can explain precisely what a cluster is waiting on.
    suppression_reason: str | None = None
    if len(devices) < params.min_independent_devices:
        suppression_reason = (
            f"awaiting_corroboration: {len(devices)} of {params.min_independent_devices} "
            "independent devices"
        )
    elif temporal_spread_minutes < params.min_temporal_spread_minutes:
        suppression_reason = (
            f"readings_too_simultaneous: all within {temporal_spread_minutes:.0f} minutes"
        )
    elif exceedance_rate <= 0.0:
        suppression_reason = "no_reading_exceeds_action_threshold"

    severity = _severity(exceedance_rate, len(devices), len(members))

    return ClusterResult(
        members=members,
        centroid_lat=centroid_lat,
        centroid_lon=centroid_lon,
        radius_m=round(radius, 2),
        window_start=window_start,
        window_end=reference_time,
        independent_device_count=len(devices),
        member_count=len(members),
        mean_concentration=mean_concentration,
        max_concentration=max_concentration,
        exceedance_rate=round(exceedance_rate, 4),
        severity=severity,
        inspection_priority=_inspection_priority(
            exceedance_rate=exceedance_rate,
            devices=len(devices),
            members=len(members),
            recency_days=recency_days,
            mean_conc=mean_concentration,
        ),
        is_publishable=suppression_reason is None,
        suppression_reason=suppression_reason,
        crop=_mode([m.crop for m in members]),
        assay=_mode([m.assay for m in members]),
        ward_code=_mode([m.ward_code for m in members]),
        ward_name=_mode([m.ward_name for m in members]),
        district=_mode([m.district for m in members]),
        state=_mode([m.state for m in members]),
        contains_synthetic=any(m.is_synthetic for m in members),
        diagnostics={
            "temporal_spread_minutes": round(temporal_spread_minutes, 1),
            "recency_days": round(recency_days, 2),
            "device_reading_counts": _device_counts(members),
        },
    )


def _device_counts(members: list[ReadingPoint]) -> dict[str, int]:
    """How many readings each device contributed.

    Surfaced to officers because a cluster where one device supplied nine of ten
    readings deserves a different reading than one with ten devices contributing
    one each, even though both pass the corroboration threshold.
    """
    counts: dict[str, int] = {}
    for member in members:
        counts[member.device_pseudonym] = counts.get(member.device_pseudonym, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
