"""Reagent calibration series and the reading they produce.

A calibration series is an ordered list of **stops**: the CIE L*a*b* colour the
reagent strip takes at a known concentration, measured under the reference card
on the reference print run. Reading a strip means locating the corrected strip
colour along the piecewise-linear path those stops trace through Lab space.

Provenance and honesty
----------------------
The numeric series below are **provisional engineering calibrations**. They
encode the correct shape of each reagent's reaction path and produce a
self-consistent, reproducible number, but they have not been validated against a
NABL-accredited laboratory. Spec 19 lists that validation as post-hackathon
work, and `is_lab_validated` is False on every series shipped here. The API
surfaces that flag, the mobile UI renders it, and the complaint document prints
it, so nobody downstream can mistake a provisional reading for a laboratory
result.

Adding a reagent
----------------
Append a `CalibrationSeries` to `CALIBRATIONS`. Nothing else changes: the
pipeline, the confidence model, the API and the mobile client are all driven by
this table. That is what "modular so new reagents can be added later" means in
practice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.core.constants import ReagentAssay
from app.services.colorimetry.color_math import delta_e_2000


@dataclass(frozen=True)
class CalibrationStop:
    concentration: float
    lab: tuple[float, float, float]
    label: str | None = None


@dataclass(frozen=True)
class CalibrationSeries:
    assay: ReagentAssay
    calibration_id: str
    display_name: str
    matrix_description: str
    unit: str
    stops: tuple[CalibrationStop, ...]
    action_threshold: float
    action_threshold_rationale: str
    # Repeatability of the chemistry itself, expressed as the Lab-space spread
    # observed between duplicate strips at the same concentration. Combined with
    # the optical correction residual to form the reported interval.
    reagent_repeatability_de: float
    # How far off the calibration path a colour may sit before we conclude the
    # thing being measured is not this reagent at all.
    max_off_path_de: float
    is_lab_validated: bool = False
    supported_crops: tuple[str, ...] = ()
    notes: str = ""

    @property
    def lab_path(self) -> np.ndarray:
        return np.array([s.lab for s in self.stops], dtype=np.float64)

    @property
    def concentrations(self) -> np.ndarray:
        return np.array([s.concentration for s in self.stops], dtype=np.float64)

    def band_label(self, concentration: float) -> str:
        """Human-facing band. Deliberately plain language, no verdict wording."""
        below = [s for s in self.stops if s.concentration <= concentration and s.label]
        return below[-1].label if below else (self.stops[0].label or "below lowest stop")


TURMERIC_CARBIDE = CalibrationSeries(
    assay=ReagentAssay.TURMERIC_CARBIDE,
    calibration_id="turmeric-carbide-v1",
    display_name="Turmeric indicator - carbide residue",
    matrix_description="Surface wash from fruit skin, applied to turmeric indicator paper",
    unit="mg/L equivalent (indicative)",
    # Stops trace the curcumin indicator's yellow -> orange -> red-brown path.
    # Every stop is kept inside the sRGB gamut with headroom: a stop printed at
    # the gamut ceiling would clip the red channel under any warm illuminant,
    # and the reading that would be lost is "not detected" -- the very result
    # that clears an honest vendor.
    stops=(
        CalibrationStop(0.0, (79.0, 3.0, 66.0), "not detected"),
        CalibrationStop(0.5, (74.0, 12.0, 64.0), "trace"),
        CalibrationStop(1.0, (67.0, 24.0, 60.0), "detected"),
        CalibrationStop(2.5, (59.0, 36.0, 51.0), "clearly detected"),
        CalibrationStop(5.0, (51.0, 45.0, 41.0), "strong"),
        CalibrationStop(10.0, (43.0, 50.0, 31.0), "very strong"),
    ),
    action_threshold=1.0,
    action_threshold_rationale=(
        "Calcium carbide ripening is prohibited outright under sub-regulation 2.3.5 of "
        "the Food Safety and Standards (Prohibition and Restrictions on Sales) "
        "Regulations, 2011, so the action threshold is the level at which the "
        "indicator response is distinguishable from an unreacted strip rather than a "
        "permitted concentration."
    ),
    reagent_repeatability_de=2.0,
    max_off_path_de=18.0,
    supported_crops=("mango", "banana", "papaya", "tomato"),
    notes=(
        "Turmeric paper responds to the alkalinity associated with carbide residue. "
        "It is an indicator reaction, not a specific assay for calcium carbide: a "
        "positive result means confirmatory laboratory testing is warranted."
    ),
)

STARCH_IODINE_MILK = CalibrationSeries(
    assay=ReagentAssay.STARCH_IODINE_MILK,
    calibration_id="starch-iodine-milk-v1",
    display_name="Starch-iodine - added starch in milk",
    matrix_description="Boiled and cooled milk sample, iodine reagent on strip",
    unit="% w/v added starch (indicative)",
    stops=(
        CalibrationStop(0.0, (78.0, 2.0, 22.0), "not detected"),
        CalibrationStop(0.25, (62.0, 4.0, 2.0), "trace"),
        CalibrationStop(0.5, (46.0, 6.0, -18.0), "detected"),
        CalibrationStop(1.0, (34.0, 8.0, -28.0), "clearly detected"),
        # Kept off the dark gamut edge: a stop that clips to zero in a channel
        # loses colour information and reads as sensor noise, not chemistry.
        CalibrationStop(2.0, (27.0, 6.0, -30.0), "strong"),
    ),
    action_threshold=0.25,
    action_threshold_rationale="Starch is not a permitted addition to milk at any level.",
    reagent_repeatability_de=2.4,
    max_off_path_de=20.0,
    notes="Blue-black starch-iodine complex. Sample must be boiled and cooled first.",
)

FORMALIN_FISH = CalibrationSeries(
    assay=ReagentAssay.FORMALIN_FISH,
    calibration_id="formalin-fish-v1",
    display_name="Formalin in fish",
    matrix_description="Surface wash from fish, chromotropic-acid style reagent strip",
    unit="ppm formaldehyde (indicative)",
    stops=(
        CalibrationStop(0.0, (88.0, -1.0, 6.0), "not detected"),
        CalibrationStop(2.0, (76.0, 14.0, 8.0), "trace"),
        CalibrationStop(5.0, (62.0, 32.0, 4.0), "detected"),
        CalibrationStop(10.0, (48.0, 46.0, -6.0), "clearly detected"),
        CalibrationStop(20.0, (36.0, 52.0, -14.0), "strong"),
    ),
    action_threshold=2.0,
    action_threshold_rationale="Formaldehyde is not permitted as a fish preservative.",
    reagent_repeatability_de=2.6,
    max_off_path_de=20.0,
)

METANIL_YELLOW_TURMERIC = CalibrationSeries(
    assay=ReagentAssay.METANIL_YELLOW_TURMERIC,
    calibration_id="metanil-yellow-turmeric-v1",
    display_name="Metanil yellow in turmeric powder",
    matrix_description="Turmeric powder in water with dilute hydrochloric acid",
    unit="relative response (indicative)",
    stops=(
        CalibrationStop(0.0, (76.0, 6.0, 64.0), "not detected"),
        CalibrationStop(1.0, (68.0, 30.0, 58.0), "detected"),
        CalibrationStop(2.0, (56.0, 46.0, 40.0), "clearly detected"),
        CalibrationStop(3.0, (46.0, 56.0, 26.0), "strong"),
    ),
    action_threshold=1.0,
    action_threshold_rationale="Metanil yellow is a non-permitted colour in any quantity.",
    reagent_repeatability_de=2.2,
    max_off_path_de=20.0,
    notes="A pink-to-magenta shift on acidification indicates metanil yellow.",
)

ARGEMONE_MUSTARD_OIL = CalibrationSeries(
    assay=ReagentAssay.ARGEMONE_MUSTARD_OIL,
    calibration_id="argemone-mustard-v1",
    display_name="Argemone oil in mustard oil",
    matrix_description="Mustard oil with nitric acid reagent (Bellier-style spot test)",
    unit="relative response (indicative)",
    stops=(
        CalibrationStop(0.0, (72.0, 6.0, 64.0), "not detected"),
        CalibrationStop(1.0, (60.0, 26.0, 52.0), "detected"),
        CalibrationStop(2.0, (48.0, 42.0, 36.0), "clearly detected"),
        CalibrationStop(3.0, (38.0, 50.0, 22.0), "strong"),
    ),
    action_threshold=1.0,
    action_threshold_rationale="Argemone oil is not permitted in edible oil at any level.",
    reagent_repeatability_de=2.8,
    max_off_path_de=22.0,
)

CALIBRATIONS: dict[ReagentAssay, CalibrationSeries] = {
    s.assay: s
    for s in (
        TURMERIC_CARBIDE,
        STARCH_IODINE_MILK,
        FORMALIN_FISH,
        METANIL_YELLOW_TURMERIC,
        ARGEMONE_MUSTARD_OIL,
    )
}


def get_calibration(assay: str | ReagentAssay) -> CalibrationSeries | None:
    try:
        return CALIBRATIONS[ReagentAssay(assay)]
    except (ValueError, KeyError):
        return None


@dataclass
class PathProjection:
    """Where a measured colour sits on the calibration path."""

    concentration: float
    off_path_de: float
    nearest_stop_de: float
    segment_index: int
    segment_fraction: float
    local_sensitivity: float  # concentration units per unit of Lab distance


def _segment_projection(point: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Project a point onto segment ``ab``. Returns (t in [0,1], distance)."""
    ab = b - a
    denominator = float(ab @ ab)
    if denominator < 1e-12:
        return 0.0, float(np.linalg.norm(point - a))
    t = float(np.clip((point - a) @ ab / denominator, 0.0, 1.0))
    return t, float(np.linalg.norm(point - (a + t * ab)))


