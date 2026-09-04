"""/officers -- the authenticated Food Safety Officer surface.

This is the only place in SATVA where vendor-level detail is exposed
(non-negotiable rule 5). Every route:

* requires the officer role, checked server-side against the database rather
  than against a token claim, and
* writes an audit record before the handler runs, via `AuditedOfficerAccess`.

The two are bound together in one dependency so an endpoint cannot be added
that authorises correctly but forgets the audit trail.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuditedOfficerAccess, Principal, require_officer, write_audit
from app.core.constants import ClusterSeverity, ComplaintStatus
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.watch import Complaint, HotspotCluster
from app.schemas.common import GeoPoint
from app.schemas.watch import (
    ClusterMemberOut,
    ClusterRunOut,
    ComplaintOut,
    MerchantDetailOut,
    OfficerHotspotDetail,
    OfficerHotspotOut,
)
from app.services.watch.service import WatchService

router = APIRouter(prefix="/officers", tags=["officers"])


def _cluster_out(cluster: HotspotCluster) -> OfficerHotspotOut:
    point = to_shape(cluster.centroid)
    return OfficerHotspotOut(
        id=cluster.id,
        centroid=GeoPoint(latitude=point.y, longitude=point.x),
        radius_m=cluster.radius_m,
        ward_code=cluster.ward_code,
        ward_name=cluster.ward_name,
        district=cluster.district,
        state=cluster.state,
        crop=cluster.crop,
        assay=cluster.assay,
        window_start=cluster.window_start,
        window_end=cluster.window_end,
        member_count=cluster.member_count,
        independent_device_count=cluster.independent_device_count,
        mean_concentration=float(cluster.mean_concentration)
        if cluster.mean_concentration is not None
        else None,
        max_concentration=float(cluster.max_concentration)
        if cluster.max_concentration is not None
        else None,
        exceedance_rate=cluster.exceedance_rate,
        severity=ClusterSeverity(cluster.severity),
        inspection_priority=cluster.inspection_priority,
        is_publishable=cluster.is_publishable,
        suppression_reason=cluster.suppression_reason,
        contains_demo_data=cluster.is_synthetic,
    )


@router.get(
    "/worklist",
    response_model=list[OfficerHotspotOut],
    summary="Ranked inspection worklist",
    description=(
        "Clusters ordered by inspection priority. Includes clusters that are NOT publishable "
        "to the public map: an officer should be able to see a pattern that has not yet met "
        "the corroboration threshold, together with the reason it is being withheld."
    ),
)
def worklist(
    request: Request,
    include_suppressed: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(
        AuditedOfficerAccess("officer.worklist_viewed", "hotspot_cluster")
    ),
    db: Session = Depends(get_db),
) -> list[OfficerHotspotOut]:
    clusters = WatchService(db).officer_clusters(
        include_suppressed=include_suppressed, limit=limit
    )
    db.commit()
    return [_cluster_out(c) for c in clusters]


@router.get(
    "/clusters/{cluster_id}",
    response_model=OfficerHotspotDetail,
    summary="Full cluster detail, including vendor-level information",
    description=(
        "Returns the member readings and any identified food business operators. This is the "
        "only endpoint in SATVA that resolves a merchant reference to a name and address. "
        "Access is recorded in the audit log."
    ),
)
def cluster_detail(
    cluster_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(
        AuditedOfficerAccess("officer.vendor_detail_viewed", "hotspot_cluster")
    ),
    db: Session = Depends(get_db),
) -> OfficerHotspotDetail:
    service = WatchService(db)
    cluster = db.get(HotspotCluster, cluster_id)
    if cluster is None:
        raise NotFoundError("No such cluster.")

    members = []
    for scan, reading in service.cluster_members(cluster_id):
        point = to_shape(scan.location) if scan.location is not None else None
        members.append(
            ClusterMemberOut(
                scan_id=scan.id,
                captured_at=scan.captured_at,
                concentration_value=float(reading.concentration_value)
                if reading and reading.concentration_value is not None
                else None,
                concentration_unit=reading.concentration_unit if reading else None,
                exceeds_action_threshold=bool(reading and reading.exceeds_action_threshold),
                crop=scan.crop,
                device_pseudonym=scan.device_pseudonym,
                location=GeoPoint(latitude=point.y, longitude=point.x) if point else None,
                merchant_ref=scan.merchant_ref,
                is_synthetic=scan.is_synthetic,
            )
        )

    merchants = [MerchantDetailOut(**m) for m in service.cluster_merchants(cluster_id)]
    complaint_count = len(
        list(db.scalars(select(Complaint).where(Complaint.cluster_id == cluster_id)))
    )

    base = _cluster_out(cluster)
    db.commit()
    return OfficerHotspotDetail(
        **base.model_dump(),
        members=members,
        merchants=merchants,
        complaint_count=complaint_count,
    )


@router.get(
    "/complaints",
    response_model=list[ComplaintOut],
    summary="Complaint packages prepared by citizens",
)
def officer_complaints(
    request: Request,
    status_filter: ComplaintStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(
        AuditedOfficerAccess("officer.complaints_viewed", "complaint")
    ),
    db: Session = Depends(get_db),
) -> list[ComplaintOut]:
    from app.api.v1.complaints import _complaint_out

    statement = select(Complaint).order_by(Complaint.created_at.desc()).limit(limit)
    if status_filter:
        statement = statement.where(Complaint.status == status_filter)
    complaints = list(db.scalars(statement))
    db.commit()
    return [_complaint_out(c, include_download=False) for c in complaints]


@router.post(
    "/clusters/recompute",
    response_model=ClusterRunOut,
    summary="Re-run the clustering job now",
    description=(
        "Normally runs on a schedule through Celery. Exposed here so an officer or a "
        "demonstration can force a refresh after new readings arrive."
    ),
)
def recompute_clusters(
    request: Request,
    principal: Principal = Depends(require_officer),
    db: Session = Depends(get_db),
) -> ClusterRunOut:
    run = WatchService(db).run_clustering()
    write_audit(
        db,
        action="officer.clustering_triggered",
        principal=principal,
        object_type="cluster_run",
        object_id=str(run.id),
        request=request,
    )
    db.commit()
    return ClusterRunOut.model_validate(run)


@router.get(
    "/audit",
    summary="Recent audit entries for the signed-in officer",
    description=(
        "Officers can see their own access record. Making the audit trail visible to the "
        "person it describes is part of what makes it a safeguard rather than surveillance."
    ),
)
def my_audit_trail(
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_officer),
    db: Session = Depends(get_db),
) -> list[dict]:
    from app.models.identity import AuditLog

    entries = db.scalars(
        select(AuditLog)
        .where(AuditLog.actor_user_id == principal.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "action": e.action,
            "object_type": e.object_type,
            "object_id": e.object_id,
            "created_at": e.created_at.isoformat(),
        }
        for e in entries
    ]
