"""SATVA Shelf: remaining shelf-life prediction.

Model, stated plainly
---------------------
Two inputs combine into a remaining-shelf-life estimate:

1. **Ripeness index** from the regression head that shares the MobileNetV3-Small
   backbone with the adulteration head (spec 3.5). One photograph, two outputs,
   no extra hardware.
2. **Thermal history** from a BLE crate tag, as accumulated degree-hours above a
   crop-specific reference temperature.

The temperature term is a **published-form degree-day model**, not something
learned from SATVA's own data:

    remaining_days = (1 - ripeness) * shelf_life_at_reference * Q10 ** ((T_ref - T) / 10)

The Q10 coefficient expresses how much faster produce respires per 10 degrees C.
The values below come from standard post-harvest handling ranges and are marked
as engineering estimates. What SATVA has *not* done is fit these against
measured spoilage outcomes -- the quarter-long retail measurement in spec 13.2
is what would do that. Until then this module produces a defensible, explainable
ordering of which crates to sell first, and the API labels it as an estimate.

The output is deliberately explainable: `rationale` carries every term that went
into the number, so a shop manager can see *why* a crate was flagged rather than
being told to discount stock by an oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.constants import ShelfAction

MODEL_ID = "satva-shelf-degreehour-v1"


@dataclass(frozen=True)
class CropShelfProfile:
    """Post-harvest behaviour for one crop.

    `shelf_life_days_at_reference` is the usable life of freshly harvested,
    unripe produce held at `reference_temperature_c`.
    """

    crop: str
    shelf_life_days_at_reference: float
    reference_temperature_c: float
    q10: float
    ripeness_at_peak_sale: float
    is_climacteric: bool
    note: str = ""


# Engineering estimates from standard post-harvest handling guidance. Not fitted
# to SATVA data; see the module docstring.
CROP_PROFILES: dict[str, CropShelfProfile] = {
    "mango": CropShelfProfile(
        "mango", 12.0, 13.0, 2.4, 0.80, True,
        "Chilling injury below about 12 C, so cold storage does not extend life "
        "indefinitely.",
    ),
    "banana": CropShelfProfile("banana", 9.0, 14.0, 2.6, 0.75, True,
                               "Highly ethylene sensitive; ripens rapidly once triggered."),
    "papaya": CropShelfProfile("papaya", 8.0, 12.0, 2.5, 0.78, True, ""),
    "tomato": CropShelfProfile("tomato", 10.0, 13.0, 2.2, 0.82, True, ""),
}

DEFAULT_PROFILE = CropShelfProfile(
    "generic", 8.0, 15.0, 2.3, 0.80, False,
    "No crop-specific profile; a conservative generic curve is used.",
)

# Ambient assumption when no tag is fitted. Kerala market ambient runs high, and
# assuming a cold chain that is not there would overstate remaining life -- the
# error that causes waste rather than preventing it.
ASSUMED_AMBIENT_C = 29.0


@dataclass
class TemperatureSummary:
    mean_temperature_c: float
    accumulated_degree_hours: float
    hours_observed: float
    source: str
    is_assumed: bool = False


@dataclass
class ShelfPredictionResult:
    ripeness_index: float
    freshness_score: float
    remaining_shelf_life_days: float
    confidence_low_days: float
    confidence_high_days: float
    recommended_action: str
    suggested_markdown_pct: float | None
    mean_temperature_c: float
    accumulated_degree_hours: float
    temperature_source: str
    model_id: str = MODEL_ID
    rationale: dict = field(default_factory=dict)


def summarise_temperature(
    readings: list[tuple[datetime, float]],
    profile: CropShelfProfile,
    *,
    received_at: datetime,
    now: datetime | None = None,
) -> TemperatureSummary:
    """Accumulate degree-hours above the crop's reference temperature.

    With no readings we assume market ambient rather than assuming the reference
    temperature. Assuming the reference would silently claim a cold chain that
    does not exist and inflate every shelf-life estimate.
    """
    reference_time = now or datetime.now(UTC)
    elapsed_hours = max(
        0.0, (reference_time - received_at).total_seconds() / 3600.0
    )

    if not readings:
        excess = max(0.0, ASSUMED_AMBIENT_C - profile.reference_temperature_c)
        return TemperatureSummary(
            mean_temperature_c=ASSUMED_AMBIENT_C,
            accumulated_degree_hours=excess * elapsed_hours,
            hours_observed=elapsed_hours,
            source="assumed_ambient",
            is_assumed=True,
        )

    ordered = sorted(readings, key=lambda r: r[0])
    degree_hours = 0.0
    total_hours = 0.0
    weighted_sum = 0.0

    for index, (timestamp, temperature) in enumerate(ordered):
        if index + 1 < len(ordered):
            span_hours = (ordered[index + 1][0] - timestamp).total_seconds() / 3600.0
        else:
            span_hours = max(0.0, (reference_time - timestamp).total_seconds() / 3600.0)
        span_hours = max(0.0, min(span_hours, 72.0))  # guard against stale gaps
        degree_hours += max(0.0, temperature - profile.reference_temperature_c) * span_hours
        total_hours += span_hours
        weighted_sum += temperature * span_hours

    mean = weighted_sum / total_hours if total_hours > 0 else ordered[-1][1]
    return TemperatureSummary(
        mean_temperature_c=round(mean, 2),
        accumulated_degree_hours=round(degree_hours, 2),
        hours_observed=round(total_hours, 2),
        source="ble_tag",
    )


def predict(
    *,
    crop: str,
    ripeness_index: float | None,
    received_at: datetime,
    temperature_readings: list[tuple[datetime, float]] | None = None,
    now: datetime | None = None,
) -> ShelfPredictionResult:
    """Estimate remaining shelf life and recommend an action."""
    reference_time = now or datetime.now(UTC)
    profile = CROP_PROFILES.get(crop.lower(), DEFAULT_PROFILE)

    # With no vision estimate, infer ripeness from elapsed time alone. Less
    # accurate, and the wider interval below says so.
    days_held = max(0.0, (reference_time - received_at).total_seconds() / 86400.0)
    inferred = ripeness_index is None
    if inferred:
        ripeness = min(0.98, days_held / max(profile.shelf_life_days_at_reference, 1e-6))
    else:
        ripeness = float(min(max(ripeness_index, 0.0), 1.0))

    temperature = summarise_temperature(
        temperature_readings or [], profile, received_at=received_at, now=reference_time
    )

    # Arrhenius-style rate multiplier: warmer produce ages faster.
    rate_multiplier = profile.q10 ** (
        (temperature.mean_temperature_c - profile.reference_temperature_c) / 10.0
    )
    rate_multiplier = max(0.25, min(rate_multiplier, 12.0))

    headroom = max(0.0, profile.ripeness_at_peak_sale - ripeness) / max(
        profile.ripeness_at_peak_sale, 1e-6
    )
    remaining = (headroom * profile.shelf_life_days_at_reference) / rate_multiplier
    remaining = max(0.0, round(remaining, 2))

    # Interval widens when ripeness was inferred rather than measured, and when
    # the thermal history was assumed rather than logged.
    relative_uncertainty = 0.25
    if inferred:
        relative_uncertainty += 0.30
    if temperature.is_assumed:
        relative_uncertainty += 0.20

    low = max(0.0, round(remaining * (1 - relative_uncertainty), 2))
    high = round(remaining * (1 + relative_uncertainty), 2)

    action, markdown = _recommend(remaining, ripeness, profile)

    return ShelfPredictionResult(
        ripeness_index=round(ripeness, 4),
        freshness_score=round(max(0.0, min(1.0, 1.0 - ripeness)) * 100, 1),
        remaining_shelf_life_days=remaining,
        confidence_low_days=low,
        confidence_high_days=high,
        recommended_action=action,
        suggested_markdown_pct=markdown,
        mean_temperature_c=temperature.mean_temperature_c,
        accumulated_degree_hours=temperature.accumulated_degree_hours,
        temperature_source=temperature.source,
        rationale={
            "crop_profile": profile.crop,
            "shelf_life_days_at_reference": profile.shelf_life_days_at_reference,
            "reference_temperature_c": profile.reference_temperature_c,
            "q10": profile.q10,
            "rate_multiplier": round(rate_multiplier, 3),
            "ripeness_source": "inferred_from_age" if inferred else "vision_regression_head",
            "temperature_source": temperature.source,
            "hours_observed": temperature.hours_observed,
            "days_held": round(days_held, 2),
            "relative_uncertainty": round(relative_uncertainty, 3),
            "model_note": (
                "Degree-hour model using published post-harvest coefficients. Not yet "
                "validated against measured spoilage outcomes."
            ),
            "crop_note": profile.note,
        },
    )


def _recommend(
    remaining_days: float, ripeness: float, profile: CropShelfProfile
) -> tuple[str, float | None]:
    """Turn a shelf-life estimate into an action.

    The donation branch is placed before the withdraw branch on purpose: produce
    that is past sale quality but still edible should be routed to a partner
    rather than discarded, which is the waste-reduction outcome the spec asks
    for.
    """
    if ripeness >= 0.97 or remaining_days <= 0.0:
        return ShelfAction.WITHDRAW, None
    if remaining_days < 0.75:
        return ShelfAction.DONATE, None
    if remaining_days < 1.5:
        return ShelfAction.MARKDOWN, 40.0
    if remaining_days < 2.5:
        return ShelfAction.MARKDOWN, 25.0
    if remaining_days < 4.0:
        return ShelfAction.PRIORITISE, None
    return ShelfAction.SELL_NORMALLY, None
