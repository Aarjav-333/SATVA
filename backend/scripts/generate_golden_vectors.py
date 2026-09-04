"""Regenerate docs/colorimetry/golden_vectors.json.

The Python reference implementation and the Dart on-device implementation are
both tested against that file. Run this whenever the colour maths, the
correction model, or a calibration series changes:

    cd backend && python scripts/generate_golden_vectors.py

Then run both test suites. If the Dart tests fail afterwards, the on-device
port has drifted from the reference and must be updated -- that is exactly what
this file exists to catch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.colorimetry.calibration import (  # noqa: E402
    CALIBRATIONS,
    confidence_interval,
    project_onto_path,
)
from app.services.colorimetry.color_math import (  # noqa: E402
    delta_e_2000,
    srgb_u8_to_lab,
)
from app.services.colorimetry.correction import fit_correction  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "colorimetry" / "golden_vectors.json"

SRGB_CASES = [
    (0, 0, 0), (255, 255, 255), (128, 128, 128), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (243, 243, 242), (222, 118, 32), (231, 199, 31), (87, 108, 67), (24, 58, 168), (52, 52, 52),
]

DE_PAIRS = [
    ((50, 2.6772, -79.7751), (50, 0, -82.7485)),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009)),
    ((50, 2.5, 0), (73, 25, -18)),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387)),
    ((22.7233, 20.0904, -46.694), (23.0331, 14.973, -42.5619)),
    ((79, 3, 66), (67, 24, 60)),
    ((59, 36, 51), (51, 45, 41)),
    ((2.0776, 0.0795, -1.135), (0.9033, -0.0636, -0.5514)),
]

REFERENCE_PATCHES = np.array(
    [
        [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121],
        [85, 85, 85], [52, 52, 52], [222, 118, 32], [231, 199, 31],
        [187, 86, 149], [98, 122, 157], [87, 108, 67], [170, 65, 51],
    ],
    dtype=np.float64,
)


def build() -> dict:
    vectors: dict = {
        "_comment": (
            "Shared golden vectors. The Python reference implementation in "
            "backend/app/services/colorimetry/ and the Dart on-device implementation in "
            "mobile/lib/features/colorimetry/engine/ are both tested against this file. "
            "If the two disagree, one of them has drifted. Regenerate with "
            "backend/scripts/generate_golden_vectors.py."
        ),
        "generated_by": "backend/scripts/generate_golden_vectors.py",
        "tolerance": {"lab": 1e-6, "delta_e": 1e-6, "concentration": 1e-6},
    }

    vectors["srgb_u8_to_lab"] = [
        {
            "rgb": list(c),
            "lab": [round(float(x), 9) for x in srgb_u8_to_lab(np.array(c, dtype=np.float64))],
        }
        for c in SRGB_CASES
    ]

    vectors["delta_e_2000"] = [
        {
            "lab1": list(a),
            "lab2": list(b),
            "de": round(float(delta_e_2000(np.array(a), np.array(b))), 9),
        }
        for a, b in DE_PAIRS
    ]

    gain = np.array([1.12, 1.00, 0.84])
    observed = np.clip(REFERENCE_PATCHES * gain + np.array([3.0, -1.0, 2.0]), 0, 255)
    vectors["correction"] = {}
    for model in ("per_channel", "affine33"):
        fitted = fit_correction(observed, REFERENCE_PATCHES, model)
        vectors["correction"][model] = {
            "observed": [[round(float(x), 6) for x in row] for row in observed],
            "expected": [[int(x) for x in row] for row in REFERENCE_PATCHES],
            "matrix": [[round(float(x), 9) for x in row] for row in fitted.matrix],
            "offset": [round(float(x), 9) for x in fitted.offset],
            "mean_residual_de": round(fitted.mean_residual_de, 9),
            "mean_cv_residual_de": round(fitted.mean_cv_residual_de, 9),
        }

    projections = []
    for assay, series in CALIBRATIONS.items():
        midpoint = tuple(
            (np.array(series.stops[0].lab) + np.array(series.stops[1].lab)) / 2
        )
        for lab in (series.stops[0].lab, series.stops[len(series.stops) // 2].lab, midpoint):
            projection = project_onto_path(np.array(lab, dtype=np.float64), series)
            low, high = confidence_interval(projection, series, 0.5)
            projections.append(
                {
                    "assay": str(assay),
                    "lab": [round(float(x), 6) for x in lab],
                    "concentration": round(projection.concentration, 9),
                    "off_path_de": round(projection.off_path_de, 9),
                    "segment_index": projection.segment_index,
                    "segment_fraction": round(projection.segment_fraction, 9),
                    "optical_uncertainty_de": 0.5,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    vectors["calibration_projection"] = projections

    vectors["calibrations"] = {
        str(assay): {
            "calibration_id": series.calibration_id,
            "display_name": series.display_name,
            "unit": series.unit,
            "action_threshold": series.action_threshold,
            "reagent_repeatability_de": series.reagent_repeatability_de,
            "max_off_path_de": series.max_off_path_de,
            "is_lab_validated": series.is_lab_validated,
            "supported_crops": list(series.supported_crops),
            "stops": [
                {"concentration": s.concentration, "lab": list(s.lab), "label": s.label}
                for s in series.stops
            ],
        }
        for assay, series in CALIBRATIONS.items()
    }
    return vectors


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT}")
