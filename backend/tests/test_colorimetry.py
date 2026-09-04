"""Colorimetry tests.

These cover the two things that matter most about Layer B:

1. the maths is correct (checked against published reference data, not against
   our own output), and
2. the pipeline **refuses** under every condition spec 7 lists, rather than
   returning a weak number.

The second group is the more important one. A wrong reading attached to an
FSSAI complaint is the specific harm non-negotiable rules 1-3 exist to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.core.constants import ColorimetryRejectReason
from app.services.colorimetry.calibration import (
    CALIBRATIONS,
    TURMERIC_CARBIDE,
    confidence_interval,
    get_calibration,
    project_onto_path,
)
from app.services.colorimetry.color_math import (
    delta_e_2000,
    lab_to_srgb,
    srgb_to_lab,
    srgb_u8_to_lab,
)
from app.services.colorimetry.correction import fit_best_correction, fit_correction
from app.services.colorimetry.pipeline import read_strip
from tests.fixtures.card_render import apply_capture_conditions, market_capture, render_card

GOLDEN = Path(__file__).resolve().parents[2] / "docs" / "colorimetry" / "golden_vectors.json"

# Sharma, Wu & Dalal (2005), supplementary CIEDE2000 test data.
SHARMA_PAIRS = [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412),
    ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, 2.4900, -0.0010), (50.0, -2.4900, 0.0009), 7.1792),
    ((50.0, 2.4900, -0.0010), (50.0, -2.4900, 0.0011), 7.2195),
    ((50.0, -0.0010, 2.4900), (50.0, 0.0011, -2.4900), 4.7461),
    ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((50.0, 2.5, 0.0), (56.0, -27.0, -3.0), 31.9030),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


class TestColourMaths:
    @pytest.mark.parametrize(("lab1", "lab2", "expected"), SHARMA_PAIRS)
    def test_ciede2000_matches_published_reference_data(self, lab1, lab2, expected):
        got = delta_e_2000(np.array(lab1), np.array(lab2))
        assert got == pytest.approx(expected, abs=1e-4)

    def test_ciede2000_is_symmetric(self):
        for lab1, lab2, _ in SHARMA_PAIRS:
            forward = delta_e_2000(np.array(lab1), np.array(lab2))
            backward = delta_e_2000(np.array(lab2), np.array(lab1))
            assert forward == pytest.approx(backward, abs=1e-9)

    def test_ciede2000_of_identical_colours_is_zero(self):
        for lab, _, _ in SHARMA_PAIRS:
            assert delta_e_2000(np.array(lab), np.array(lab)) == pytest.approx(0.0, abs=1e-12)

    def test_known_srgb_lab_anchors(self):
        assert srgb_to_lab(np.array([1.0, 1.0, 1.0]))[0] == pytest.approx(100.0, abs=1e-6)
        assert srgb_to_lab(np.array([0.0, 0.0, 0.0]))[0] == pytest.approx(0.0, abs=1e-9)
        red = srgb_to_lab(np.array([1.0, 0.0, 0.0]))
        assert red[0] == pytest.approx(53.2408, abs=1e-3)
        assert red[1] == pytest.approx(80.0925, abs=1e-3)
        assert red[2] == pytest.approx(67.2032, abs=1e-3)

    def test_neutral_colours_have_zero_chroma(self):
        for level in (0.1, 0.25, 0.5, 0.75, 0.95):
            lab = srgb_to_lab(np.array([level, level, level]))
            assert abs(lab[1]) < 1e-9
            assert abs(lab[2]) < 1e-9

    def test_srgb_lab_round_trip(self):
        rng = np.random.default_rng(4)
        samples = rng.uniform(0.02, 0.98, size=(200, 3))
        recovered = lab_to_srgb(srgb_to_lab(samples))
        assert np.max(np.abs(recovered - samples)) < 1e-6

    def test_lab_averaging_happens_in_linear_light(self):
        """The mean of two greys must sit at their mean *luminance*, not at the
        mean of their gamma-encoded codes."""
        from app.services.colorimetry.color_math import mean_lab

        pixels = np.array([[0, 0, 0], [255, 255, 255]] * 50, dtype=np.uint8)
        lab = mean_lab(pixels)
        # Y = 0.5 gives L* ~= 76.07. Averaging in sRGB would give L* ~= 53.4.
        assert lab[0] == pytest.approx(76.069, abs=0.01)


class TestCorrection:
    def _patches(self):
        expected = np.array(
            [
                [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121],
                [85, 85, 85], [52, 52, 52], [222, 118, 32], [231, 199, 31],
                [187, 86, 149], [98, 122, 157], [87, 108, 67], [170, 65, 51],
            ],
            dtype=np.float64,
        )
        return expected

    def test_identity_observation_fits_identity_correction(self):
        expected = self._patches()
        correction = fit_correction(expected, expected, "per_channel")
        assert correction.mean_residual_de < 1e-6
        assert np.allclose(correction.matrix, np.eye(3), atol=1e-6)
        assert np.allclose(correction.offset, 0.0, atol=1e-6)

    @staticmethod
    def _tint(expected: np.ndarray, gain: tuple[float, float, float]) -> np.ndarray:
        from app.services.colorimetry.color_math import linear_to_srgb, srgb_to_linear

        return np.clip(
            linear_to_srgb(srgb_to_linear(expected / 255.0) * np.array(gain)) * 255.0, 0, 255
        )

    def test_correction_recovers_a_tinted_illuminant(self):
        """A warm light source multiplies linear RGB; the fit must undo it."""
        expected = self._patches()
        observed = self._tint(expected, (1.08, 1.0, 0.82))
        correction = fit_correction(observed, expected, "per_channel")
        assert correction.mean_residual_de < 0.5
        recovered = correction.apply_to_lab(observed[6])
        assert delta_e_2000(recovered, srgb_u8_to_lab(expected[6])) < 1.0

    def test_clipping_degrades_the_fit_and_is_therefore_detectable(self):
        """A tint strong enough to clip the brightest patch breaks the linear
        relationship the correction assumes. The residual must rise, because
        that residual is what the quality gate uses to refuse the reading."""
        expected = self._patches()
        clean = fit_correction(expected, expected, "per_channel").mean_residual_de
        clipped = fit_correction(
            self._tint(expected, (1.30, 1.0, 0.78)), expected, "per_channel"
        )
        assert clipped.mean_residual_de > clean
        assert clipped.worst_cv_residual_de > 1.0

    def test_cross_validated_residual_is_not_better_than_in_sample(self):
        """Leave-one-out must be an honest estimate, never flattering."""
        rng = np.random.default_rng(11)
        expected = self._patches()
        observed = np.clip(expected * 1.1 + rng.normal(0, 4, expected.shape), 0, 255)
        correction = fit_correction(observed, expected, "affine33")
        assert correction.mean_cv_residual_de >= correction.mean_residual_de - 1e-9

    def test_best_model_selection_prefers_lower_cv_residual(self):
        rng = np.random.default_rng(5)
        expected = self._patches()
        observed = np.clip(expected * 1.05 + rng.normal(0, 2, expected.shape), 0, 255)
        best = fit_best_correction(observed, expected)
        candidates = [
            fit_correction(observed, expected, "per_channel"),
            fit_correction(observed, expected, "affine33"),
        ]
        assert best.mean_cv_residual_de == pytest.approx(
            min(c.mean_cv_residual_de for c in candidates)
        )

    def test_too_few_patches_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError):
            fit_correction(np.array([[10, 10, 10]]), np.array([[12, 12, 12]]), "per_channel")


class TestCalibration:
    def test_each_stop_projects_to_its_own_concentration(self):
        for series in CALIBRATIONS.values():
            for stop in series.stops:
                projection = project_onto_path(np.array(stop.lab), series)
                assert projection.concentration == pytest.approx(stop.concentration, abs=1e-6)
                assert projection.off_path_de == pytest.approx(0.0, abs=1e-6)

    def test_projection_is_monotonic_along_the_path(self):
        series = TURMERIC_CARBIDE
        path = series.lab_path
        previous = -1.0
        for i in range(len(path) - 1):
            for t in (0.0, 0.25, 0.5, 0.75):
                point = path[i] + t * (path[i + 1] - path[i])
                value = project_onto_path(point, series).concentration
                assert value >= previous - 1e-9
                previous = value

    def test_never_extrapolates_beyond_the_calibrated_range(self):
        series = TURMERIC_CARBIDE
        beyond = np.array(series.stops[-1].lab) + (
            np.array(series.stops[-1].lab) - np.array(series.stops[-2].lab)
        )
        projection = project_onto_path(beyond, series)
        assert projection.concentration <= float(series.concentrations.max()) + 1e-9

    def test_confidence_interval_widens_with_optical_uncertainty(self):
        series = TURMERIC_CARBIDE
        projection = project_onto_path(np.array(series.stops[2].lab), series)
        tight = confidence_interval(projection, series, 0.2)
        loose = confidence_interval(projection, series, 6.0)
        assert (loose[1] - loose[0]) > (tight[1] - tight[0])

    def test_interval_never_leaves_the_calibrated_range(self):
        series = TURMERIC_CARBIDE
        for stop in series.stops:
            projection = project_onto_path(np.array(stop.lab), series)
            low, high = confidence_interval(projection, series, 20.0)
            assert low >= float(series.concentrations.min()) - 1e-9
            assert high <= float(series.concentrations.max()) + 1e-9

    def test_no_shipped_series_claims_laboratory_validation(self):
        """Guards the honesty requirement: nothing here is NABL-validated yet."""
        for series in CALIBRATIONS.values():
            assert series.is_lab_validated is False

    def test_all_stops_sit_inside_the_srgb_gamut_with_headroom(self):
        """A stop at the gamut edge clips under real illumination and becomes
        unreadable -- which would silently remove the 'not detected' result."""
        for series in CALIBRATIONS.values():
            for stop in series.stops:
                rgb = lab_to_srgb(np.array(stop.lab)) * 255.0
                assert rgb.max() <= 240.0, f"{series.assay} @ {stop.concentration} too bright"
                assert rgb.min() >= 8.0, f"{series.assay} @ {stop.concentration} too dark"

    def test_unknown_assay_returns_none_rather_than_a_default(self):
        assert get_calibration("not_a_real_reagent") is None


class TestPipelineAccuracy:
    @pytest.mark.parametrize("stop_index", range(len(TURMERIC_CARBIDE.stops)))
    def test_reads_each_carbide_stop_under_market_capture(self, stop_index):
        stop = TURMERIC_CARBIDE.stops[stop_index]
        result = read_strip(market_capture(stop.lab, seed=11), "turmeric_carbide")
        assert result.accepted, f"refused: {result.reject_reason} / {result.reject_detail}"
        full_scale = float(TURMERIC_CARBIDE.concentrations.max())
        assert abs(result.concentration - stop.concentration) < 0.05 * full_scale
        assert result.ci_low <= stop.concentration <= result.ci_high

    @pytest.mark.parametrize("assay", [str(a) for a in CALIBRATIONS])
    def test_every_reagent_reads_its_own_series(self, assay):
        series = CALIBRATIONS[next(a for a in CALIBRATIONS if str(a) == assay)]
        for stop in series.stops:
            result = read_strip(market_capture(stop.lab, seed=3), assay)
            assert result.accepted, f"{assay} @ {stop.concentration}: {result.reject_reason}"

    def test_result_is_deterministic_for_the_same_input(self):
        image = market_capture(TURMERIC_CARBIDE.stops[3].lab, seed=17)
        first = read_strip(image, "turmeric_carbide")
        second = read_strip(image, "turmeric_carbide")
        assert first.concentration == second.concentration
        assert first.strip_lab_corrected == second.strip_lab_corrected

    def test_upper_range_reading_is_flagged_as_a_bound(self):
        top = TURMERIC_CARBIDE.stops[-1]
        result = read_strip(market_capture(top.lab, seed=11), "turmeric_carbide")
        assert result.accepted
        assert result.at_range_limit is True
        assert result.range_limit_side == "upper"

    def test_action_threshold_uses_the_lower_confidence_bound(self):
        """Escalating on the point estimate would flag readings whose interval
        still spans 'not detected'. Rule 1 makes that unacceptable."""
        clean = read_strip(market_capture(TURMERIC_CARBIDE.stops[0].lab, seed=11), "turmeric_carbide")
        assert clean.accepted
        assert clean.exceeds_action_threshold is False

        strong = read_strip(market_capture(TURMERIC_CARBIDE.stops[4].lab, seed=11), "turmeric_carbide")
        assert strong.accepted
        assert strong.exceeds_action_threshold is True
        assert strong.ci_low >= TURMERIC_CARBIDE.action_threshold


class TestPipelineRefusal:
    """Non-negotiable rule 3: refuse, never report weakly."""

    def _clean_strip(self):
        return TURMERIC_CARBIDE.stops[2].lab

    def test_refuses_when_no_reference_card_is_present(self):
        blank = np.full((640, 480, 3), 130, dtype=np.uint8)
        result = read_strip(blank, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason == ColorimetryRejectReason.REFERENCE_CARD_NOT_FOUND
        assert result.concentration is None

    def test_refuses_an_over_exposed_frame(self):
        image = apply_capture_conditions(render_card(self._clean_strip()), exposure=2.6)
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason in {
            ColorimetryRejectReason.EXPOSURE_OUT_OF_RANGE,
            ColorimetryRejectReason.CLIPPED_HIGHLIGHTS,
        }

    def test_refuses_an_under_exposed_frame(self):
        image = apply_capture_conditions(render_card(self._clean_strip()), exposure=0.15)
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason in {
            ColorimetryRejectReason.EXPOSURE_OUT_OF_RANGE,
            ColorimetryRejectReason.CLIPPED_SHADOWS,
        }

    def test_refuses_a_heavily_tinted_light_source(self):
        image = apply_capture_conditions(
            render_card(self._clean_strip()), illuminant_gain=(1.45, 1.0, 0.45), exposure=0.62
        )
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason == ColorimetryRejectReason.ILLUMINANT_TOO_TINTED

    def test_refuses_when_a_shadow_falls_across_the_card(self):
        image = apply_capture_conditions(render_card(self._clean_strip()), shadow_strength=0.55)
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason in {
            ColorimetryRejectReason.NON_UNIFORM_ILLUMINATION,
            ColorimetryRejectReason.CORRECTION_RESIDUAL_TOO_HIGH,
        }

    def test_refuses_when_the_strip_well_is_empty(self):
        image = apply_capture_conditions(render_card(None, strip_present=False))
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False

    def test_refuses_a_colour_that_is_not_on_the_reaction_path(self):
        image = apply_capture_conditions(render_card((55.0, -45.0, 30.0)))
        result = read_strip(image, "turmeric_carbide")
        assert result.accepted is False
        assert result.reject_reason == ColorimetryRejectReason.OUT_OF_CALIBRATION_RANGE

    def test_refuses_an_unregistered_reagent(self):
        image = apply_capture_conditions(render_card(self._clean_strip()))
        result = read_strip(image, "chlorine_bleach")
        assert result.accepted is False
        assert result.reject_reason == ColorimetryRejectReason.UNKNOWN_REAGENT

    def test_refusal_never_carries_a_number(self):
        """The contract the rest of the system depends on."""
        cases = [
            np.full((640, 480, 3), 130, dtype=np.uint8),
            apply_capture_conditions(render_card(self._clean_strip()), exposure=2.6),
            apply_capture_conditions(render_card((55.0, -45.0, 30.0))),
        ]
        for image in cases:
            result = read_strip(image, "turmeric_carbide")
            assert result.accepted is False
            assert result.concentration is None
            assert result.ci_low is None
            assert result.ci_high is None
            assert result.exceeds_action_threshold is False
            assert result.reject_reason is not None
            assert result.reject_detail


class TestGoldenVectors:
    """Keeps the Python reference and the Dart on-device port in lockstep."""

    @pytest.fixture(scope="class")
    def golden(self):
        if not GOLDEN.exists():
            pytest.skip("golden vectors not generated")
        return json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_srgb_to_lab_vectors(self, golden):
        for case in golden["srgb_u8_to_lab"]:
            got = srgb_u8_to_lab(np.array(case["rgb"], dtype=np.float64))
            assert np.allclose(got, case["lab"], atol=1e-6)

    def test_delta_e_vectors(self, golden):
        for case in golden["delta_e_2000"]:
            got = delta_e_2000(np.array(case["lab1"]), np.array(case["lab2"]))
            assert got == pytest.approx(case["de"], abs=1e-6)

    def test_projection_vectors(self, golden):
        for case in golden["calibration_projection"]:
            series = get_calibration(case["assay"])
            projection = project_onto_path(np.array(case["lab"]), series)
            assert projection.concentration == pytest.approx(case["concentration"], abs=1e-6)
            low, high = confidence_interval(projection, series, case["optical_uncertainty_de"])
            assert low == pytest.approx(case["ci_low"], abs=1e-6)
            assert high == pytest.approx(case["ci_high"], abs=1e-6)

    def test_calibration_table_matches_shipped_series(self, golden):
        """The Dart client reads its calibrations from this file. If the Python
        table changes without regenerating it, the handset silently uses stale
        chemistry -- so the two must be checked against each other."""
        for assay, expected in golden["calibrations"].items():
            series = get_calibration(assay)
            assert series is not None
            assert series.calibration_id == expected["calibration_id"]
            assert series.action_threshold == expected["action_threshold"]
            assert [s.concentration for s in series.stops] == [
                s["concentration"] for s in expected["stops"]
            ]
            assert [list(s.lab) for s in series.stops] == [s["lab"] for s in expected["stops"]]
