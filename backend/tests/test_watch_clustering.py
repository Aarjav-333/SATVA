"""Clustering, corroboration and duplicate-detection tests.

The security-relevant assertions live here: that a single actor cannot
manufacture a public hotspot, and that the same photograph submitted twice
counts once.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.core.constants import ClusterSeverity
from app.services.watch.clustering import (
    ClusteringParameters,
    ReadingPoint,
    cluster_readings,
    haversine_metres,
)
from app.services.watch.dedup import (
    compute_phash,
    cosine_similarity,
    hamming_distance,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
# Palakkad municipal market, roughly.
BASE_LAT, BASE_LON = 10.7757, 76.6548


def offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a point by a distance in metres. Longitude scales with latitude."""
    import math

    d_lat = north_m / 111_320.0
    d_lon = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + d_lat, lon + d_lon


def reading(
    *,
    north: float = 0.0,
    east: float = 0.0,
    device: str = "dev_a",
    minutes_ago: float = 60.0,
    concentration: float = 3.0,
    exceeds: bool = True,
    ward: str = "PKD-14",
) -> ReadingPoint:
    lat, lon = offset(BASE_LAT, BASE_LON, north, east)
    return ReadingPoint(
        scan_id=uuid.uuid4(),
        latitude=lat,
        longitude=lon,
        captured_at=NOW - timedelta(minutes=minutes_ago),
        device_pseudonym=device,
        concentration=concentration,
        exceeds_action_threshold=exceeds,
        crop="mango",
        assay="turmeric_carbide",
        ward_code=ward,
        ward_name="Palakkad Ward 14",
        district="Palakkad",
        state="Kerala",
    )


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_metres(BASE_LAT, BASE_LON, BASE_LAT, BASE_LON) == pytest.approx(0.0)

    def test_known_separation_north(self):
        lat, lon = offset(BASE_LAT, BASE_LON, 1000.0, 0.0)
        assert haversine_metres(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(1000.0, rel=0.01)

    def test_known_separation_east(self):
        lat, lon = offset(BASE_LAT, BASE_LON, 0.0, 1000.0)
        assert haversine_metres(BASE_LAT, BASE_LON, lat, lon) == pytest.approx(1000.0, rel=0.01)

    def test_longitude_is_not_treated_as_latitude(self):
        """At 10.8 N a degree of longitude is ~1.7% shorter than a degree of
        latitude. Treating them alike would distort every cluster."""
        north = haversine_metres(BASE_LAT, BASE_LON, BASE_LAT + 0.01, BASE_LON)
        east = haversine_metres(BASE_LAT, BASE_LON, BASE_LAT, BASE_LON + 0.01)
        assert north > east
        assert east / north == pytest.approx(0.9823, abs=0.005)


class TestClustering:
    def test_forms_a_cluster_from_corroborating_readings(self):
        readings = [
            reading(north=0, east=0, device="dev_a", minutes_ago=600),
            reading(north=40, east=20, device="dev_b", minutes_ago=400),
            reading(north=-30, east=50, device="dev_c", minutes_ago=100),
        ]
        clusters = cluster_readings(readings, now=NOW)
        assert len(clusters) == 1
        assert clusters[0].member_count == 3
        assert clusters[0].independent_device_count == 3
        assert clusters[0].is_publishable is True

    def test_isolated_readings_are_noise_not_clusters(self):
        readings = [
            reading(north=0, east=0, device="dev_a"),
            reading(north=8000, east=0, device="dev_b"),
            reading(north=0, east=9000, device="dev_c"),
        ]
        assert cluster_readings(readings, now=NOW) == []

    def test_separate_markets_form_separate_clusters(self):
        readings = []
        for i in range(3):
            readings.append(reading(north=i * 30, east=0, device=f"dev_a{i}", minutes_ago=300 + i * 60))
        for i in range(3):
            readings.append(
                reading(north=5000 + i * 30, east=0, device=f"dev_b{i}", minutes_ago=300 + i * 60)
            )
        clusters = cluster_readings(readings, now=NOW)
        assert len(clusters) == 2

    def test_readings_outside_the_time_window_are_excluded(self):
        params = ClusteringParameters(time_window_days=30)
        readings = [
            reading(north=0, east=0, device="dev_a", minutes_ago=100),
            reading(north=20, east=20, device="dev_b", minutes_ago=200),
            reading(north=-20, east=10, device="dev_c", minutes_ago=60 * 24 * 45),
        ]
        assert cluster_readings(readings, params, now=NOW) == []

    def test_centroid_sits_among_the_members(self):
        readings = [
            reading(north=0, east=0, device="dev_a", minutes_ago=600),
            reading(north=100, east=0, device="dev_b", minutes_ago=400),
            reading(north=50, east=80, device="dev_c", minutes_ago=100),
        ]
        cluster = cluster_readings(readings, now=NOW)[0]
        for member in cluster.members:
            distance = haversine_metres(
                cluster.centroid_lat, cluster.centroid_lon, member.latitude, member.longitude
            )
            # radius_m is stored rounded to centimetres.
            assert distance <= cluster.radius_m + 0.01

    def test_eps_controls_cluster_separation(self):
        readings = [
            reading(north=0, east=0, device="dev_a", minutes_ago=600),
            reading(north=300, east=0, device="dev_b", minutes_ago=400),
            reading(north=600, east=0, device="dev_c", minutes_ago=100),
        ]
        tight = cluster_readings(readings, ClusteringParameters(eps_metres=100), now=NOW)
        loose = cluster_readings(readings, ClusteringParameters(eps_metres=400), now=NOW)
        assert tight == []
        assert len(loose) == 1


class TestCorroborationRule:
    """Non-negotiable rules 1 and 4, and the coordinated-reporting risk."""

    def test_one_device_cannot_publish_a_hotspot_alone(self):
        readings = [
            reading(north=i * 20, east=i * 15, device="dev_attacker", minutes_ago=600 - i * 60)
            for i in range(8)
        ]
        clusters = cluster_readings(readings, now=NOW)
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster.member_count == 8
        assert cluster.independent_device_count == 1
        assert cluster.is_publishable is False
        assert "awaiting_corroboration" in cluster.suppression_reason

    def test_two_colluding_devices_are_still_below_threshold(self):
        readings = []
        for i in range(6):
            readings.append(
                reading(
                    north=i * 20,
                    east=0,
                    device=f"dev_{i % 2}",
                    minutes_ago=600 - i * 60,
                )
            )
        cluster = cluster_readings(readings, now=NOW)[0]
        assert cluster.independent_device_count == 2
        assert cluster.is_publishable is False

    def test_threshold_is_configurable_and_enforced_exactly(self):
        params = ClusteringParameters(min_independent_devices=4)
        three = [
            reading(north=i * 20, east=0, device=f"dev_{i}", minutes_ago=600 - i * 90)
            for i in range(3)
        ]
        assert cluster_readings(three, params, now=NOW)[0].is_publishable is False

        four = three + [reading(north=70, east=10, device="dev_3", minutes_ago=200)]
        assert cluster_readings(four, params, now=NOW)[0].is_publishable is True

    def test_simultaneous_readings_are_suppressed(self):
        """Three devices at one spot within seconds is more likely one person
        with three handsets than a genuine community signal."""
        readings = [
            reading(north=i * 5, east=0, device=f"dev_{i}", minutes_ago=60.0 + i * 0.1)
            for i in range(4)
        ]
        cluster = cluster_readings(readings, now=NOW)[0]
        assert cluster.is_publishable is False
        assert "too_simultaneous" in cluster.suppression_reason

    def test_cluster_with_no_exceedance_is_not_published(self):
        readings = [
            reading(north=i * 20, east=0, device=f"dev_{i}", minutes_ago=600 - i * 120,
                    concentration=0.2, exceeds=False)
            for i in range(4)
        ]
        cluster = cluster_readings(readings, now=NOW)[0]
        assert cluster.is_publishable is False
        assert cluster.suppression_reason == "no_reading_exceeds_action_threshold"

    def test_suppressed_clusters_are_still_returned_for_the_officer_view(self):
        """Officers need to see sub-threshold patterns; the public map does not."""
        readings = [
            reading(north=i * 20, east=0, device="dev_solo", minutes_ago=600 - i * 60)
            for i in range(5)
        ]
        clusters = cluster_readings(readings, now=NOW)
        assert len(clusters) == 1
        assert clusters[0].is_publishable is False
        assert clusters[0].suppression_reason is not None


class TestSeverityAndPriority:
    def test_severity_rises_with_corroboration_and_exceedance(self):
        weak = cluster_readings(
            [
                reading(north=i * 20, east=0, device=f"dev_{i}", minutes_ago=600 - i * 100,
                        exceeds=(i == 0))
                for i in range(4)
            ],
            now=NOW,
        )[0]
        strong = cluster_readings(
            [
                reading(north=i * 20, east=0, device=f"dev_{i}", minutes_ago=600 - i * 60,
                        concentration=6.0, exceeds=True)
                for i in range(7)
            ],
            now=NOW,
        )[0]
        assert weak.severity == ClusterSeverity.ADVISORY
        assert strong.severity == ClusterSeverity.HIGH
        assert strong.inspection_priority > weak.inspection_priority

    def test_priority_is_bounded(self):
        readings = [
            reading(north=i * 10, east=0, device=f"dev_{i}", minutes_ago=30 + i, concentration=100.0)
            for i in range(20)
        ]
        cluster = cluster_readings(readings, now=NOW)[0]
        assert 0.0 <= cluster.inspection_priority <= 100.0

    def test_recent_clusters_outrank_stale_ones(self):
        def build(days_ago: float):
            return cluster_readings(
                [
                    reading(
                        north=i * 20,
                        east=0,
                        device=f"dev_{i}",
                        minutes_ago=days_ago * 1440 + i * 60,
                    )
                    for i in range(4)
                ],
                now=NOW,
            )[0]

        assert build(0.5).inspection_priority > build(20).inspection_priority

    def test_device_reading_counts_are_reported(self):
        readings = [
            reading(north=0, east=0, device="dev_a", minutes_ago=600),
            reading(north=20, east=0, device="dev_a", minutes_ago=500),
            reading(north=40, east=0, device="dev_b", minutes_ago=400),
            reading(north=60, east=0, device="dev_c", minutes_ago=300),
        ]
        cluster = cluster_readings(readings, now=NOW)[0]
        assert cluster.diagnostics["device_reading_counts"]["dev_a"] == 2
        assert cluster.independent_device_count == 3


class TestPrivacyBoundary:
    def test_reading_point_carries_no_identity_fields(self):
        """Rule 12: the clustering pipeline must not see personal data."""
        forbidden = {"user_id", "phone", "phone_number", "email", "name", "merchant_name"}
        assert not (set(ReadingPoint.__dataclass_fields__) & forbidden)

    def test_device_pseudonym_is_opaque(self):
        point = reading(device="dev_" + "a" * 32)
        assert point.device_pseudonym.startswith("dev_")
        assert "@" not in point.device_pseudonym


class TestPerceptualHashing:
    def _image(self, seed: int, size=(256, 256)) -> bytes:
        import io

        from PIL import Image

        rng = np.random.default_rng(seed)
        # Low-frequency structure, so the image has real perceptual content
        # rather than noise a pHash would treat as arbitrary.
        small = rng.integers(0, 255, (8, 8, 3), dtype=np.uint8)
        img = Image.fromarray(small).resize(size, Image.Resampling.BICUBIC)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()

    def _recompress(self, data: bytes, quality: int, resize=None) -> bytes:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if resize:
                img = img.resize(resize, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, format="JPEG", quality=quality)
            return buffer.getvalue()

    def test_hash_is_stable_and_correctly_sized(self):
        data = self._image(1)
        assert compute_phash(data) == compute_phash(data)
        assert len(compute_phash(data)) == 16

    def test_identical_image_hashes_identically(self):
        data = self._image(2)
        assert hamming_distance(compute_phash(data), compute_phash(data)) == 0

    def test_survives_recompression(self):
        """The attack is re-uploading a photo after it has been through a
        messaging app, which recompresses it."""
        original = self._image(3)
        recompressed = self._recompress(original, quality=45)
        assert hamming_distance(compute_phash(original), compute_phash(recompressed)) <= 8

    def test_survives_resizing(self):
        original = self._image(4)
        resized = self._recompress(original, quality=88, resize=(180, 180))
        assert hamming_distance(compute_phash(original), compute_phash(resized)) <= 10

    def test_different_images_hash_far_apart(self):
        distances = [
            hamming_distance(compute_phash(self._image(i)), compute_phash(self._image(i + 50)))
            for i in range(6)
        ]
        assert min(distances) > 8

    def test_hamming_requires_equal_length(self):
        with pytest.raises(ValueError):
            hamming_distance("abcd", "abcdef")


class TestEmbeddingSimilarity:
    def test_identical_vectors_are_maximally_similar(self):
        v = np.random.default_rng(1).normal(size=576)
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_scale_invariant(self):
        v = np.random.default_rng(2).normal(size=576)
        assert cosine_similarity(v, v * 7.5) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        a = np.zeros(4)
        a[0] = 1.0
        b = np.zeros(4)
        b[1] = 1.0
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_zero_vector_does_not_divide_by_zero(self):
        assert cosine_similarity(np.zeros(8), np.ones(8)) == 0.0
