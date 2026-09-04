"""/complaints -- prepare FSSAI evidence packages.

SATVA prepares; the citizen files. No route in this module submits anything to
any external system, and the response bodies say so in words the client renders
verbatim.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_current_principal, write_audit
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models.identity import ScanOwnership
from app.models.watch import Complaint
from app.schemas.watch import ComplaintCreate, ComplaintMarkSubmitted, ComplaintOut
from app.services.complaints.service import (
    FILING_INSTRUCTIONS,
    SUBMISSION_NOTICE,
    ComplaintService,
)
from app.services.scan_service import ScanService
from app.services.storage import object_store

router = APIRouter(prefix="/complaints", tags=["complaints"])


def _complaint_out(complaint: Complaint, *, include_download: bool = True) -> ComplaintOut:
    return ComplaintOut(
        id=complaint.id,
        reference_code=complaint.reference_code,
        scan_id=complaint.scan_id,
        reading_id=complaint.reading_id,
        status=complaint.status,
        crop=complaint.crop,
        ward_code=complaint.ward_code,
        district=complaint.district,
        state=complaint.state,
        incident_at=complaint.incident_at,
        package_sha256=complaint.package_sha256,
        submission_channel=complaint.submission_channel,
        fssai_reference=complaint.fssai_reference,
        submitted_by_user_at=complaint.submitted_by_user_at,
        is_synthetic=complaint.is_synthetic,
        created_at=complaint.created_at,
        download_url=(
            f"/api/v1/complaints/{complaint.id}/package" if include_download else None
        ),
        filing_instructions=FILING_INSTRUCTIONS,
        submission_notice=SUBMISSION_NOTICE,
    )


def _owned_complaint(
    complaint_id: uuid.UUID, principal: Principal, db: Session
) -> Complaint:
    service = ComplaintService(db)
    complaint = service.get(complaint_id)
    ownership = db.scalar(select(ScanOwnership).where(ScanOwnership.scan_id == complaint.scan_id))
    is_owner = ownership is not None and ownership.user_id == principal.user.id
    if not is_owner and principal.role not in ("officer", "admin"):
        raise NotFoundError("No such complaint.")
    return complaint


@router.post(
    "",
    response_model=ComplaintOut,
    status_code=status.HTTP_201_CREATED,
    summary="Prepare an FSSAI complaint package",
    description=(
        "Builds a structured evidence package: the confirmatory strip reading with its "
        "confidence interval, the photographs with SHA-256 digests, GPS coordinates, "
        "timestamp, commodity and the SATVA scan reference. Requires a scan with an accepted "
        "chemical reading; a visually screened scan returns 409 evidence_rule_violation.\n\n"
        "**This does not submit anything.** SATVA has no programmatic route into the FSSAI "
        "complaint channel, and inventing one would leave a citizen believing a complaint was "
        "filed when it was not. The generated PDF carries filing instructions."
    ),
)
def create_complaint(
    payload: ComplaintCreate,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ComplaintOut:
    scan = ScanService(db).get_owned(payload.scan_id, principal.user)
    complaint = ComplaintService(db).create(scan, payload, user=principal.user)
    write_audit(
        db,
        action="complaint.package_generated",
        principal=principal,
        object_type="complaint",
        object_id=str(complaint.id),
        request=request,
        context={"scan_id": str(scan.id), "reference_code": complaint.reference_code},
    )
    db.commit()
    db.refresh(complaint)
    return _complaint_out(complaint)


@router.get("", response_model=list[ComplaintOut], summary="Your complaint packages")
def list_complaints(
    principal: Principal = Depends(get_current_principal), db: Session = Depends(get_db)
) -> list[ComplaintOut]:
    owned_scans = select(ScanOwnership.scan_id).where(
        ScanOwnership.user_id == principal.user.id
    )
    complaints = db.scalars(
        select(Complaint)
        .where(Complaint.scan_id.in_(owned_scans))
        .order_by(Complaint.created_at.desc())
    )
    return [_complaint_out(c) for c in complaints]


@router.get("/{complaint_id}", response_model=ComplaintOut, summary="One complaint package")
def get_complaint(
    complaint_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ComplaintOut:
    return _complaint_out(_owned_complaint(complaint_id, principal, db))


@router.get(
    "/{complaint_id}/package",
    summary="Download the complaint PDF",
    response_class=StreamingResponse,
)
def download_package(
    complaint_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    import io

    complaint = _owned_complaint(complaint_id, principal, db)
    if not complaint.package_object_key:
        raise NotFoundError("No package has been generated for this complaint.")

    data = object_store.get(complaint.package_object_key)
    write_audit(
        db,
        action="complaint.package_downloaded",
        principal=principal,
        object_type="complaint",
        object_id=str(complaint.id),
        request=request,
    )
    db.commit()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{complaint.reference_code}.pdf"'
            )
        },
    )


@router.post(
    "/{complaint_id}/mark-submitted",
    response_model=ComplaintOut,
    summary="Record that you filed this complaint yourself",
    description=(
        "SATVA cannot verify a filing, so this records the citizen's own statement. The "
        "status is SUBMITTED_BY_USER rather than SUBMITTED for exactly that reason."
    ),
)
def mark_submitted(
    complaint_id: uuid.UUID,
    payload: ComplaintMarkSubmitted,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ComplaintOut:
    complaint = _owned_complaint(complaint_id, principal, db)
    ComplaintService(db).mark_submitted(complaint, payload)
    write_audit(
        db,
        action="complaint.marked_submitted",
        principal=principal,
        object_type="complaint",
        object_id=str(complaint.id),
        request=request,
        context={"channel": payload.submission_channel},
    )
    db.commit()
    db.refresh(complaint)
    return _complaint_out(complaint)


@router.post(
    "/{complaint_id}/withdraw", response_model=ComplaintOut, summary="Withdraw a complaint"
)
def withdraw(
    complaint_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ComplaintOut:
    complaint = _owned_complaint(complaint_id, principal, db)
    ComplaintService(db).withdraw(complaint)
    write_audit(
        db,
        action="complaint.withdrawn",
        principal=principal,
        object_type="complaint",
        object_id=str(complaint.id),
        request=request,
    )
    db.commit()
    db.refresh(complaint)
    return _complaint_out(complaint)
