"""Colour correction from observed reference patches to true printed colour.

Model
-----
The correction is fitted in **linear** RGB, because that is the space in which a
camera's response to light is approximately affine. Fitting in gamma-encoded
sRGB would fold the transfer function into the fit and produce a correction that
is only valid at the exposure it was fitted at.

Two models are available:

``per_channel``
    Independent gain and offset per channel: ``out_c = g_c * in_c + o_c``.
    Six parameters, needs only the neutral ramp. This is the model spec 7
    describes ("compute a per-channel colour correction") and it handles the
    dominant error source, which is white-balance drift.

``affine33``
    A full 3x3 matrix plus offset, fitted by least squares over all patches.
    Twelve parameters. This additionally corrects channel cross-talk from the
    sensor's colour filter array, which per-channel gain cannot express.

The pipeline fits both and keeps whichever gives the lower **cross-validated**
residual. Cross-validation matters: the 3x3 model has enough freedom to fit 12
patches well while generalising worse to the strip colour, and an in-sample
residual would hide exactly that. Leave-one-out is cheap at this size and gives
an honest estimate of the error we are about to propagate into the confidence
interval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.services.colorimetry.color_math import (
    linear_to_srgb,
    srgb_to_linear,
    srgb_u8_to_lab,
    xyz_to_lab,
)
from app.services.colorimetry.color_math import srgb_to_xyz as _srgb_to_xyz
from app.services.colorimetry.quality import residuals_between

CorrectionModel = Literal["per_channel", "affine33"]


@dataclass
class ColorCorrection:
    """A fitted correction plus the evidence that it is trustworthy."""

    model: CorrectionModel
    matrix: np.ndarray  # (3, 3)
    offset: np.ndarray  # (3,)
    residuals_de: np.ndarray  # in-sample per-patch CIEDE2000
    cv_residuals_de: np.ndarray  # leave-one-out per-patch CIEDE2000
    n_patches: int

    @property
    def mean_residual_de(self) -> float:
        return float(np.mean(self.residuals_de))

    @property
    def mean_cv_residual_de(self) -> float:
        return float(np.mean(self.cv_residuals_de))

    @property
    def worst_cv_residual_de(self) -> float:
        return float(np.max(self.cv_residuals_de))

    def apply_linear(self, linear_rgb: np.ndarray) -> np.ndarray:
        arr = np.atleast_2d(np.asarray(linear_rgb, dtype=np.float64))
        out = arr @ self.matrix.T + self.offset
        return out[0] if np.ndim(linear_rgb) == 1 else out

    def apply_srgb_u8(self, rgb_u8: np.ndarray) -> np.ndarray:
        """Correct an 8-bit sRGB colour, returning corrected sRGB in [0, 1]."""
        linear = srgb_to_linear(np.asarray(rgb_u8, dtype=np.float64) / 255.0)
        corrected = np.clip(self.apply_linear(linear), 0.0, 1.0)
        return linear_to_srgb(corrected)

    def apply_to_lab(self, rgb_u8: np.ndarray) -> np.ndarray:
        """Correct an 8-bit sRGB colour and return it as CIE L*a*b*."""
        corrected_srgb = np.atleast_2d(self.apply_srgb_u8(rgb_u8))
        lab = xyz_to_lab(_srgb_to_xyz(corrected_srgb))
        return lab[0] if np.ndim(rgb_u8) == 1 else lab

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "matrix": [[round(float(v), 6) for v in row] for row in self.matrix],
            "offset": [round(float(v), 6) for v in self.offset],
            "n_patches": self.n_patches,
            "mean_residual_de": round(self.mean_residual_de, 4),
            "mean_cv_residual_de": round(self.mean_cv_residual_de, 4),
            "worst_cv_residual_de": round(self.worst_cv_residual_de, 4),
        }


def _fit_per_channel(observed: np.ndarray, expected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares gain and offset per channel, in linear RGB."""
    gains = np.ones(3)
    offsets = np.zeros(3)
    for c in range(3):
        design = np.column_stack([observed[:, c], np.ones(len(observed))])
        solution, *_ = np.linalg.lstsq(design, expected[:, c], rcond=None)
        gains[c], offsets[c] = solution
    return np.diag(gains), offsets


