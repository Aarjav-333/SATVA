"""Colour-space mathematics for the SATVA colorimetric reader.

Everything here is pure, deterministic and free of I/O so that it can be
unit-tested against published reference values and mirrored exactly in the Dart
implementation that runs on the handset. See
`mobile/lib/features/colorimetry/engine/color_math.dart` and the shared golden
vectors in `docs/colorimetry/golden_vectors.json`.

Conventions
-----------
* sRGB values are floats in [0, 1] unless a function name says ``_u8``.
* The reference white is D65 (the illuminant sRGB is defined against).
* ``delta_e_2000`` implements CIEDE2000 (Sharma, Wu & Dalal 2005). The unit
  tests check it against that paper's published test-data table, because a
  subtly wrong ΔE would silently corrupt every reading SATVA reports.
"""

from __future__ import annotations

import math

import numpy as np

# sRGB (IEC 61966-2-1) to CIE XYZ, D65.
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)

# D65 white point, 2-degree standard observer, normalised so Y = 100.
#
# Derived from the matrix above rather than written out as the nominal
# (95.047, 100.000, 108.883), because the published matrix coefficients are
# rounded and do not sum exactly to that triple. Using the nominal value leaves
# white at L* = 100.0000039 and gives neutral greys a chroma of ~4e-6 -- small,
# but it means "is this patch neutral?" is answered against an inconsistent
# reference. Deriving the white point from the same matrix makes white exact by
# construction and neutrals exactly achromatic.
D65_WHITE = _SRGB_TO_XYZ.sum(axis=1) * 100.0

# The nominal published value, kept for documentation and cross-checking.
D65_WHITE_NOMINAL = np.array([95.047, 100.000, 108.883], dtype=np.float64)

_CIE_EPSILON = 216.0 / 24389.0
_CIE_KAPPA = 24389.0 / 27.0


def srgb_to_linear(channel: np.ndarray | float) -> np.ndarray:
    """Undo the sRGB transfer function. Input and output are in [0, 1]."""
    c = np.asarray(channel, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(channel: np.ndarray | float) -> np.ndarray:
    """Apply the sRGB transfer function. Input and output are in [0, 1]."""
    c = np.asarray(channel, dtype=np.float64)
    return np.where(
        c <= 0.0031308, c * 12.92, 1.055 * np.power(np.clip(c, 0, None), 1 / 2.4) - 0.055
    )


def srgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    """sRGB in [0, 1] -> XYZ with Y in [0, 100]. Accepts (3,) or (N, 3)."""
    arr = np.atleast_2d(np.asarray(rgb, dtype=np.float64))
    linear = srgb_to_linear(arr)
    xyz = linear @ _SRGB_TO_XYZ.T * 100.0
    return xyz[0] if np.ndim(rgb) == 1 else xyz


def xyz_to_lab(xyz: np.ndarray, white: np.ndarray = D65_WHITE) -> np.ndarray:
    """CIE XYZ -> CIE L*a*b*. Accepts (3,) or (N, 3)."""
    arr = np.atleast_2d(np.asarray(xyz, dtype=np.float64))
    ratio = arr / white

    f = np.where(
        ratio > _CIE_EPSILON,
        np.cbrt(np.clip(ratio, 0, None)),
        (_CIE_KAPPA * ratio + 16.0) / 116.0,
    )
    fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]

    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=1)
    return lab[0] if np.ndim(xyz) == 1 else lab


