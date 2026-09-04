"""/scans -- create, sync, inspect and share screening records."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import (
    Principal,
    get_current_principal,
    get_device,
    scan_rate_limit,
    write_audit,
)
from app.core.constants import (
    DISCLAIMER_LONG,
    DISCLAIMER_SHORT,
    SUPPORTED_CROPS,
    UNVALIDATED_CROPS,
)
from app.core.errors import SatvaError
from app.db.session import get_db
from app.models.identity import Device
from app.models.scan import Scan
from app.schemas.common import Disclaimer, Page
from app.schemas.scan import (
    CropCatalogueOut,
    EvidenceCapabilities,
    ScanCreate,
    ScanOut,
    ScanSyncRequest,
    ScanSyncResponse,
    ScanSyncResult,
    SupportedCropOut,
)
from app.services import evidence as evidence_rules
from app.services.scan_service import ScanService
from app.services.storage import object_store

router = APIRouter(prefix="/scans", tags=["scans"])

DISCLAIMER = Disclaimer(short=DISCLAIMER_SHORT, long=DISCLAIMER_LONG)


def _to_out(service: ScanService, scan: Scan) -> ScanOut:
    return ScanOut(
        **{
            column: getattr(scan, column)
            for column in (
                "id", "client_scan_uid", "crop", "cultivar", "vision_anomaly_score",
                "vision_verdict", "vision_model_id", "vision_model_kind", "ripeness_index",
                "evidence_grade", "captured_at", "captured_offline", "shared_with_watch",
                "ward_code", "ward_name", "district", "merchant_ref", "lot_id",
                "duplicate_of_scan_id", "is_synthetic", "created_at",
            )
        },
        readings=[r for r in scan.readings],
        capabilities=EvidenceCapabilities(**service.capabilities(scan)),
        disclaimer=DISCLAIMER,
    )


@router.get(
    "/crops",
    response_model=CropCatalogueOut,
    summary="Crops the screening model will and will not score",
    description=(
        "The model is calibrated per crop and declines anything outside its validated set "
        "rather than guessing (specification 6.4). The refused list is returned so the app "
        "can explain the refusal instead of silently omitting a crop."
    ),
)
def crop_catalogue() -> CropCatalogueOut:
    return CropCatalogueOut(
        supported=[
            SupportedCropOut(
                key=key,
                label=entry["label"],
                cultivars=entry["cultivars"],
                assays=[str(a) for a in entry["assays"]],
                calibrated=entry["calibrated"],
            )
            for key, entry in SUPPORTED_CROPS.items()
        ],
        refused=sorted(UNVALIDATED_CROPS),
        note=(
            "SATVA refuses to score a crop it has not validated under controlled conditions. "
            "A refusal is not a failure: scoring an unvalidated cultivar would be a guess "
            "presented as a measurement."
        ),
    )


@router.post(
    "",
    response_model=ScanOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(scan_rate_limit)],
    summary="Record a screening scan",
    description=(
        "Records the result of an on-device visual screen. The produce photograph itself is "
        "NOT uploaded here: Layer A runs entirely on the handset and only the score and a "
        "small saliency summary are transmitted. Idempotent on client_scan_uid, so the "
        "offline queue can retry safely."
    ),
)
def create_scan(
    payload: ScanCreate,
    principal: Principal = Depends(get_current_principal),
    device: Device | None = Depends(get_device),
    db: Session = Depends(get_db),
) -> ScanOut:
    service = ScanService(db)
    scan, created = service.create(payload, user=principal.user, device=device)
    db.commit()
    db.refresh(scan)
    if not created:
        # A repeat of an already-synced scan is a success, not an error: the
        # queue's contract is at-least-once delivery.
        pass
    return _to_out(service, scan)


@router.post(
    "/sync",
    response_model=ScanSyncResponse,
    dependencies=[Depends(scan_rate_limit)],
    summary="Upload a batch of scans queued while offline",
    description=(
        "Each entry is processed independently: one bad record does not fail the batch, so a "
        "handset that has been offline for a week never gets stuck behind a single "
        "unsupported crop."
    ),
)
def sync_scans(
    payload: ScanSyncRequest,
    principal: Principal = Depends(get_current_principal),
    device: Device | None = Depends(get_device),
    db: Session = Depends(get_db),
) -> ScanSyncResponse:
    service = ScanService(db)
    results: list[ScanSyncResult] = []

    for item in payload.scans:
        try:
            scan, created = service.create(item, user=principal.user, device=device)
            # Commit per item so an accepted scan is durable even if a later
            # entry in the batch fails.
            db.commit()
            results.append(
                ScanSyncResult(
                    client_scan_uid=item.client_scan_uid,
                    status="created" if created else "already_synced",
                    scan_id=scan.id,
                )
            )
        except SatvaError as exc:
            db.rollback()
            results.append(
                ScanSyncResult(
                    client_scan_uid=item.client_scan_uid,
                    status="rejected",
                    error_code=exc.code,
                    error_message=exc.message,
                )
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            results.append(
                ScanSyncResult(
                    client_scan_uid=item.client_scan_uid,
                    status="rejected",
                    error_code="internal_error",
                    error_message=str(exc)[:200],
                )
            )

    return ScanSyncResponse(
        results=results,
        created=sum(1 for r in results if r.status == "created"),
        already_synced=sum(1 for r in results if r.status == "already_synced"),
        rejected=sum(1 for r in results if r.status == "rejected"),
    )


@router.get("", response_model=Page[ScanOut], summary="Your own scan history")
def list_scans(
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Page[ScanOut]:
    service = ScanService(db)
    scans, total = service.list_for_user(
        principal.user, limit=min(limit, 200), offset=max(offset, 0)
    )
    return Page(
        items=[_to_out(service, s) for s in scans], total=total, limit=limit, offset=offset
    )


@router.get("/{scan_id}", response_model=ScanOut, summary="One of your scans")
def get_scan(
    scan_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ScanOut:
    service = ScanService(db)
    return _to_out(service, service.get_owned(scan_id, principal.user))


@router.post(
    "/{scan_id}/images",
    response_model=dict,
    summary="Attach an evidence image to a scan",
    description=(
        "Only used for the confirmatory strip photograph and, optionally, the produce image "
        "when the user is preparing a complaint. Uploads are validated by decoding the file, "
        "not by trusting its declared content type, and are retained only as long as an "
        "associated complaint remains live."
    ),
)
async def upload_image(
    scan_id: uuid.UUID,
    request: Request,
    kind: str = Form(..., pattern="^(produce|strip|reference)$"),
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.scan import ScanImage

    service = ScanService(db)
    scan = service.get_owned(scan_id, principal.user)

    data = await file.read()
    stored = object_store.put_image(
        scan.id, kind, data, declared_type=file.content_type
    )

    image = ScanImage(
        scan_id=scan.id,
        kind=kind,
        object_key=stored.object_key,
        content_type=stored.content_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
        retain_until=evidence_rules.retention_deadline(),
    )
    db.add(image)
    db.flush()

    # The strip photograph is what makes a device-computed reading checkable,
    # so this is where a scan actually earns its confirmatory grade: the
    # handset attaches the reading first and uploads the image afterwards.
    if kind == "strip":
        pending = evidence_rules.accepted_reading_for(db, scan.id)
        if pending is not None:
            evidence_rules.promote_to_confirmatory(db, scan, pending)

    write_audit(
        db,
        action="scan.image_uploaded",
        principal=principal,
        object_type="scan",
        object_id=str(scan.id),
        request=request,
        context={"kind": kind, "sha256": stored.sha256},
    )
    db.commit()
    return {
        "id": str(image.id),
        "kind": kind,
        "sha256": stored.sha256,
        "byte_size": stored.byte_size,
        "retain_until": image.retain_until.isoformat() if image.retain_until else None,
    }


@router.post(
    "/{scan_id}/share",
    response_model=ScanOut,
    summary="Share or unshare a confirmed scan with SATVA Watch",
    description=(
        "Only a scan with a confirmatory chemical reading can be shared. Attempting to share "
        "a visually screened scan returns 409 evidence_rule_violation."
    ),
)
def share_scan(
    scan_id: uuid.UUID,
    shared: bool = True,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ScanOut:
    service = ScanService(db)
    scan = service.get_owned(scan_id, principal.user)
    service.set_watch_sharing(scan, shared)
    db.commit()
    db.refresh(scan)
    return _to_out(service, scan)