def project_onto_path(lab: np.ndarray, series: CalibrationSeries) -> PathProjection:
    """Locate a corrected strip colour along a calibration series.

    Projection happens in Lab as a Euclidean geometry problem (finding the
    closest point on a polyline), while the *reported* distances use CIEDE2000.
    Mixing the two is deliberate: CIEDE2000 is not a metric and does not admit a
    well-defined projection, but it is the right measure for "how different do
    these two colours look", which is what the off-path and nearest-stop
    diagnostics mean.
    """
    point = np.asarray(lab, dtype=np.float64)
    path = series.lab_path
    concentrations = series.concentrations

    best_index = 0
    best_t = 0.0
    best_distance = float("inf")
    for i in range(len(path) - 1):
        t, distance = _segment_projection(point, path[i], path[i + 1])
        if distance < best_distance:
            best_index, best_t, best_distance = i, t, distance

    lo_c = float(concentrations[best_index])
    hi_c = float(concentrations[best_index + 1])
    concentration = lo_c + best_t * (hi_c - lo_c)

    segment_length = float(np.linalg.norm(path[best_index + 1] - path[best_index]))
    sensitivity = (hi_c - lo_c) / segment_length if segment_length > 1e-9 else 0.0

    foot = path[best_index] + best_t * (path[best_index + 1] - path[best_index])
    off_path_de = float(delta_e_2000(point, foot))
    nearest_stop_de = float(
        np.min(np.atleast_1d(delta_e_2000(np.tile(point, (len(path), 1)), path)))
    )

    return PathProjection(
        concentration=concentration,
        off_path_de=off_path_de,
        nearest_stop_de=nearest_stop_de,
        segment_index=best_index,
        segment_fraction=best_t,
        local_sensitivity=sensitivity,
    )


def confidence_interval(
    projection: PathProjection,
    series: CalibrationSeries,
    optical_uncertainty_de: float,
) -> tuple[float, float]:
    """Turn colour-space uncertainty into a concentration interval.

    The two independent error sources are combined in quadrature:

    * ``optical_uncertainty_de`` -- how far the corrected colour could be from
      the true colour, taken from the leave-one-out correction residual.
    * ``series.reagent_repeatability_de`` -- how much two identical strips at
      the same concentration differ from each other.

    The combined Lab-space uncertainty is mapped to concentration through the
    local slope of the calibration path, then clamped to the calibrated range.
    Extrapolating past the end stops would be guessing, and SATVA does not
    guess: a value that projects to an end stop is reported at that stop with
    the range flag set by the caller.
    """
    combined_de = float(
        np.hypot(max(optical_uncertainty_de, 0.0), series.reagent_repeatability_de)
    )
    half_width = combined_de * abs(projection.local_sensitivity)

    lo_bound = float(series.concentrations.min())
    hi_bound = float(series.concentrations.max())
    low = max(lo_bound, projection.concentration - half_width)
    high = min(hi_bound, projection.concentration + half_width)
    return (round(low, 4), round(high, 4))
