"""Synthetic reference-card renderer used by the colorimetry tests.

This renders the SATVA card exactly as `reference_card.py` specifies it, places
a strip of a chosen colour in the well, and then degrades the frame the way a
real handset in a market would: a tinted illuminant, an exposure error, a
shadow gradient, perspective, sensor noise and JPEG compression.

That degradation is the point. Testing the pipeline on a clean synthetic render
would only prove the code runs; testing it on a *degraded* render proves the
correction actually recovers the true colour and that the quality gates fire
when it cannot.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.services.colorimetry.color_math import lab_to_srgb
from app.services.colorimetry.reference_card import (
    SATVA_CARD_V1,
    WARP_HEIGHT,
    WARP_WIDTH,
    ReferenceCardSpec,
)

PAPER_WHITE = (248, 248, 246)


def render_card(
    strip_lab: tuple[float, float, float] | None,
    spec: ReferenceCardSpec = SATVA_CARD_V1,
    *,
    strip_present: bool = True,
    strip_noise: float = 1.0,
) -> np.ndarray:
    """Render an ideal, head-on card image in BGR at canonical warp size."""
    canvas = np.zeros((WARP_HEIGHT, WARP_WIDTH, 3), dtype=np.uint8)
    canvas[:] = PAPER_WHITE[::-1]

    for patch in (*spec.patches, spec.orientation_key):
        x, y, w, h = patch.pixel_rect()
        canvas[y : y + h, x : x + w] = np.array(patch.srgb[::-1], dtype=np.uint8)

    sx = int(spec.strip_well[0] * WARP_WIDTH)
    sy = int(spec.strip_well[1] * WARP_HEIGHT)
    sw = int(spec.strip_well[2] * WARP_WIDTH)
    sh = int(spec.strip_well[3] * WARP_HEIGHT)

    # The well is printed as a thin outline so it is visible on paper.
    cv2.rectangle(canvas, (sx, sy), (sx + sw, sy + sh), (60, 60, 60), 2)

    if strip_present and strip_lab is not None:
        rgb = np.clip(lab_to_srgb(np.array(strip_lab)) * 255.0, 0, 255)
        block = np.zeros((sh - 6, sw - 6, 3), dtype=np.float64)
        block[:] = rgb[::-1]
        if strip_noise > 0:
            rng = np.random.default_rng(20260904)
            block += rng.normal(0.0, strip_noise, block.shape)
        canvas[sy + 3 : sy + sh - 3, sx + 3 : sx + sw - 3] = np.clip(block, 0, 255).astype(np.uint8)

    # Dark border so the card outline is findable against a light background.
    return cv2.copyMakeBorder(canvas, 14, 14, 14, 14, cv2.BORDER_CONSTANT, value=(20, 20, 20))


def apply_capture_conditions(
    card_bgr: np.ndarray,
    *,
    illuminant_gain: tuple[float, float, float] = (1.0, 1.0, 1.0),
    exposure: float = 1.0,
    shadow_strength: float = 0.0,
    perspective: float = 0.0,
    noise_sigma: float = 0.0,
    jpeg_quality: int | None = None,
    background: tuple[int, int, int] = (150, 155, 160),
    scale: float = 1.0,
    seed: int = 7,
) -> np.ndarray:
    """Degrade an ideal card render into something a phone would actually capture.

    `illuminant_gain` is applied in linear light (the physically correct place
    for a light-source tint), while `exposure` scales the whole frame. Applying
    the tint in gamma-encoded space would produce a cast the correction can
    trivially undo, which would make the test far too easy.
    """
    rng = np.random.default_rng(seed)
    img = card_bgr.astype(np.float64) / 255.0

    # Linearise, tint, expose, re-encode.
    linear = np.where(img <= 0.04045, img / 12.92, ((img + 0.055) / 1.055) ** 2.4)
    gain_bgr = np.array(illuminant_gain[::-1], dtype=np.float64)
    linear = linear * gain_bgr * exposure

    if shadow_strength > 0:
        height, width = linear.shape[:2]
        ramp = np.linspace(1.0, 1.0 - shadow_strength, width)[None, :, None]
        linear = linear * ramp

    linear = np.clip(linear, 0.0, 1.0)
    img = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
    out = np.clip(img * 255.0, 0, 255)

    if noise_sigma > 0:
        out = np.clip(out + rng.normal(0.0, noise_sigma, out.shape), 0, 255)

    out = out.astype(np.uint8)

    # Place the card on a background, optionally at an angle.
    height, width = out.shape[:2]
    pad_x, pad_y = int(width * 0.22), int(height * 0.30)
    canvas = np.zeros((height + 2 * pad_y, width + 2 * pad_x, 3), dtype=np.uint8)
    canvas[:] = background[::-1]

    if perspective > 0:
        src = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
        )
        jitter = perspective * min(width, height)
        dst = src + rng.uniform(-jitter, jitter, src.shape).astype(np.float32)
        dst[:, 0] += pad_x
        dst[:, 1] += pad_y
        matrix = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(
            out, matrix, (canvas.shape[1], canvas.shape[0]), borderValue=background[::-1]
        )
        mask = cv2.warpPerspective(
            np.full((height, width), 255, np.uint8), matrix, (canvas.shape[1], canvas.shape[0])
        )
        canvas[mask > 0] = warped[mask > 0]
    else:
        canvas[pad_y : pad_y + height, pad_x : pad_x + width] = out

    if scale != 1.0:
        canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if jpeg_quality is not None:
        ok, buf = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if ok:
            canvas = cv2.imdecode(buf, cv2.IMREAD_COLOR)

    return canvas


def market_capture(
    strip_lab: tuple[float, float, float],
    *,
    spec: ReferenceCardSpec = SATVA_CARD_V1,
    seed: int = 7,
    **overrides,
) -> np.ndarray:
    """A representative 'photographed in a market' frame: warm light, slight
    under-exposure, perspective, noise and JPEG artefacts."""
    defaults = dict(
        illuminant_gain=(1.10, 1.00, 0.82),
        exposure=0.92,
        shadow_strength=0.06,
        perspective=0.012,
        noise_sigma=1.4,
        jpeg_quality=86,
        seed=seed,
    )
    defaults.update(overrides)
    return apply_capture_conditions(render_card(strip_lab, spec), **defaults)
