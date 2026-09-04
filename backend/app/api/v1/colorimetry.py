"""/colorimetry -- Layer B, the only source of evidence-grade results.

Two paths exist, and both matter:

``POST /colorimetry/analyse``
    Server-side analysis of an uploaded strip photograph. Used when the handset
    cannot run the pipeline, and -- more importantly -- to let a reading be
    independently recomputed from the stored image. A number that can only be
    reproduced by the device that produced it is not much of an evidence claim.

``POST /colorimetry/scans/{scan_id}/readings``
    Records a result the handset computed offline. This is the normal path:
    the pipeline runs on-device so it works with no connectivity.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_current_principal, scan_rate_limit, write_audit
from app.core.constants import DISCLAIMER_LONG, DISCLAIMER_SHORT, ReagentAssay
from app.db.session import get_db
from app.schemas.common import Disclaimer
from app.schemas.scan import (
    ChemicalReadingOut,
    ColorimetryAnalyseResponse,
    ColorimetrySubmitRequest,
    ScanOut,
)
from app.services.colorimetry.calibration import CALIBRATIONS
from app.services.colorimetry.pipeline import read_strip
from app.services.scan_service import ScanService
from app.services.storage import object_store, validate_image

router = APIRouter(prefix="/colorimetry", tags=["colorimetry"])

DISCLAIMER = Disclaimer(short=DISCLAIMER_SHORT, long=DISCLAIMER_LONG)


@router.get(
    "/assays",
    summary="Registered reagent assays and their calibration series",
    description=(
        "The mobile client downloads this table so it can run the pipeline offline with "
        "exactly the calibration the server uses. `is_lab_validated` is false on every series "
        "in this build: the calibrations are provisional engineering series pending "
        "validation against a NABL-accredited laboratory."
    ),
)
def list_assays() -> dict:
    return {
        "assays": [
            {
                "assay": str(assay),
                "calibration_id": series.calibration_id,
                "display_name": series.display_name,
                "matrix": series.matrix_description,
                "unit": series.unit,
                "action_threshold": series.action_threshold,
                "action_threshold_rationale": series.action_threshold_rationale,
                "is_lab_validated": series.is_lab_validated,
                "supported_crops": list(series.supported_crops),
                "notes": series.notes,
                "stops": [
                    {"concentration": s.concentration, "lab": list(s.lab), "label": s.label}
                    for s in series.stops
                ],
                "reagent_repeatability_de": series.reagent_repeatability_de,
                "max_off_path_de": series.max_off_path_de,
            }
            for assay, series in CALIBRATIONS.items()
        ],
        "pipeline_version": "1.0.0",
        "reference_card": {"card_id": "satva-refcard", "version": "v1"},
    }


@router.post(
    "/analyse",
    response_model=ColorimetryAnalyseResponse,
    dependencies=[Depends(scan_rate_limit)],
    summary="Analyse a strip photograph server-side",
    description=(
        "Runs the full ten-step pipeline: detect the reference card, validate exposure and "
        "lighting, fit a colour correction, isolate the strip, convert to CIE L*a*b*, compute "
        "dE against the calibration stops, and report a value with a confidence interval. "
        "**Refuses outright** when the card is missing, exposure is out of range, the "
        "illuminant is too tinted, or the correction residual is too large -- it does not "
        "return a low-confidence number."
    ),
)
async def analyse_strip(
    assay: ReagentAssay = Form(...),
    file: UploadFile = File(...),
    _: Principal = Depends(get_current_principal),
) -> ColorimetryAnalyseResponse:
    data = await file.read()
    validate_image(data, declared_type=file.content_type)

    result = read_strip(data, assay)
    return ColorimetryAnalyseResponse(
        accepted=result.accepted,
        assay=result.assay,
        calibration_id=result.calibration_id,
        concentration_value=result.concentration,
        concentration_unit=result.unit,
        ci_low=result.ci_low,
        ci_high=result.ci_high,
        band_label=result.band_label,
        exceeds_action_threshold=result.exceeds_action_threshold,
        is_lab_validated=result.is_lab_validated,
        at_range_limit=result.at_range_limit,
        range_limit_side=result.range_limit_side,
        reject_reason=str(result.reject_reason) if result.reject_reason else None,
        reject_detail=result.reject_detail,
        strip_lab_corrected=list(result.strip_lab_corrected)
        if result.strip_lab_corrected
        else None,
        delta_e_nearest_stop=result.delta_e_nearest_stop,
        correction_residual_de=result.correction_residual_de,
        elapsed_ms=result.elapsed_ms,
        quality=result.quality,
        disclaimer=DISCLAIMER,
    )


@router.post(
    "/scans/{scan_id}/readings",
    response_model=ScanOut,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a colorimetric reading computed on the handset",
    description=(
        "An accepted reading promotes the scan to CONFIRMATORY -- the only state in which it "
        "may be shared with Watch or attached to a complaint -- once the strip photograph it "
        "was derived from has also been uploaded, so the number can be recomputed if it is "
        "disputed. Until then the reading is stored and the scan stays advisory. A refused "
        "reading grades the scan REJECTED and is still stored, because a refusal is a "
        "meaningful record: it shows SATVA declined to measure rather than guessing."
    ),
)
def attach_reading(
    scan_id: uuid.UUID,
    payload: ColorimetrySubmitRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ScanOut:
    from app.api.v1.scans import _to_out

    service = ScanService(db)
    scan = service.get_owned(scan_id, principal.user)
    reading = service.attach_reading(scan, payload, computed_on="device")

    write_audit(
        db,
        action="colorimetry.reading_attached",
        principal=principal,
        object_type="scan",
        object_id=str(scan.id),
        request=request,
        context={
            "assay": reading.assay,
            "accepted": reading.accepted,
            "reject_reason": reading.reject_reason,
            "evidence_grade": scan.evidence_grade,
        },
    )
    db.commit()
    db.refresh(scan)
    return _to_out(service, scan)


@router.post(
    "/scans/{scan_id}/recompute",
    response_model=ChemicalReadingOut,
    summary="Recompute a reading server-side from the stored strip image",
    description=(
        "Independent verification of a device-computed reading. Used when a finding is "
        "disputed: the server re-derives the number from the stored photograph using the "
        "same pipeline, so the result does not rest on trusting the handset."
    ),
)
def recompute_reading(
    scan_id: uuid.UUID,
    assay: ReagentAssay,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChemicalReadingOut:
    from sqlalchemy import select

    from app.core.errors import NotFoundError
    from app.models.scan import ChemicalReading, ScanImage

    service = ScanService(db)
    scan = service.get_owned(scan_id, principal.user)

    image = db.scalar(
        select(ScanImage)
        .where(
            ScanImage.scan_id == scan.id,
            ScanImage.kind == "strip",
            ScanImage.deleted_at.is_(None),
        )
        .order_by(ScanImage.created_at.desc())
        .limit(1)
    )
    if image is None:
        raise NotFoundError(
            "No strip photograph is stored for this scan, so the reading cannot be recomputed."
        )

    result = read_strip(object_store.get(image.object_key), assay)
    reading = ChemicalReading(
        scan_id=scan.id,
        assay=result.assay,
        calibration_id=result.calibration_id or "",
        accepted=result.accepted,
        reject_reason=str(result.reject_reason) if result.reject_reason else None,
        reject_detail=result.reject_detail,
        concentration_value=result.concentration,
        concentration_unit=result.unit,
        ci_low=result.ci_low,
        ci_high=result.ci_high,
        band_label=result.band_label,
        exceeds_action_threshold=result.exceeds_action_threshold,
        delta_e_nearest=result.delta_e_nearest_stop,
        lab_l=result.strip_lab_corrected[0] if result.strip_lab_corrected else None,
        lab_a=result.strip_lab_corrected[1] if result.strip_lab_corrected else None,
        lab_b=result.strip_lab_corrected[2] if result.strip_lab_corrected else None,
        correction_residual_de=result.correction_residual_de,
        exposure_score=result.exposure_score,
        illuminant_tint=result.illuminant_tint,
        quality_report=result.quality,
        computed_on="server",
        pipeline_version=result.pipeline_version,
    )
    db.add(reading)
    write_audit(
        db,
        action="colorimetry.recomputed",
        principal=principal,
        object_type="scan",
        object_id=str(scan.id),
        request=request,
        context={"accepted": result.accepted, "value": result.concentration},
    )
    db.commit()
    db.refresh(reading)
    return ChemicalReadingOut.model_validate(reading)
