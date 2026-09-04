"""Ward resolution and public-location generalisation.

Two jobs, both required by the privacy rules:

1. **Ward lookup.** Map a coordinate to an administrative ward, because
   non-negotiable rule 4 says public surfaces show areas, not points.
2. **Generalisation.** Reduce a precise fix to something that cannot identify a
   stall, for anything that leaves the officer boundary.

Data source and its limits
--------------------------
A real deployment resolves wards against official boundary geometry (a
PostGIS polygon table loaded from the Local Self Government Department's ward
shapefiles). The hackathon build ships a small hand-built table of Palakkad and
Thrissur wards with representative points and approximate radii, and assigns a
coordinate to the nearest ward centre within range. That is enough to
demonstrate the boundary correctly, and it is honest about being an
approximation: `WardMatch.is_approximate` is True for every match, and the API
surfaces it.

Loading real polygons changes only this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Ward centres for the pilot districts named in the specification (Palakkad and
# Thrissur, Kerala). Coordinates are approximate town-level references chosen so
# the demo map renders in the right place; they are not survey data.
WARD_TABLE: list[dict] = [
    # --- Palakkad district ---
    {
        "ward_code": "PKD-01", "ward_name": "Palakkad Town North",
        "lat": 10.792, "lon": 76.654, "radius_km": 2.0,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-14", "ward_name": "Palakkad Municipal Market",
        "lat": 10.7757, "lon": 76.6548, "radius_km": 1.6,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-22", "ward_name": "Kalpathy",
        "lat": 10.7912, "lon": 76.6707, "radius_km": 1.8,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-31", "ward_name": "Chittur",
        "lat": 10.7, "lon": 76.746, "radius_km": 3.0,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-38", "ward_name": "Ottapalam",
        "lat": 10.77, "lon": 76.377, "radius_km": 3.0,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-45", "ward_name": "Mannarkkad",
        "lat": 10.992, "lon": 76.467, "radius_km": 3.0,
        "district": "Palakkad", "state": "Kerala",
    },
    {
        "ward_code": "PKD-52", "ward_name": "Alathur",
        "lat": 10.639, "lon": 76.551, "radius_km": 2.6,
        "district": "Palakkad", "state": "Kerala",
    },
    # --- Thrissur district ---
    {
        "ward_code": "TSR-02", "ward_name": "Thrissur Round",
        "lat": 10.5276, "lon": 76.2144, "radius_km": 1.8,
        "district": "Thrissur", "state": "Kerala",
    },
    {
        "ward_code": "TSR-09", "ward_name": "Shakthan Market",
        "lat": 10.515, "lon": 76.216, "radius_km": 1.5,
        "district": "Thrissur", "state": "Kerala",
    },
    {
        "ward_code": "TSR-17", "ward_name": "Ollur",
        "lat": 10.464, "lon": 76.244, "radius_km": 2.4,
        "district": "Thrissur", "state": "Kerala",
    },
    {
        "ward_code": "TSR-24", "ward_name": "Chalakudy",
        "lat": 10.307, "lon": 76.335, "radius_km": 3.0,
        "district": "Thrissur", "state": "Kerala",
    },
    {
        "ward_code": "TSR-31", "ward_name": "Kunnamkulam",
        "lat": 10.65, "lon": 76.07, "radius_km": 3.0,
        "district": "Thrissur", "state": "Kerala",
    },
    {
        "ward_code": "TSR-36", "ward_name": "Irinjalakuda",
        "lat": 10.342, "lon": 76.214, "radius_km": 2.8,
        "district": "Thrissur", "state": "Kerala",
    },
]

EARTH_RADIUS_KM = 6371.0088

# Public coordinates are snapped to this grid. ~0.01 degrees is roughly 1.1 km,
# coarse enough that a point cannot single out a stall in a market row.
PUBLIC_GRID_DEGREES = 0.01


@dataclass(frozen=True)
class WardMatch:
    ward_code: str
    ward_name: str
    district: str
    state: str
    centre_lat: float
    centre_lon: float
    distance_km: float
    is_approximate: bool = True


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def resolve_ward(latitude: float, longitude: float) -> WardMatch | None:
    """Nearest ward whose radius contains the point, if any.

    Returns None rather than guessing when the point is outside every ward. A
    scan from an unmapped area still records its coordinates for the officer
    view; it simply does not appear on the ward-level public map, which is the
    right failure mode -- publishing an area we cannot name would be worse than
    publishing nothing.
    """
    best: WardMatch | None = None
    for ward in WARD_TABLE:
        distance = _haversine_km(latitude, longitude, ward["lat"], ward["lon"])
        if distance <= ward["radius_km"] and (best is None or distance < best.distance_km):
            best = WardMatch(
                ward_code=ward["ward_code"],
                ward_name=ward["ward_name"],
                district=ward["district"],
                state=ward["state"],
                centre_lat=ward["lat"],
                centre_lon=ward["lon"],
                distance_km=round(distance, 4),
            )
    return best


def ward_by_code(ward_code: str) -> dict | None:
    return next((w for w in WARD_TABLE if w["ward_code"] == ward_code), None)


def generalise_for_public(latitude: float, longitude: float) -> tuple[float, float]:
    """Snap a coordinate to a coarse grid for any public surface.

    Used as a fallback when a point has no ward. Snapping (rather than adding
    random jitter) means repeated publication of the same location cannot be
    averaged back to the true point, which is the flaw in naive jittering.
    """
    return (
        round(latitude / PUBLIC_GRID_DEGREES) * PUBLIC_GRID_DEGREES,
        round(longitude / PUBLIC_GRID_DEGREES) * PUBLIC_GRID_DEGREES,
    )


def ward_representative_point(ward_code: str) -> tuple[float, float] | None:
    """The ward's own centre -- never a cluster centroid.

    This distinction matters: publishing a cluster centroid would point at where
    the readings actually were, which is what rule 4 forbids.
    """
    ward = ward_by_code(ward_code)
    return (ward["lat"], ward["lon"]) if ward else None
