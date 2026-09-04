"""Complaint workflow: assemble evidence, render the package, record filing.

The evidence rule is enforced at the top of `create`: a complaint can only be
built from a scan graded CONFIRMATORY, which requires an accepted colorimetric
reading. There is no override.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ComplaintStatus
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models.identity import Merchant, User
from app.models.scan import ChemicalReading, Scan, ScanImage
from app.models.trace import ProduceLot
from app.models.watch import Complaint
from app.services import evidence as evidence_rules
from app.services.colorimetry.calibration import get_calibration
from app.services.complaints.generator import (
    ComplaintEvidence,
    EvidenceItem,
    build_package,
    new_reference_code,
)
from app.services.storage import object_store

log = get_logger("satva.complaints.service")

FILING_INSTRUCTIONS = (
    "SATVA has prepared this evidence package. It has NOT been submitted. Open the "
    f"{settings.fssai_connect_app} app or visit {settings.fssai_portal_url}, raise the "
    "complaint yourself, attach the PDF, and quote the SATVA reference so the two records "
    "can be matched."
)

SUBMISSION_NOTICE = (
    "SATVA does not file complaints on anyone's behalf and has no authority to determine "
    "that food is unsafe or that an offence has occurred. Escalation is routed through the "
    "statutory FSSAI mechanism rather than replacing it."
)


class ComplaintService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, complaint_id: uuid.UUID) -> Complaint:
        complaint = self.db.get(Complaint, complaint_id)
        if complaint is None:
            raise NotFoundError("No such complaint.")
        return complaint

    def create(self, scan: Scan, payload, *, user: User | None = None) -> Complaint:
        """Build a complaint package from a confirmed scan."""
        # The gate. Raises EvidenceRuleViolation for a screening-only scan.
        evidence_rules.require_evidence_grade(scan, "attach_to_complaint")

        reading = evidence_rules.accepted_reading_for(self.db, scan.id)
        if reading is None:
            raise ConflictError(
                "This scan has no accepted chemical reading, so there is nothing to escalate."
            )

        existing = self.db.scalar(
            select(Complaint).where(
                Complaint.scan_id == scan.id,
                Complaint.status.not_in([ComplaintStatus.WITHDRAWN, ComplaintStatus.CLOSED]),
            )
        )
        if existing is not None:
            raise ConflictError(
                "A complaint package already exists for this scan.",
                details={
                    "complaint_id": str(existing.id),
                    "reference_code": existing.reference_code,
                },
            )

        evidence = self._assemble(scan, reading, payload, user=user)
        pdf, digest = build_package(evidence)

        key = f"complaints/{evidence.reference_code}/{evidence.reference_code}.pdf"
        object_store.put_document(key, pdf, "application/pdf")

        complaint = Complaint(
            reference_code=evidence.reference_code,
            scan_id=scan.id,
            reading_id=reading.id,
            status=ComplaintStatus.PACKAGE_READY,
            complainant_provided=bool(payload.complainant_name or payload.complainant_phone),
            crop=scan.crop,
            merchant_ref=scan.merchant_ref,
            ward_code=scan.ward_code,
            district=scan.district,
            state=scan.state,
            incident_at=scan.captured_at,
            evidence_snapshot=evidence.to_snapshot(),
            package_object_key=key,
            package_sha256=digest,
            is_synthetic=scan.is_synthetic,
        )
        self.db.add(complaint)
        self.db.flush()

        # Images attached to a live complaint get the extended retention window
        # (spec 15.3: kept only as long as the complaint remains live).
        deadline = evidence_rules.retention_deadline(has_live_complaint=True)
        for image in self.db.scalars(select(ScanImage).where(ScanImage.scan_id == scan.id)):
            image.retain_until = deadline
        self.db.flush()

        log.info(
            "complaint_created",
            complaint_id=str(complaint.id),
            reference_code=complaint.reference_code,
            scan_id=str(scan.id),
        )
        return complaint

    def _assemble(
        self, scan: Scan, reading: ChemicalReading, payload, *, user: User | None
    ) -> ComplaintEvidence:
        series = get_calibration(reading.assay)
        point = to_shape(scan.location) if scan.location is not None else None

        merchant: Merchant | None = None
        if scan.merchant_ref:
            merchant = self.db.scalar(
                select(Merchant).where(Merchant.merchant_ref == scan.merchant_ref)
            )

        images: list[EvidenceItem] = []
        if payload.include_images:
            for image in self.db.scalars(
                select(ScanImage)
                .where(ScanImage.scan_id == scan.id, ScanImage.deleted_at.is_(None))
                .order_by(ScanImage.created_at)
            ):
                data = None
                try:
                    data = object_store.get(image.object_key)
                except Exception as exc:  # noqa: BLE001
                    # A missing object must not block the citizen from filing;
                    # the digest is still recorded so the image can be supplied
                    # separately and matched.
                    log.warning(
                        "complaint_image_unavailable",
                        object_key=image.object_key,
                        error=str(exc)[:120],
                    )
                images.append(
                    EvidenceItem(
                        kind=image.kind,
                        sha256=image.sha256,
                        captured_at=image.created_at,
                        object_key=image.object_key,
                        content_type=image.content_type,
                        byte_size=image.byte_size,
                        image_bytes=data,
                    )
                )

        lot_code = None
        trace_verified = None
        if scan.lot_id:
            lot = self.db.get(ProduceLot, scan.lot_id)
            if lot is not None:
                from app.services.trace.service import TraceService

                lot_code = lot.lot_code
                trace_verified = TraceService(self.db).verify_ancestry(lot.id)["valid"]

        return ComplaintEvidence(
            reference_code=new_reference_code(),
            generated_at=datetime.now(UTC),
            scan_id=str(scan.id),
            reading_id=str(reading.id),
            assay=reading.assay,
            assay_display_name=series.display_name if series else reading.assay,
            calibration_id=reading.calibration_id,
            concentration_value=float(reading.concentration_value),
            concentration_unit=reading.concentration_unit or "",
            ci_low=float(reading.ci_low) if reading.ci_low is not None else 0.0,
            ci_high=float(reading.ci_high) if reading.ci_high is not None else 0.0,
            band_label=reading.band_label or "",
            exceeds_action_threshold=reading.exceeds_action_threshold,
            action_threshold=series.action_threshold if series else 0.0,
            is_lab_validated=series.is_lab_validated if series else False,
            measurement_quality=reading.quality_report or {},
            crop=scan.crop,
            cultivar=scan.cultivar,
            incident_at=scan.captured_at,
            latitude=point.y if point else None,
            longitude=point.x if point else None,
            location_accuracy_m=scan.location_accuracy_m,
            ward_code=scan.ward_code,
            ward_name=scan.ward_name,
            district=scan.district,
            state=scan.state,
            place_description=payload.place_description,
            merchant_name=payload.merchant_name or (merchant.name if merchant else None),
            merchant_address=payload.merchant_address
            or (merchant.address_line if merchant else None),
            merchant_fssai_licence=payload.merchant_fssai_licence
            or (merchant.fssai_licence_no if merchant else None),
            complainant_name=payload.complainant_name,
            complainant_phone=payload.complainant_phone,
            complainant_email=payload.complainant_email,
            vision_anomaly_score=scan.vision_anomaly_score,
            vision_model_id=scan.vision_model_id,
            lot_code=lot_code,
            trace_verified=trace_verified,
            images=images,
            narrative=payload.narrative,
        )

    def download_url(self, complaint: Complaint) -> str | None:
        if not complaint.package_object_key:
            return None
        return object_store.presigned_url(complaint.package_object_key)

    def mark_submitted(self, complaint: Complaint, payload) -> Complaint:
        """Record that the citizen filed the complaint themselves.

        SATVA cannot and does not verify this. The status name says so
        explicitly: SUBMITTED_BY_USER, not SUBMITTED.
        """
        if complaint.status in (ComplaintStatus.WITHDRAWN, ComplaintStatus.CLOSED):
            raise ConflictError("This complaint is no longer open.")
        complaint.status = ComplaintStatus.SUBMITTED_BY_USER
        complaint.submission_channel = payload.submission_channel
        complaint.fssai_reference = payload.fssai_reference
        complaint.submitted_by_user_at = payload.submitted_at or datetime.now(UTC)
        self.db.flush()
        log.info(
            "complaint_marked_submitted",
            complaint_id=str(complaint.id),
            channel=payload.submission_channel,
        )
        return complaint

    def withdraw(self, complaint: Complaint) -> Complaint:
        complaint.status = ComplaintStatus.WITHDRAWN
        # Withdrawing releases the extended image retention: the images were
        # kept because a complaint was live, and it no longer is.
        deadline = evidence_rules.retention_deadline(has_live_complaint=False)
        for image in self.db.scalars(
            select(ScanImage).where(ScanImage.scan_id == complaint.scan_id)
        ):
            image.retain_until = deadline
        self.db.flush()
        return complaint