def lab_to_xyz(lab: np.ndarray, white: np.ndarray = D65_WHITE) -> np.ndarray:
    arr = np.atleast_2d(np.asarray(lab, dtype=np.float64))
    ell, a, b = arr[:, 0], arr[:, 1], arr[:, 2]

    fy = (ell + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def _inv(t: np.ndarray) -> np.ndarray:
        t3 = t**3
        return np.where(t3 > _CIE_EPSILON, t3, (116.0 * t - 16.0) / _CIE_KAPPA)

    xyz = np.stack([_inv(fx), _inv(fy), _inv(fz)], axis=1) * white
    return xyz[0] if np.ndim(lab) == 1 else xyz


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convenience: sRGB in [0, 1] straight to L*a*b*."""
    return xyz_to_lab(srgb_to_xyz(rgb))


def srgb_u8_to_lab(rgb_u8: np.ndarray) -> np.ndarray:
    """Convenience: 8-bit sRGB straight to L*a*b*."""
    return srgb_to_lab(np.asarray(rgb_u8, dtype=np.float64) / 255.0)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    """L*a*b* back to sRGB in [0, 1], clipped to the displayable gamut."""
    xyz = np.atleast_2d(lab_to_xyz(lab)) / 100.0
    linear = xyz @ np.linalg.inv(_SRGB_TO_XYZ).T
    out = np.clip(linear_to_srgb(np.clip(linear, 0.0, 1.0)), 0.0, 1.0)
    return out[0] if np.ndim(lab) == 1 else out


def delta_e_76(lab1: np.ndarray, lab2: np.ndarray) -> float | np.ndarray:
    """CIE76 Euclidean distance. Used only for coarse gating, never reporting."""
    a = np.atleast_2d(np.asarray(lab1, dtype=np.float64))
    b = np.atleast_2d(np.asarray(lab2, dtype=np.float64))
    d = np.sqrt(np.sum((a - b) ** 2, axis=1))
    return float(d[0]) if d.size == 1 else d


def delta_e_2000(
    lab1: np.ndarray,
    lab2: np.ndarray,
    *,
    k_l: float = 1.0,
    k_c: float = 1.0,
    k_h: float = 1.0,
) -> float | np.ndarray:
    """CIEDE2000 colour difference.

    Implementation follows Sharma, Wu & Dalal (2005), "The CIEDE2000
    Color-Difference Formula: Implementation Notes, Supplementary Test Data, and
    Mathematical Observations". The hue-difference discontinuities called out in
    that paper are handled explicitly rather than relying on atan2 alone, which
    is where naive implementations usually diverge.
    """
    a = np.atleast_2d(np.asarray(lab1, dtype=np.float64))
    b = np.atleast_2d(np.asarray(lab2, dtype=np.float64))

    l1, a1, b1 = a[:, 0], a[:, 1], a[:, 2]
    l2, a2, b2 = b[:, 0], b[:, 1], b[:, 2]

    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0

    c_bar7 = c_bar**7
    g = 0.5 * (1.0 - np.sqrt(c_bar7 / (c_bar7 + 25.0**7)))

    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = np.hypot(a1p, b1)
    c2p = np.hypot(a2p, b2)

    def _hue(ap: np.ndarray, bp: np.ndarray) -> np.ndarray:
        h = np.degrees(np.arctan2(bp, ap))
        h = np.where(h < 0, h + 360.0, h)
        # Convention: hue is 0 when chroma is 0.
        return np.where((np.abs(ap) < 1e-12) & (np.abs(bp) < 1e-12), 0.0, h)

    h1p = _hue(a1p, b1)
    h2p = _hue(a2p, b2)

    dlp = l2 - l1
    dcp = c2p - c1p

    c_prod_zero = (c1p * c2p) == 0
    dhp_raw = h2p - h1p
    dhp = np.where(
        c_prod_zero,
        0.0,
        np.where(
            np.abs(dhp_raw) <= 180.0,
            dhp_raw,
            np.where(dhp_raw > 180.0, dhp_raw - 360.0, dhp_raw + 360.0),
        ),
    )
    dhp_term = 2.0 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp / 2.0))

    lp_bar = (l1 + l2) / 2.0
    cp_bar = (c1p + c2p) / 2.0

    h_sum = h1p + h2p
    h_abs = np.abs(h1p - h2p)
    hp_bar = np.where(
        c_prod_zero,
        h_sum,
        np.where(
            h_abs <= 180.0,
            h_sum / 2.0,
            np.where(h_sum < 360.0, (h_sum + 360.0) / 2.0, (h_sum - 360.0) / 2.0),
        ),
    )

    t = (
        1.0
        - 0.17 * np.cos(np.radians(hp_bar - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hp_bar))
        + 0.32 * np.cos(np.radians(3.0 * hp_bar + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hp_bar - 63.0))
    )

    d_theta = 30.0 * np.exp(-(((hp_bar - 275.0) / 25.0) ** 2))
    cp_bar7 = cp_bar**7
    r_c = 2.0 * np.sqrt(cp_bar7 / (cp_bar7 + 25.0**7))
    r_t = -r_c * np.sin(np.radians(2.0 * d_theta))

    lp_bar_sq = (lp_bar - 50.0) ** 2
    s_l = 1.0 + (0.015 * lp_bar_sq) / np.sqrt(20.0 + lp_bar_sq)
    s_c = 1.0 + 0.045 * cp_bar
    s_h = 1.0 + 0.015 * cp_bar * t

    term_l = dlp / (k_l * s_l)
    term_c = dcp / (k_c * s_c)
    term_h = dhp_term / (k_h * s_h)

    de = np.sqrt(term_l**2 + term_c**2 + term_h**2 + r_t * term_c * term_h)
    return float(de[0]) if de.size == 1 else de


def mean_lab(pixels_u8: np.ndarray) -> np.ndarray:
    """Perceptual mean of an 8-bit RGB pixel block.

    Averaging must happen in a linear space, not in gamma-encoded sRGB: the mean
    of gamma-encoded values is systematically darker than the mean of the light
    the sensor actually received. We therefore average linear RGB and convert the
    result once.
    """
    arr = np.asarray(pixels_u8, dtype=np.float64).reshape(-1, 3) / 255.0
    linear_mean = srgb_to_linear(arr).mean(axis=0)
    xyz = (linear_mean @ _SRGB_TO_XYZ.T) * 100.0
    return xyz_to_lab(xyz)


def chroma(lab: np.ndarray) -> float:
    lab = np.asarray(lab, dtype=np.float64)
    return float(math.hypot(lab[1], lab[2]))


def hue_angle_deg(lab: np.ndarray) -> float:
    lab = np.asarray(lab, dtype=np.float64)
    h = math.degrees(math.atan2(lab[2], lab[1]))
    return h + 360.0 if h < 0 else h
