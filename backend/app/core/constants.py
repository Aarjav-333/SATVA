"""Domain vocabulary.

Terminology is taken directly from SATVA_Complete_Project_Specification.md and
must not drift. Where the spec names a thing, that name is used verbatim.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Spec: consumer, farmer/FPO, retailer, Food Safety Officer, administrator."""

    CONSUMER = "consumer"
    FARMER = "farmer"
    RETAILER = "retailer"
    OFFICER = "officer"
    ADMIN = "admin"


class ScanStage(StrEnum):
    """Which layer of the dual-signal pipeline produced a record.

    Spec 3.1 Evidence Rule: only LAYER_B_COLORIMETRY output may be published,
    escalated or attached to a complaint.
    """

    LAYER_A_VISION = "layer_a_vision"
    LAYER_B_COLORIMETRY = "layer_b_colorimetry"


class ScreeningVerdict(StrEnum):
    """Layer A output band. Deliberately advisory language — never 'guilty'."""

    NOT_SUSPICIOUS = "not_suspicious"
    INCONCLUSIVE = "inconclusive"
    SUSPICIOUS = "suspicious"
    REFUSED_UNSUPPORTED_CROP = "refused_unsupported_crop"
    REFUSED_CAPTURE_QUALITY = "refused_capture_quality"


class EvidenceGrade(StrEnum):
    """What a record is permitted to be used for.

    SCREENING_ONLY records can never leave the device owner's own history.
    CONFIRMATORY records are the only ones eligible for Watch and complaints.
    """

    SCREENING_ONLY = "screening_only"
    CONFIRMATORY = "confirmatory"
    REJECTED = "rejected"


class ColorimetryRejectReason(StrEnum):
    """Spec 7: prefer refusal to report over a low-confidence chemical result."""

    REFERENCE_CARD_NOT_FOUND = "reference_card_not_found"
    REFERENCE_CARD_INCOMPLETE = "reference_card_incomplete"
    EXPOSURE_OUT_OF_RANGE = "exposure_out_of_range"
    CLIPPED_HIGHLIGHTS = "clipped_highlights"
    CLIPPED_SHADOWS = "clipped_shadows"
    ILLUMINANT_TOO_TINTED = "illuminant_too_tinted"
    NON_UNIFORM_ILLUMINATION = "non_uniform_illumination"
    CORRECTION_RESIDUAL_TOO_HIGH = "correction_residual_too_high"
    STRIP_REGION_NOT_FOUND = "strip_region_not_found"
    STRIP_REGION_NOT_UNIFORM = "strip_region_not_uniform"
    OUT_OF_CALIBRATION_RANGE = "out_of_calibration_range"
    UNKNOWN_REAGENT = "unknown_reagent"


class ReagentAssay(StrEnum):
    """Reagents named in spec 3.1 Layer B. Each has its own calibration series."""

    TURMERIC_CARBIDE = "turmeric_carbide"
    STARCH_IODINE_MILK = "starch_iodine_milk"
    FORMALIN_FISH = "formalin_fish"
    METANIL_YELLOW_TURMERIC = "metanil_yellow_turmeric"
    ARGEMONE_MUSTARD_OIL = "argemone_mustard_oil"


class CustodyEventType(StrEnum):
    """Spec 3.2 custody stages."""

    LOT_REGISTERED = "lot_registered"
    AGGREGATOR_SCAN_IN = "aggregator_scan_in"
    AGGREGATOR_SCAN_OUT = "aggregator_scan_out"
    TRANSPORT_DEPART = "transport_depart"
    TRANSPORT_ARRIVE = "transport_arrive"
    TEMPERATURE_READING = "temperature_reading"
    RETAILER_RECEIVED = "retailer_received"
    LOT_SPLIT = "lot_split"
    LOT_WITHDRAWN = "lot_withdrawn"


class RipeningMethod(StrEnum):
    """Declared by the farmer at lot registration (spec 3.2)."""

    NATURAL = "natural"
    ETHYLENE_PERMITTED = "ethylene_permitted"
    UNDECLARED = "undeclared"


class ComplaintStatus(StrEnum):
    """SATVA prepares packages; it never claims to have filed with FSSAI."""

    DRAFT = "draft"
    PACKAGE_READY = "package_ready"
    SUBMITTED_BY_USER = "submitted_by_user"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"
    WITHDRAWN = "withdrawn"


class ClusterSeverity(StrEnum):
    ADVISORY = "advisory"
    ELEVATED = "elevated"
    HIGH = "high"


class ShelfAction(StrEnum):
    SELL_NORMALLY = "sell_normally"
    PRIORITISE = "prioritise"
    MARKDOWN = "markdown"
    DONATE = "donate"
    WITHDRAW = "withdraw"


# --- Model scope discipline (spec 6.4) --------------------------------------
# The vision model refuses crops outside its validated set rather than guessing.
SUPPORTED_CROPS: dict[str, dict] = {
    "mango": {
        "label": "Mango",
        "cultivars": ["alphonso", "banganapalli", "neelam", "totapuri", "unspecified"],
        "assays": [ReagentAssay.TURMERIC_CARBIDE],
        "calibrated": True,
    },
    "banana": {
        "label": "Banana",
        "cultivars": ["nendran", "robusta", "poovan", "unspecified"],
        "assays": [ReagentAssay.TURMERIC_CARBIDE],
        "calibrated": True,
    },
    "papaya": {
        "label": "Papaya",
        "cultivars": ["red_lady", "unspecified"],
        "assays": [ReagentAssay.TURMERIC_CARBIDE],
        "calibrated": True,
    },
    "tomato": {
        "label": "Tomato",
        "cultivars": ["unspecified"],
        "assays": [ReagentAssay.TURMERIC_CARBIDE],
        "calibrated": True,
    },
}

# Crops present in the product vocabulary but NOT yet validated. The model must
# decline to score these (spec rule 15) until controlled-set validation exists.
UNVALIDATED_CROPS: set[str] = {"sapota", "guava", "custard_apple", "pineapple", "grape"}

DISCLAIMER_SHORT = (
    "SATVA is a screening aid, not a statutory test or certification."
)
DISCLAIMER_LONG = (
    "SATVA is a screening aid. It is not a statutory test, a certification, or a "
    "legal determination of food safety. A visual screening result is advisory "
    "only and must never be treated as proof of adulteration. Only a confirmatory "
    "colorimetric strip reading may be escalated, and escalation is routed to the "
    "statutory FSSAI channel rather than replacing it."
)
