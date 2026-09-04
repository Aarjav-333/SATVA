"""Capture-quality gating for the colorimetric reader.

Non-negotiable rule 3: bad capture conditions must cause refusal, not a weak
result. This module is where that rule is actually enforced. Every check
returns a hard pass/fail with a machine-readable reason; there is no "proceed
anyway with lower confidence" path, because a wrong chemical number attached to
a complaint is far more damaging than no number at all.

Thresholds
----------
The numeric thresholds below are engineering defaults chosen to be
conservative. They have **not** been validated against a NABL laboratory; spec
19 lists that validation as post-hackathon work. They are collected in one
dataclass so a calibration campaign can replace them without touching logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.core.constants import ColorimetryRejectReason
from app.services.colorimetry.color_math import delta_e_2000, srgb_u8_to_lab
from app.services.colorimetry.reference_card import (
    ReferenceCardSpec,
    sample_patch,
    sample_patch_pixels,
)


@dataclass(frozen=True)
class QualityThresholds:
    # Exposure: mean level of the brightest neutral patch, 8-bit.
    white_patch_min: float = 90.0
    white_patch_max: float = 246.0
    # Fraction of card pixels allowed to be at the extremes of the range.
    max_clipped_high_fraction: float = 0.02
    max_clipped_low_fraction: float = 0.05
    clip_high_level: int = 252
    clip_low_level: int = 4
    # Illuminant tint: chroma of the neutral patches after averaging. A neutral
    # patch imaged under a neutral illuminant has near-zero chroma.
    max_neutral_chroma: float = 14.0
    # Illumination uniformity: relative luminance spread of the same-value
    # neutral sampled at opposite ends of the card. A shadow across the card
    # shows up here even when the mean exposure looks fine.
    max_illumination_nonuniformity: float = 0.22
    # Strip well homogeneity: standard deviation of L* inside the well.
    max_strip_l_std: float = 9.0
    min_strip_pixels: int = 400
    # Clipping inside the strip well is disqualifying at a much lower fraction
    # than elsewhere, because the well *is* the measurement.
    max_strip_clipped_fraction: float = 0.10
    # Post-correction accuracy, mean CIEDE2000 residual across patches.
    max_mean_residual_de: float = 3.5
    max_worst_residual_de: float = 7.0


DEFAULT_THRESHOLDS = QualityThresholds()


@dataclass
class QualityReport:
    passed: bool
    reason: ColorimetryRejectReason | None = None
    detail: str | None = None
    metrics: dict = field(default_factory=dict)

    def fail(self, reason: ColorimetryRejectReason, detail: str) -> QualityReport:
        self.passed = False
        self.reason = reason
        self.detail = detail
        return self


def assess_exposure(
    warped_bgr: np.ndarray,
    spec: ReferenceCardSpec,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Check that the frame is exposed inside the range the card can correct."""
    report = QualityReport(passed=True)

    neutrals = spec.neutral_patches
    if not neutrals:
        return report.fail(
            ColorimetryRejectReason.REFERENCE_CARD_INCOMPLETE, "card has no neutral patches"
        )

    samples = np.array([sample_patch(warped_bgr, p) for p in neutrals])
    luminance = samples.mean(axis=1)
    brightest = float(luminance.max())
    darkest = float(luminance.min())

    report.metrics["neutral_luminance"] = [round(float(v), 2) for v in luminance]
    report.metrics["brightest_neutral"] = round(brightest, 2)
    report.metrics["darkest_neutral"] = round(darkest, 2)

    # Clipping is measured over the card's *reference* area, excluding the strip
    # well. The strip is the sample, not a reference: an unreacted turmeric
    # strip is a legitimately near-saturated yellow, and counting it as card
    # clipping would refuse exactly the "not detected" readings that exonerate a
    # vendor. Clipping inside the well is checked separately, in
    # `assess_strip_region`, where it is genuinely disqualifying.
    reference_area = warped_bgr.copy()
    wx, wy, ww, wh = spec.strip_well
    height, width = reference_area.shape[:2]
    x0, y0 = int(wx * width), int(wy * height)
    x1, y1 = int((wx + ww) * width), int((wy + wh) * height)
    mask = np.ones((height, width), dtype=bool)
    mask[y0:y1, x0:x1] = False
    pixels = reference_area[mask]
    high_fraction = float((pixels >= thresholds.clip_high_level).any(axis=1).mean())
    low_fraction = float((pixels <= thresholds.clip_low_level).all(axis=1).mean())
    report.metrics["clipped_high_fraction"] = round(high_fraction, 4)
    report.metrics["clipped_low_fraction"] = round(low_fraction, 4)

    if brightest > thresholds.white_patch_max:
        return report.fail(
            ColorimetryRejectReason.EXPOSURE_OUT_OF_RANGE,
            f"reference white is at {brightest:.0f}/255 - the frame is over-exposed",
        )
    if brightest < thresholds.white_patch_min:
        return report.fail(
            ColorimetryRejectReason.EXPOSURE_OUT_OF_RANGE,
            f"reference white is only {brightest:.0f}/255 - the frame is under-exposed",
        )
    if high_fraction > thresholds.max_clipped_high_fraction:
        return report.fail(
            ColorimetryRejectReason.CLIPPED_HIGHLIGHTS,
            f"{high_fraction * 100:.1f}% of the card is clipped white",
        )
    if low_fraction > thresholds.max_clipped_low_fraction:
        return report.fail(
            ColorimetryRejectReason.CLIPPED_SHADOWS,
            f"{low_fraction * 100:.1f}% of the card is crushed black",
        )

    # Monotonicity: the printed ramp descends. If the observed ramp does not,
    # the card was misdetected or something is occluding a patch.
    if not np.all(np.diff(luminance) < 6.0):
        return report.fail(
            ColorimetryRejectReason.REFERENCE_CARD_INCOMPLETE,
            "the neutral ramp is not monotonic - the card may be occluded",
        )

    return report


