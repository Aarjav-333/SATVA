"""Generate the SATVA development dataset.

    python -m satva_ml.make_dev_dataset --out data --per-class 400

What this is, stated plainly
---------------------------
This renders **procedurally generated images of fruit-like objects**. It is not
photographs of real produce, and a model trained on it has learned nothing about
real mangoes. Its purpose is to let the full pipeline -- training, calibration,
ONNX export, INT8 quantisation, on-device inference -- be built, run and
verified end to end before the field and controlled-lab datasets exist.

Every artefact produced from this data is labelled `DEV`, the model card records
it, and the mobile app displays "development model" wherever a score appears.
The numbers a DEV model produces must never be quoted as accuracy figures.

Why render these particular features
------------------------------------
The generator draws the morphological signatures the specification names, so the
model has genuine visual structure to learn rather than a trivially separable
colour shift:

* **uniform skin colour while the calyx stays green** -- forced ripening colours
  the skin without advancing the stem end, so the class-1 renders have low
  hue variance across the body but a persistently green calyx;
* **speckled surface burns** -- small dark lesions where carbide contacted skin;
* **unnatural sheen** -- a tight specular highlight, versus the broad diffuse
  highlight of a naturally ripened fruit;
* **texture lagging colour** -- class-1 renders keep the finer surface texture
  of an unripe fruit under ripe-looking colour.

Both classes share lighting, background, blur and compression variation, so the
model cannot separate them on capture conditions alone -- which is the mistake
that would make an evaluation meaningless.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CROPS = ["mango", "banana", "papaya", "tomato"]
CULTIVARS = {
    "mango": ["banganapalli", "alphonso", "neelam", "totapuri"],
    "banana": ["nendran", "robusta", "poovan"],
    "papaya": ["red_lady"],
    "tomato": ["unspecified"],
}

# Ripe skin colours per crop, as (R, G, B) centres in sRGB.
RIPE_COLOUR = {
    "mango": (226, 168, 52),
    "banana": (232, 205, 74),
    "papaya": (232, 146, 62),
    "tomato": (198, 56, 42),
}
UNRIPE_COLOUR = {
    "mango": (104, 142, 58),
    "banana": (140, 168, 70),
    "papaya": (118, 150, 72),
    "tomato": (126, 158, 70),
}
CALYX_GREEN = (72, 108, 46)

SIZE = 256


def _shade(base: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(int(np.clip(c * factor, 0, 255)) for c in base)


def render_fruit(
    rng: np.random.Generator,
    crop: str,
    *,
    forced: bool,
) -> tuple[Image.Image, float]:
    """Render one fruit. Returns (image, ripeness_index)."""
    image = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    # --- Background: market-stall-ish, varied, identical across classes -----
    bg_base = rng.integers(90, 190, size=3)
    for y in range(SIZE):
        shade = 1.0 - 0.25 * (y / SIZE)
        draw.line(
            [(0, y), (SIZE, y)],
            fill=tuple(int(np.clip(c * shade + rng.normal(0, 3), 0, 255)) for c in bg_base),
        )

    # --- Body -------------------------------------------------------------
    ripeness = float(rng.uniform(0.55, 0.95) if forced else rng.uniform(0.25, 0.95))
    ripe = np.array(RIPE_COLOUR[crop], dtype=np.float64)
    unripe = np.array(UNRIPE_COLOUR[crop], dtype=np.float64)

    # Forced ripening colours the skin more completely than natural ripening at
    # the same physiological age -- the core visual signature in the spec.
    colour_progress = min(1.0, ripeness * (1.35 if forced else 1.0))
    body = unripe + (ripe - unripe) * colour_progress

    cx, cy = SIZE // 2 + rng.integers(-14, 15), SIZE // 2 + rng.integers(-10, 11)
    rx = int(SIZE * rng.uniform(0.26, 0.34))
    ry = int(rx * rng.uniform(0.72, 1.05))
    if crop == "banana":
        rx, ry = int(rx * 1.35), int(ry * 0.55)

    # Per-pixel body render so colour variance can be controlled directly.
    body_layer = np.zeros((SIZE, SIZE, 3), dtype=np.float64)
    mask = np.zeros((SIZE, SIZE), dtype=bool)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    ellipse = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
    inside = ellipse <= 1.0
    mask[inside] = True

    # Natural ripening is patchy; forced ripening is unnaturally even. This is
    # the single strongest cue and it is expressed as colour *variance*, not as
    # a colour difference, so the model cannot shortcut on hue alone.
    variance = 6.0 if forced else 26.0
    blotch_scale = 34 if forced else 15
    noise = rng.normal(0.0, 1.0, (SIZE // blotch_scale + 2, SIZE // blotch_scale + 2, 3))
    noise_image = np.array(
        Image.fromarray(
            np.clip(noise * 40 + 128, 0, 255).astype(np.uint8)
        ).resize((SIZE, SIZE), Image.Resampling.BICUBIC),
        dtype=np.float64,
    )
    blotches = (noise_image - 128.0) / 40.0 * variance

    # Spherical shading so the object reads as three-dimensional.
    depth = np.sqrt(np.clip(1.0 - ellipse, 0, 1))
    lighting = 0.72 + 0.38 * depth

    for channel in range(3):
        body_layer[:, :, channel] = body[channel] * lighting + blotches[:, :, channel]

    base = np.array(image, dtype=np.float64)
    base[mask] = np.clip(body_layer[mask], 0, 255)

    # --- Surface texture ---------------------------------------------------
    # Forced-ripened fruit keeps the finer texture of an unripe fruit under
    # ripe-looking colour: texture lags colour.
    texture_amplitude = 7.0 if forced else 3.0
    texture = rng.normal(0.0, texture_amplitude, (SIZE, SIZE, 1))
    base[mask] = np.clip(base[mask] + texture[mask], 0, 255)

    image = Image.fromarray(base.astype(np.uint8))
    draw = ImageDraw.Draw(image)

    # --- Calyx / stem end --------------------------------------------------
    # Forced ripening leaves the calyx green while the body is fully coloured;
    # natural ripening advances the calyx along with the body.
    calyx_progress = 0.05 if forced else min(1.0, ripeness)
    calyx = np.array(CALYX_GREEN, dtype=np.float64) * (1 - calyx_progress) + ripe * calyx_progress
    calyx_r = max(6, int(rx * 0.20))
    calyx_x = cx + int(rx * rng.uniform(-0.15, 0.15))
    calyx_y = cy - ry + int(calyx_r * 0.6)
    draw.ellipse(
        [calyx_x - calyx_r, calyx_y - calyx_r, calyx_x + calyx_r, calyx_y + calyx_r],
        fill=tuple(int(c) for c in np.clip(calyx, 0, 255)),
    )
    # A short stem, so the calyx region is not a bare disc.
    draw.line(
        [(calyx_x, calyx_y), (calyx_x + rng.integers(-6, 7), calyx_y - calyx_r - 8)],
        fill=_shade(CALYX_GREEN, 0.8),
        width=max(2, calyx_r // 3),
    )

    # --- Carbide speckling -------------------------------------------------
    if forced and rng.random() < 0.75:
        for _ in range(int(rng.integers(8, 34))):
            angle = rng.uniform(0, 2 * math.pi)
            radius = rng.uniform(0, 0.86)
            px = int(cx + math.cos(angle) * rx * radius)
            py = int(cy + math.sin(angle) * ry * radius)
            spot = int(rng.integers(1, 4))
            darkness = rng.uniform(0.35, 0.68)
            patch = image.crop((px - spot, py - spot, px + spot, py + spot))
            if patch.size[0] and patch.size[1]:
                image.paste(
                    Image.eval(patch, lambda v: int(v * darkness)), (px - spot, py - spot)
                )

    # --- Specular highlight ------------------------------------------------
    # Waxed / forced-ripened skin gives a tight, bright highlight; natural skin
    # gives a broad, soft one.
    highlight = Image.new("L", (SIZE, SIZE), 0)
    hdraw = ImageDraw.Draw(highlight)
    hx = cx - int(rx * 0.34)
    hy = cy - int(ry * 0.38)
    if forced:
        hr = max(4, int(rx * rng.uniform(0.10, 0.17)))
        strength = int(rng.integers(120, 190))
        blur = 3
    else:
        hr = max(6, int(rx * rng.uniform(0.22, 0.34)))
        strength = int(rng.integers(45, 90))
        blur = 9
    hdraw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=strength)
    highlight = highlight.filter(ImageFilter.GaussianBlur(blur))
    image = Image.composite(Image.new("RGB", (SIZE, SIZE), (255, 255, 255)), image, highlight)

    # --- Shared capture variation -----------------------------------------
    # Applied identically to both classes so capture conditions carry no label
    # information.
    if rng.random() < 0.5:
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 1.1)))
    array = np.array(image, dtype=np.float64)
    array *= rng.uniform(0.72, 1.28)                       # exposure
    array *= np.array([rng.uniform(0.88, 1.14), 1.0, rng.uniform(0.84, 1.14)])  # white balance
    array += rng.normal(0, rng.uniform(1.5, 6.0), array.shape)                  # sensor noise
    image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))

    return image, ripeness


def generate(out_dir: Path, per_class: int, seed: int = 20260904) -> Path:
    rng = np.random.default_rng(seed)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    index = 0
    for crop in CROPS:
        for label in (0, 1):
            for _ in range(per_class):
                image, ripeness = render_fruit(rng, crop, forced=bool(label))
                # A slice of the data is marked `controlled` so the split logic
                # has a held-out evaluation set, mirroring how the real
                # controlled-ripening batch will be used.
                source = "controlled" if rng.random() < 0.18 else "synthetic_dev"
                name = f"{crop}_{label}_{index:06d}.jpg"
                image.save(images_dir / name, quality=int(rng.integers(72, 96)))
                rows.append(
                    {
                        "image_path": f"images/{name}",
                        "crop": crop,
                        "cultivar": str(rng.choice(CULTIVARS[crop])),
                        "label": label,
                        "ripeness": round(ripeness, 4),
                        "source": source,
                        "captured_at": "2026-09-04T00:00:00Z",
                    }
                )
                index += 1

    manifest = out_dir / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    readme = out_dir / "README.md"
    readme.write_text(
        "# SATVA development dataset\n\n"
        "**These are procedurally generated images, not photographs of real produce.**\n\n"
        "They exist so the training, calibration, export and quantisation pipeline can be\n"
        "built and verified end to end before the field and controlled-lab datasets are\n"
        "collected. A model trained on this data has learned nothing about real fruit, and\n"
        "any accuracy figure measured on it says only that the pipeline runs correctly.\n\n"
        "Replace this directory with the real manifest and images; nothing else changes.\n\n"
        f"- images: {len(rows)}\n"
        f"- crops: {', '.join(CROPS)}\n"
        f"- balance: {per_class} per class per crop\n",
        encoding="utf-8",
    )

    print(f"generated {len(rows)} images -> {manifest}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the SATVA development dataset.")
    parser.add_argument("--out", default="data", type=Path)
    parser.add_argument("--per-class", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()
    generate(args.out, args.per_class, args.seed)


if __name__ == "__main__":
    main()