def _fit_affine33(observed: np.ndarray, expected: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares 3x3 matrix plus offset, in linear RGB."""
    design = np.column_stack([observed, np.ones(len(observed))])
    solution, *_ = np.linalg.lstsq(design, expected, rcond=None)  # (4, 3)
    return solution[:3].T, solution[3]


_FITTERS = {"per_channel": _fit_per_channel, "affine33": _fit_affine33}


def _lab_of_linear(linear_rgb: np.ndarray) -> np.ndarray:
    srgb = linear_to_srgb(np.clip(linear_rgb, 0.0, 1.0))
    return xyz_to_lab(_srgb_to_xyz(np.atleast_2d(srgb)))


def fit_correction(
    observed_rgb_u8: np.ndarray,
    expected_rgb_u8: np.ndarray,
    model: CorrectionModel = "per_channel",
) -> ColorCorrection:
    """Fit one correction model and evaluate it, including leave-one-out.

    Parameters
    ----------
    observed_rgb_u8
        (N, 3) means measured off the photographed card.
    expected_rgb_u8
        (N, 3) the values those patches are printed to.
    """
    observed_u8 = np.asarray(observed_rgb_u8, dtype=np.float64).reshape(-1, 3)
    expected_u8 = np.asarray(expected_rgb_u8, dtype=np.float64).reshape(-1, 3)
    n = len(observed_u8)

    minimum = 4 if model == "affine33" else 2
    if n < minimum:
        raise ValueError(f"{model} needs at least {minimum} patches, got {n}")

    obs_linear = srgb_to_linear(observed_u8 / 255.0)
    exp_linear = srgb_to_linear(expected_u8 / 255.0)
    expected_lab = srgb_u8_to_lab(expected_u8)

    matrix, offset = _FITTERS[model](obs_linear, exp_linear)

    corrected = obs_linear @ matrix.T + offset
    residuals = residuals_between(_lab_of_linear(corrected), expected_lab)

    # Leave-one-out: refit without patch i, then measure the error on patch i.
    cv_residuals = np.zeros(n)
    if n > minimum:
        for i in range(n):
            keep = np.arange(n) != i
            m_i, o_i = _FITTERS[model](obs_linear[keep], exp_linear[keep])
            predicted = obs_linear[i] @ m_i.T + o_i
            cv_residuals[i] = float(
                residuals_between(_lab_of_linear(predicted), expected_lab[i : i + 1])[0]
            )
    else:
        # Not enough patches to hold one out; fall back to the in-sample value
        # and let the caller's threshold decide. This should not occur with the
        # v1 card, which has 12 patches.
        cv_residuals = residuals.copy()

    return ColorCorrection(
        model=model,
        matrix=matrix,
        offset=offset,
        residuals_de=residuals,
        cv_residuals_de=cv_residuals,
        n_patches=n,
    )


def fit_best_correction(
    observed_rgb_u8: np.ndarray,
    expected_rgb_u8: np.ndarray,
    candidates: tuple[CorrectionModel, ...] = ("per_channel", "affine33"),
) -> ColorCorrection:
    """Fit every candidate model and keep the best cross-validated one."""
    fitted: list[ColorCorrection] = []
    for model in candidates:
        try:
            fitted.append(fit_correction(observed_rgb_u8, expected_rgb_u8, model))
        except (ValueError, np.linalg.LinAlgError):
            continue
    if not fitted:
        raise ValueError("no correction model could be fitted to the observed patches")
    return min(fitted, key=lambda c: c.mean_cv_residual_de)
