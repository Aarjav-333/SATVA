"""Watch persistence: run clustering, store results, serve public and officer views.

The public and officer read paths are separate methods returning separate
schemas. That separation is the enforcement mechanism for non-negotiable rules
4 and 5: `public_hotspots` cannot leak a centroid or a merchant because it never
loads them, and there is no shared serialiser that could be changed in a way
that quietly widens the public surface.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from geoalchemy2.shape import from_shape, to_shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ClusterSeverity, EvidenceGrade
from app.core.logging import get_logger
from app.models.identity import Merchant
from app.models.scan import ChemicalReading, Scan
from app.models.watch import ClusterMember, ClusterRun, Complaint, HotspotCluster
from app.services.watch.clustering import (
    ClusteringParameters,
    ReadingPoint,
    cluster_readings,
)
from app.services.watch.wards import ward_by_code

log = get_logger("satva.watch.service")

SEVERITY_ORDER = {
    ClusterSeverity.ADVISORY: 0,
    ClusterSeverity.ELEVATED: 1,
    ClusterSeverity.HIGH: 2,
}

PUBLIC_NOTICE = (
    "This map shows ward-level areas where multiple independent confirmatory strip readings "
    "have been recorded. It never identifies an individual trader. A reading is not a finding "
    "of adulteration and SATVA is not a statutory authority; only the food safety authority "
    "can draw an official sample and determine whether the law has been broken."
)


class WatchService:
    def __init__(self, db: Session):
        self.db = db

    # --- Input ------------------------------------------------------------
    def load_confirmed_readings(self, *, window_days: int | None = None) -> list[ReadingPoint]:
        """Load the confirmed, shared, geolocated readings eligible for clustering.

        The filters here are the evidence rule expressed as SQL. A scan that is
        not CONFIRMATORY, not shared, has no location, or is a known duplicate
        never enters the clustering input at all -- it is not filtered out
        later, it is never loaded.
        """
        days = window_days or settings.cluster_time_window_days
        since = datetime.now(UTC) - timedelta(days=days)

        rows = self.db.execute(
            select(Scan, ChemicalReading)
            .join(ChemicalReading, ChemicalReading.scan_id == Scan.id)
            .where(
                Scan.evidence_grade == EvidenceGrade.CONFIRMATORY,
                Scan.shared_with_watch.is_(True),
                Scan.location.is_not(None),
                Scan.duplicate_of_scan_id.is_(None),
                Scan.captured_at >= since,
                ChemicalReading.accepted.is_(True),
            )
        ).all()

        points: list[ReadingPoint] = []
        for scan, reading in rows:
            shape = to_shape(scan.location)
            points.append(
                ReadingPoint(
                    scan_id=scan.id,
                    latitude=shape.y,
                    longitude=shape.x,
                    captured_at=scan.captured_at,
                    device_pseudonym=scan.device_pseudonym,
                    concentration=float(reading.concentration_value)
                    if reading.concentration_value is not None
                    else None,
                    exceeds_action_threshold=reading.exceeds_action_threshold,
                    crop=scan.crop,
                    assay=reading.assay,
                    ward_code=scan.ward_code,
                    ward_name=scan.ward_name,
                    district=scan.district,
                    state=scan.state,
                    is_synthetic=scan.is_synthetic,
                )
            )
        return points

    # --- Clustering run ---------------------------------------------------
    def run_clustering(self, parameters: ClusteringParameters | None = None) -> ClusterRun:
        """Cluster the current window and replace the stored clusters.

        Clusters are recomputed wholesale rather than incrementally updated. A
        reading leaving the window can dissolve a cluster, and an incremental
        update that failed to notice would leave a published hotspot standing on
        evidence that has expired.
        """
        params = parameters or ClusteringParameters(
            eps_metres=settings.dbscan_eps_metres,
            min_samples=settings.dbscan_min_samples,
            time_window_days=settings.cluster_time_window_days,
            min_independent_devices=settings.cluster_min_independent_devices,
        )
        started = time.perf_counter()
        readings = self.load_confirmed_readings(window_days=params.time_window_days)
        clusters = cluster_readings(readings, params)

        run = ClusterRun(
            eps_metres=params.eps_metres,
            min_samples=params.min_samples,
            time_window_days=params.time_window_days,
            min_independent_devices=params.min_independent_devices,
            input_reading_count=len(readings),
            cluster_count=len(clusters),
            publishable_count=sum(1 for c in clusters if c.is_publishable),
            parameters={
                "min_temporal_spread_minutes": params.min_temporal_spread_minutes,
            },
        )
        self.db.add(run)
        self.db.flush()

        # Clear previous results, then write this run's.
        self.db.query(ClusterMember).delete(synchronize_session=False)
        self.db.query(HotspotCluster).delete(synchronize_session=False)

        from shapely.geometry import Point

        for cluster in clusters:
            row = HotspotCluster(
                run_id=run.id,
                crop=cluster.crop,
                assay=cluster.assay,
                centroid=from_shape(Point(cluster.centroid_lon, cluster.centroid_lat), srid=4326),
                radius_m=cluster.radius_m,
                ward_code=cluster.ward_code,
                ward_name=cluster.ward_name,
                district=cluster.district,
                state=cluster.state,
                window_start=cluster.window_start,
                window_end=cluster.window_end,
                member_count=cluster.member_count,
                independent_device_count=cluster.independent_device_count,
                mean_concentration=cluster.mean_concentration,
                max_concentration=cluster.max_concentration,
                exceedance_rate=cluster.exceedance_rate,
                severity=cluster.severity,
                inspection_priority=cluster.inspection_priority,
                is_publishable=cluster.is_publishable,
                suppression_reason=cluster.suppression_reason,
                is_synthetic=cluster.contains_synthetic,
            )
            self.db.add(row)
            self.db.flush()
            for member in cluster.members:
                self.db.add(
                    ClusterMember(
                        cluster_id=row.id,
                        scan_id=member.scan_id,
                        device_pseudonym=member.device_pseudonym,
                        weight=member.weight,
                    )
                )

        run.duration_ms = int((time.perf_counter() - started) * 1000)
        self.db.flush()
        log.info(
            "cluster_run_complete",
            run_id=str(run.id),
            readings=run.input_reading_count,
            clusters=run.cluster_count,
            publishable=run.publishable_count,
            duration_ms=run.duration_ms,
        )
        return run

    # --- Public view (rule 4: areas, never vendors) -----------------------
    def public_wards(self, *, window_days: int | None = None) -> list[dict]:
        """Ward-level aggregates for the public heatmap.

        Only publishable clusters contribute. The point returned for each ward
        is the ward's own centre from the ward table -- never a cluster centroid,
        which would point at where the readings actually were.
        """
        days = window_days or settings.cluster_time_window_days
        clusters = list(
            self.db.scalars(
                select(HotspotCluster).where(
                    HotspotCluster.is_publishable.is_(True),
                    HotspotCluster.ward_code.is_not(None),
                )
            )
        )

        by_ward: dict[str, dict] = {}
        for cluster in clusters:
            entry = by_ward.setdefault(
                cluster.ward_code,
                {
                    "ward_code": cluster.ward_code,
                    "ward_name": cluster.ward_name,
                    "district": cluster.district,
                    "state": cluster.state,
                    "confirmed_reading_count": 0,
                    "cluster_count": 0,
                    "highest_severity": ClusterSeverity.ADVISORY,
                    "crops": set(),
                    "last_reading_at": None,
                    "contains_demo_data": False,
                    "independent_device_count": 0,
                },
            )
            entry["confirmed_reading_count"] += cluster.member_count
            entry["cluster_count"] += 1
            entry["independent_device_count"] += cluster.independent_device_count
            if cluster.crop:
                entry["crops"].add(cluster.crop)
            if SEVERITY_ORDER[ClusterSeverity(cluster.severity)] > SEVERITY_ORDER[
                ClusterSeverity(entry["highest_severity"])
            ]:
                entry["highest_severity"] = cluster.severity
            if entry["last_reading_at"] is None or cluster.window_end > entry["last_reading_at"]:
                entry["last_reading_at"] = cluster.window_end
            entry["contains_demo_data"] = entry["contains_demo_data"] or cluster.is_synthetic

        results = []
        for entry in by_ward.values():
            ward = ward_by_code(entry["ward_code"]) or {}
            entry["crops"] = sorted(entry["crops"])
            entry["representative_point"] = {
                "latitude": ward.get("lat", 0.0),
                "longitude": ward.get("lon", 0.0),
            }
            entry["window_days"] = days
            results.append(entry)

        results.sort(key=lambda e: -e["confirmed_reading_count"])
        return results

    # --- Officer view (rule 5: authenticated officials only) --------------
    def officer_clusters(
        self, *, include_suppressed: bool = True, limit: int = 100
    ) -> list[HotspotCluster]:
        statement = select(HotspotCluster).order_by(HotspotCluster.inspection_priority.desc())
        if not include_suppressed:
            statement = statement.where(HotspotCluster.is_publishable.is_(True))
        return list(self.db.scalars(statement.limit(limit)))

    def cluster_members(self, cluster_id: uuid.UUID) -> list[tuple[Scan, ChemicalReading | None]]:
        member_ids = select(ClusterMember.scan_id).where(ClusterMember.cluster_id == cluster_id)
        rows = self.db.execute(
            select(Scan, ChemicalReading)
            .outerjoin(
                ChemicalReading,
                (ChemicalReading.scan_id == Scan.id) & ChemicalReading.accepted.is_(True),
            )
            .where(Scan.id.in_(member_ids))
            .order_by(Scan.captured_at.desc())
        ).all()
        return [(row[0], row[1]) for row in rows]

    def cluster_merchants(self, cluster_id: uuid.UUID) -> list[dict]:
        """Resolve merchant references for a cluster.

        This is the one place the analytics side reaches into the identity
        schema, and it is only ever called from an officer-authenticated,
        audited route.
        """
        member_ids = select(ClusterMember.scan_id).where(ClusterMember.cluster_id == cluster_id)
        counts = dict(
            self.db.execute(
                select(Scan.merchant_ref, func.count())
                .where(Scan.id.in_(member_ids), Scan.merchant_ref.is_not(None))
                .group_by(Scan.merchant_ref)
            ).all()
        )
        if not counts:
            return []

        merchants = self.db.scalars(
            select(Merchant).where(Merchant.merchant_ref.in_(list(counts)))
        )
        return [
            {
                "merchant_ref": m.merchant_ref,
                "name": m.name,
                "stall_identifier": m.stall_identifier,
                "market_name": m.market_name,
                "address_line": m.address_line,
                "ward_code": m.ward_code,
                "district": m.district,
                "fssai_licence_no": m.fssai_licence_no,
                "location": (
                    {"latitude": m.latitude, "longitude": m.longitude}
                    if m.latitude is not None and m.longitude is not None
                    else None
                ),
                "confirmed_reading_count": counts.get(m.merchant_ref, 0),
                "is_synthetic": m.is_synthetic,
            }
            for m in merchants
        ]

    # --- Stats ------------------------------------------------------------
    def stats(self, *, window_days: int | None = None) -> dict:
        days = window_days or settings.cluster_time_window_days
        since = datetime.now(UTC) - timedelta(days=days)

        confirmed = self.db.scalar(
            select(func.count())
            .select_from(Scan)
            .where(
                Scan.evidence_grade == EvidenceGrade.CONFIRMATORY,
                Scan.shared_with_watch.is_(True),
                Scan.captured_at >= since,
            )
        )
        published = self.db.scalar(
            select(func.count())
            .select_from(HotspotCluster)
            .where(HotspotCluster.is_publishable.is_(True))
        )
        suppressed = self.db.scalar(
            select(func.count())
            .select_from(HotspotCluster)
            .where(HotspotCluster.is_publishable.is_(False))
        )
        wards = self.db.scalar(
            select(func.count(func.distinct(HotspotCluster.ward_code))).where(
                HotspotCluster.is_publishable.is_(True)
            )
        )
        complaints = self.db.scalar(select(func.count()).select_from(Complaint))
        demo = self.db.scalar(
            select(func.count()).select_from(Scan).where(Scan.is_synthetic.is_(True))
        )

        return {
            "confirmed_readings": int(confirmed or 0),
            "published_clusters": int(published or 0),
            "suppressed_clusters": int(suppressed or 0),
            "wards_covered": int(wards or 0),
            "complaints_prepared": int(complaints or 0),
            "window_days": days,
            "demo_data_included": bool(demo),
        }
