"""The SATVA colorimetric pipeline.

Implements spec 7 end to end, in the order the specification states:

  1. detect the reference card
  2. verify acceptable exposure and capture conditions
  3. observe the known reference patches
  4. compute a per-channel colour correction
  5. apply the correction to the strip region
  6. convert the corrected strip colour to CIE L*a*b*
  7. compute dE against calibrated concentration stops
  8. produce a numeric reading
  9. calculate a confidence interval from correction residual error
 10. reject the reading if correction residuals are excessive

Every failure path returns a `ColorimetryResult` with `accepted = False` and a
machine-readable `reject_reason`. The pipeline never returns a number it does
not stand behind -- that is non-negotiable rule 3, and it is the single most
important behaviour in this module.

This is the canonical implementation. `mobile/lib/features/colorimetry/engine/`
mirrors it in Dart for offline on-device execution, and
`docs/colorimetry/golden_vectors.json` holds shared test vectors that both
implementations are checked against so they cannot silently diverge.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from app.core.constants import ColorimetryRejectReason, ReagentAssay
from app.services.colorimetry.calibration import (
    CalibrationSeries,
    confidence_interval,
    get_calibration,
    project_onto_path,
)
from app.services.colorimetry.color_math import srgb_u8_to_lab
from app.services.colorimetry.correction import ColorCorrection, fit_best_correction
from app.services.colorimetry.quality import (
    DEFAULT_THRESHOLDS,
    QualityThresholds,
    assess_correction_residual,
    assess_exposure,
    assess_illumination,
    assess_strip_region,
)
from app.services.colorimetry.reference_card import (
    SATVA_CARD_V1,
    CardDetection,
    ReferenceCardSpec,
    detect_card,
    sample_patch,
    sample_patch_pixels,
)

PIPELINE_VERSION = "1.0.0"


@dataclass
class ColorimetryResult:
    """Outcome of one strip read.

    `accepted` is the only field the rest of the system is allowed to branch on
    when deciding whether something counts as evidence.
    """

    accepted: bool
    assay: str
    calibration_id: str | None = None
    pipeline_version: str = PIPELINE_VERSION

    # Result (present only when accepted).
    concentration: float | None = None
    unit: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    band_label: str | None = None
    exceeds_action_threshold: bool = False
    is_lab_validated: bool = False
    # True when the reading sits at an end stop of the calibrated series. The
    # true value may lie beyond it, but SATVA does not extrapolate, so the
    # number is reported as a bound rather than a point estimate.
    at_range_limit: bool = False
    range_limit_side: str | None = None  # "lower" | "upper"

    # Refusal (present only when not accepted).
    reject_reason: ColorimetryRejectReason | None = None
    reject_detail: str | None = None

    # Measurement provenance -- enough to re-derive the number offline.
    strip_lab_raw: tuple[float, float, float] | None = None
    strip_lab_corrected: tuple[float, float, float] | None = None
    delta_e_nearest_stop: float | None = None
    off_path_delta_e: float | None = None
    correction_residual_de: float | None = None
    correction_model: str | None = None
    exposure_score: float | None = None
    illuminant_tint: float | None = None
    card_area_fraction: float | None = None
    elapsed_ms: int | None = None
    quality: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.reject_reason is not None:
            data["reject_reason"] = str(self.reject_reason)
        return data

    @classmethod
    def refused(
        cls,
        assay: str,
        reason: ColorimetryRejectReason,
        detail: str,
        *,
        quality: dict | None = None,
        calibration_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> ColorimetryResult:
        return cls(
            accepted=False,
            assay=assay,
            calibration_id=calibration_id,
            reject_reason=reason,
            reject_detail=detail,
            quality=quality or {},
            elapsed_ms=elapsed_ms,
        )


def _decode(image_bytes: bytes) -> np.ndarray | None:
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def read_strip(
    image: np.ndarray | bytes,
    assay: str | ReagentAssay,
    *,
    spec: ReferenceCardSpec = SATVA_CARD_V1,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> ColorimetryResult:
    """Read a reacted test strip photographed beside the SATVA reference card."""
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    assay_value = str(assay)
    series: CalibrationSeries | None = get_calibration(assay_value)
    if series is None:
        return ColorimetryResult.refused(
            assay_value,
            ColorimetryRejectReason.UNKNOWN_REAGENT,
            f"no calibration series is registered for reagent '{assay_value}'",
            elapsed_ms=elapsed(),
        )

    # --- Step 1: detect the reference card ---------------------------------
    frame = _decode(image) if isinstance(image, bytes | bytearray) else image
    if frame is None or frame.size == 0:
        return ColorimetryResult.refused(
            assay_value,
            ColorimetryRejectReason.REFERENCE_CARD_NOT_FOUND,
            "the image could not be decoded",
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    detection: CardDetection = detect_card(frame, spec)
    if not detection.found or detection.warped is None:
        reason = (
            ColorimetryRejectReason.REFERENCE_CARD_INCOMPLETE
            if detection.reason == "orientation_key_not_found"
            else ColorimetryRejectReason.REFERENCE_CARD_NOT_FOUND
        )
        return ColorimetryResult.refused(
            assay_value,
            reason,
            "the SATVA reference card was not found in the photograph. "
            "Place the card flat beside the strip and retake the photo.",
            quality={"detection": detection.reason, **detection.diagnostics},
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    warped = detection.warped
    quality: dict = {
        "card_area_fraction": round(detection.quad_area_fraction, 4),
        "orientation_confidence": round(detection.orientation_confidence, 4),
    }

    # --- Step 2: verify exposure and capture conditions --------------------
    exposure = assess_exposure(warped, spec, thresholds)
    quality.update(exposure.metrics)
    if not exposure.passed:
        return ColorimetryResult.refused(
            assay_value,
            exposure.reason,
            exposure.detail,
            quality=quality,
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    illumination = assess_illumination(warped, spec, thresholds)
    quality.update(illumination.metrics)
    if not illumination.passed:
        return ColorimetryResult.refused(
            assay_value,
            illumination.reason,
            illumination.detail,
            quality=quality,
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    strip_quality = assess_strip_region(warped, spec, thresholds)
    quality.update(strip_quality.metrics)
    if not strip_quality.passed:
        return ColorimetryResult.refused(
            assay_value,
            strip_quality.reason,
            strip_quality.detail,
            quality=quality,
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    # --- Steps 3-4: observe the patches and fit the correction -------------
    observed = np.array([sample_patch(warped, p) for p in spec.patches])
    expected = np.array([p.srgb for p in spec.patches], dtype=np.float64)
    try:
        correction: ColorCorrection = fit_best_correction(observed, expected)
    except (ValueError, np.linalg.LinAlgError) as exc:
        return ColorimetryResult.refused(
            assay_value,
            ColorimetryRejectReason.CORRECTION_RESIDUAL_TOO_HIGH,
            f"colour correction could not be fitted: {exc}",
            quality=quality,
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    quality["correction"] = correction.to_dict()

    # --- Step 10 (applied early, as the spec requires) ---------------------
    # The residual gate runs before any number is produced, so that a badly
    # corrected frame can never yield a reading at all.
    residual_check = assess_correction_residual(correction.cv_residuals_de, thresholds)
    quality.update(residual_check.metrics)
    if not residual_check.passed:
        return ColorimetryResult.refused(
            assay_value,
            residual_check.reason,
            residual_check.detail,
            quality=quality,
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    # --- Steps 5-6: correct the strip region and convert to Lab ------------
    strip_pixels = sample_patch_pixels(warped, spec.strip_well)
    strip_raw_mean_u8 = strip_pixels.mean(axis=0)
    strip_lab_raw = srgb_u8_to_lab(strip_raw_mean_u8)
    strip_lab_corrected = correction.apply_to_lab(strip_raw_mean_u8)

    # --- Step 7: dE against the calibrated stops ---------------------------
    projection = project_onto_path(strip_lab_corrected, series)

    if projection.off_path_de > series.max_off_path_de:
        return ColorimetryResult.refused(
            assay_value,
            ColorimetryRejectReason.OUT_OF_CALIBRATION_RANGE,
            f"the strip colour sits dE {projection.off_path_de:.1f} away from the "
            f"{series.display_name} calibration path. This does not look like a "
            "reacted strip for the selected test.",
            quality={**quality, "off_path_de": round(projection.off_path_de, 3)},
            calibration_id=series.calibration_id,
            elapsed_ms=elapsed(),
        )

    # --- Steps 8-9: the number, and the interval around it -----------------
    optical_uncertainty = correction.mean_cv_residual_de
    ci_low, ci_high = confidence_interval(projection, series, optical_uncertainty)
    concentration = round(projection.concentration, 4)

    lowest = float(series.concentrations.min())
    highest = float(series.concentrations.max())
    tolerance = 1e-6
    at_upper = concentration >= highest - tolerance
    at_lower = concentration <= lowest + tolerance

    return ColorimetryResult(
        accepted=True,
        assay=assay_value,
        calibration_id=series.calibration_id,
        concentration=concentration,
        unit=series.unit,
        ci_low=ci_low,
        ci_high=ci_high,
        band_label=series.band_label(concentration),
        # The action threshold is compared against the *lower* bound of the
        # interval. Escalating on the point estimate alone would flag readings
        # whose uncertainty still spans "not detected", and a complaint against
        # an honest vendor is exactly the harm rule 1 exists to prevent.
        exceeds_action_threshold=ci_low >= series.action_threshold,
        is_lab_validated=series.is_lab_validated,
        at_range_limit=at_upper or at_lower,
        range_limit_side=("upper" if at_upper else "lower" if at_lower else None),
        strip_lab_raw=tuple(round(float(v), 3) for v in strip_lab_raw),
        strip_lab_corrected=tuple(round(float(v), 3) for v in strip_lab_corrected),
        delta_e_nearest_stop=round(projection.nearest_stop_de, 3),
        off_path_delta_e=round(projection.off_path_de, 3),
        correction_residual_de=round(optical_uncertainty, 4),
        correction_model=correction.model,
        exposure_score=quality.get("brightest_neutral"),
        illuminant_tint=quality.get("neutral_chroma_mean"),
        card_area_fraction=round(detection.quad_area_fraction, 4),
        elapsed_ms=elapsed(),
        quality=quality,
    )