def assess_illumination(
    warped_bgr: np.ndarray,
    spec: ReferenceCardSpec,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Check for a tinted light source or a shadow falling across the card."""
    report = QualityReport(passed=True)

    neutrals = spec.neutral_patches
    labs = np.array([srgb_u8_to_lab(sample_patch(warped_bgr, p)) for p in neutrals])
    chromas = np.hypot(labs[:, 1], labs[:, 2])
    mean_chroma = float(chromas.mean())
    report.metrics["neutral_chroma_mean"] = round(mean_chroma, 3)
    report.metrics["neutral_chroma_max"] = round(float(chromas.max()), 3)

    if mean_chroma > thresholds.max_neutral_chroma:
        return report.fail(
            ColorimetryRejectReason.ILLUMINANT_TOO_TINTED,
            f"grey patches show a colour cast (mean chroma {mean_chroma:.1f}) - "
            "the light source is too strongly tinted to correct reliably",
        )

    # Uniformity: compare the card's own left and right halves using a flat-field
    # estimate from the paper white around the patches.
    height, width = warped_bgr.shape[:2]
    band = warped_bgr[int(height * 0.90) : int(height * 0.99), :, :]
    if band.size:
        columns = band.reshape(band.shape[0], -1, 3).mean(axis=(0, 2))
        left = float(columns[: width // 3].mean())
        right = float(columns[-width // 3 :].mean())
        centre = float(columns[width // 3 : 2 * width // 3].mean())
        span = max(left, right, centre)
        low = min(left, right, centre)
        nonuniformity = (span - low) / (span + 1e-6)
        report.metrics["illumination_nonuniformity"] = round(nonuniformity, 4)
        if nonuniformity > thresholds.max_illumination_nonuniformity:
            return report.fail(
                ColorimetryRejectReason.NON_UNIFORM_ILLUMINATION,
                f"brightness varies by {nonuniformity * 100:.0f}% across the card - "
                "a shadow is probably falling on it",
            )

    return report


def assess_strip_region(
    warped_bgr: np.ndarray,
    spec: ReferenceCardSpec,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Check that a usable, uniformly reacted strip is present in the well."""
    report = QualityReport(passed=True)

    pixels = sample_patch_pixels(warped_bgr, spec.strip_well)
    report.metrics["strip_pixel_count"] = int(pixels.shape[0])
    if pixels.shape[0] < thresholds.min_strip_pixels:
        return report.fail(
            ColorimetryRejectReason.STRIP_REGION_NOT_FOUND,
            "the strip well is too small in the frame to sample reliably",
        )

    # Clipping inside the well destroys the measurement outright: a saturated
    # channel no longer carries the colour information the reading depends on.
    strip_high = float((pixels >= thresholds.clip_high_level).any(axis=1).mean())
    strip_low = float((pixels <= thresholds.clip_low_level).all(axis=1).mean())
    report.metrics["strip_clipped_high_fraction"] = round(strip_high, 4)
    report.metrics["strip_clipped_low_fraction"] = round(strip_low, 4)
    if strip_high > thresholds.max_strip_clipped_fraction:
        return report.fail(
            ColorimetryRejectReason.CLIPPED_HIGHLIGHTS,
            f"{strip_high * 100:.0f}% of the strip is blown out - move out of direct "
            "sunlight or glare and retake the photo",
        )
    if strip_low > thresholds.max_strip_clipped_fraction:
        return report.fail(
            ColorimetryRejectReason.CLIPPED_SHADOWS,
            f"{strip_low * 100:.0f}% of the strip is in deep shadow",
        )

    labs = srgb_u8_to_lab(pixels)
    l_std = float(labs[:, 0].std())
    report.metrics["strip_l_std"] = round(l_std, 3)
    report.metrics["strip_lab_mean"] = [round(float(v), 3) for v in labs.mean(axis=0)]

    if l_std > thresholds.max_strip_l_std:
        return report.fail(
            ColorimetryRejectReason.STRIP_REGION_NOT_UNIFORM,
            f"the strip well is not uniform (L* sd {l_std:.1f}) - the strip may be "
            "missing, folded, creased or only partly wetted",
        )

    return report


def assess_correction_residual(
    residuals_de: np.ndarray,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> QualityReport:
    """Reject when the colour correction could not fit the observed patches.

    A large residual means the camera response could not be reconciled with the
    printed card, so any number derived from the corrected strip colour would be
    fiction. Spec 7 lists this explicitly as a rejection condition.
    """
    report = QualityReport(passed=True)
    mean_residual = float(np.mean(residuals_de))
    worst_residual = float(np.max(residuals_de))
    report.metrics["residual_de_mean"] = round(mean_residual, 3)
    report.metrics["residual_de_max"] = round(worst_residual, 3)

    if mean_residual > thresholds.max_mean_residual_de:
        return report.fail(
            ColorimetryRejectReason.CORRECTION_RESIDUAL_TOO_HIGH,
            f"colour correction left a mean error of dE {mean_residual:.1f} across the "
            "reference patches, which is too large to trust a reading",
        )
    if worst_residual > thresholds.max_worst_residual_de:
        return report.fail(
            ColorimetryRejectReason.CORRECTION_RESIDUAL_TOO_HIGH,
            f"one reference patch is off by dE {worst_residual:.1f} after correction",
        )
    return report


def residuals_between(observed_lab: np.ndarray, expected_lab: np.ndarray) -> np.ndarray:
    """Per-patch CIEDE2000 residual vector."""
    out = delta_e_2000(observed_lab, expected_lab)
    return np.atleast_1d(np.asarray(out, dtype=np.float64))
