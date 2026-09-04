"""Tests for the evidence rule — SATVA's central product constraint.

    "A photograph should never convict a vendor."

These tests exist because that sentence is easy to agree with and easy to
violate by accident. They assert the machinery that makes it structural rather
than aspirational.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.constants import (
    SUPPORTED_CROPS,
    UNVALIDATED_CROPS,
    EvidenceGrade,
    ScreeningVerdict,
)
from app.core.errors import EvidenceRuleViolation, UnsupportedCropError
from app.services.evidence import (
    ACTIONS_REQUIRING_CONFIRMATION,
    EVIDENCE_CAPABILITIES,
    SCREENING_BANDS,
    assert_crop_supported,
    can,
    classify_vision_score,
    eligible_for_watch,
    is_cultivar_supported,
    promote_to_confirmatory,
    require_evidence_grade,
    retention_deadline,
)


def make_scan(
    grade: str = EvidenceGrade.SCREENING_ONLY,
    *,
    shared: bool = True,
    location: object | None = "point",
    duplicate_of: uuid.UUID | None = None,
) -> SimpleNamespace:
    """A minimal stand-in for a Scan row.

    The gate only reads a handful of fields, so a namespace keeps these tests
    free of a database and lets them run in milliseconds.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        evidence_grade=grade,
        shared_with_watch=shared,
        location=location,
        duplicate_of_scan_id=duplicate_of,
    )


def make_reading(accepted: bool = True, *, scan_id=None, value: float | None = 4.2):
    return SimpleNamespace(
        id=uuid.uuid4(),
        scan_id=scan_id,
        accepted=accepted,
        concentration_value=value,
        reject_reason=None,
        assay="calcium_carbide",
        exceeds_action_threshold=False,
    )


class FakeSession:
    """Enough Session for the gate: it only asks whether a strip image exists.

    `scalar` stands in for that single lookup, so these tests keep the
    millisecond runtime the rest of the file relies on.
    """

    def __init__(self, *, strip_image: bool):
        self._strip_image = strip_image
        self.flushed = 0

    def scalar(self, _statement):
        return uuid.uuid4() if self._strip_image else None

    def flush(self):
        self.flushed += 1


class TestTheCapabilityTable:
    """The table itself is the policy; these tests pin it down."""

    def test_screening_only_can_do_nothing_but_be_viewed(self):
        assert EVIDENCE_CAPABILITIES[EvidenceGrade.SCREENING_ONLY] == {"view_own"}

    def test_rejected_can_do_nothing_but_be_viewed(self):
        assert EVIDENCE_CAPABILITIES[EvidenceGrade.REJECTED] == {"view_own"}

    def test_only_confirmatory_unlocks_consequences(self):
        confirmatory = EVIDENCE_CAPABILITIES[EvidenceGrade.CONFIRMATORY]
        for action in (
            "share_with_watch",
            "participate_in_clustering",
            "attach_to_complaint",
            "appear_in_officer_worklist",
        ):
            assert action in confirmatory
            assert action in ACTIONS_REQUIRING_CONFIRMATION

    def test_no_grade_other_than_confirmatory_can_escalate(self):
        for grade, actions in EVIDENCE_CAPABILITIES.items():
            if grade == EvidenceGrade.CONFIRMATORY:
                continue
            assert not (actions & ACTIONS_REQUIRING_CONFIRMATION), (
                f"grade {grade} must not permit any escalating action"
            )


class TestTheGate:
    @pytest.mark.parametrize("action", sorted(ACTIONS_REQUIRING_CONFIRMATION))
    def test_screening_only_scan_is_refused_every_escalation(self, action):
        with pytest.raises(EvidenceRuleViolation) as exc:
            require_evidence_grade(make_scan(EvidenceGrade.SCREENING_ONLY), action)
        assert exc.value.details["required_grade"] == EvidenceGrade.CONFIRMATORY.value
        # The message must explain the two-layer rule, not just say "denied".
        assert "photograph" in exc.value.message.lower()

    @pytest.mark.parametrize("action", sorted(ACTIONS_REQUIRING_CONFIRMATION))
    def test_rejected_scan_is_refused_every_escalation(self, action):
        with pytest.raises(EvidenceRuleViolation):
            require_evidence_grade(make_scan(EvidenceGrade.REJECTED), action)

    @pytest.mark.parametrize("action", sorted(ACTIONS_REQUIRING_CONFIRMATION))
    def test_confirmatory_scan_is_permitted(self, action):
        require_evidence_grade(make_scan(EvidenceGrade.CONFIRMATORY), action)

    def test_viewing_your_own_scan_is_always_allowed(self):
        for grade in EvidenceGrade:
            require_evidence_grade(make_scan(grade), "view_own")

    def test_an_unknown_action_is_refused_rather_than_allowed(self):
        """Fail closed. A typo in a call site must not grant permission."""
        with pytest.raises(EvidenceRuleViolation):
            require_evidence_grade(make_scan(EvidenceGrade.CONFIRMATORY), "delete_everything")

    def test_can_agrees_with_the_gate(self):
        for grade in EvidenceGrade:
            for action in EVIDENCE_CAPABILITIES[EvidenceGrade.CONFIRMATORY]:
                scan = make_scan(grade)
                permitted = can(scan, action)
                if permitted:
                    require_evidence_grade(scan, action)
                else:
                    with pytest.raises(EvidenceRuleViolation):
                        require_evidence_grade(scan, action)


class TestScreeningBands:
    def test_high_scores_are_suspicious(self):
        assert classify_vision_score(60.0) == ScreeningVerdict.SUSPICIOUS
        assert classify_vision_score(99.9) == ScreeningVerdict.SUSPICIOUS

    def test_low_scores_are_not_suspicious(self):
        assert classify_vision_score(0.0) == ScreeningVerdict.NOT_SUSPICIOUS
        assert classify_vision_score(34.9) == ScreeningVerdict.NOT_SUSPICIOUS

    def test_the_middle_is_inconclusive_not_folded_into_a_neighbour(self):
        """A model that is unsure should say so.

        Folding uncertainty into 'not suspicious' hides risk; folding it into
        'suspicious' sends people to buy strips they do not need.
        """
        assert classify_vision_score(35.0) == ScreeningVerdict.INCONCLUSIVE
        assert classify_vision_score(59.9) == ScreeningVerdict.INCONCLUSIVE

    def test_bands_are_contiguous_and_ordered(self):
        assert 0 < SCREENING_BANDS.not_suspicious_below < SCREENING_BANDS.suspicious_at_or_above < 100

    def test_no_score_maps_to_a_verdict_that_implies_guilt(self):
        """The vocabulary itself must stay advisory."""
        for score in range(0, 101, 5):
            verdict = classify_vision_score(float(score))
            assert verdict.value in {"not_suspicious", "inconclusive", "suspicious"}


class TestCropRefusal:
    """Rule 15: refuse unsupported crops rather than guess."""

    @pytest.mark.parametrize("crop", sorted(SUPPORTED_CROPS))
    def test_validated_crops_are_accepted(self, crop):
        assert_crop_supported(crop)

    @pytest.mark.parametrize("crop", sorted(UNVALIDATED_CROPS))
    def test_known_but_unvalidated_crops_are_refused_with_a_reason(self, crop):
        with pytest.raises(UnsupportedCropError) as exc:
            assert_crop_supported(crop)
        assert exc.value.details["reason"] == "awaiting_controlled_validation"
        # The user must be told what IS available, not just what is not.
        assert exc.value.details["supported_crops"]

    def test_an_unknown_crop_is_refused(self):
        with pytest.raises(UnsupportedCropError) as exc:
            assert_crop_supported("dragonfruit")
        assert exc.value.details["reason"] == "unknown_crop"

    def test_refusal_is_case_and_whitespace_insensitive(self):
        assert_crop_supported("  MANGO  ")

    def test_supported_and_unvalidated_sets_do_not_overlap(self):
        assert not (set(SUPPORTED_CROPS) & UNVALIDATED_CROPS)

    def test_cultivar_check(self):
        assert is_cultivar_supported("mango", "banganapalli")
        assert is_cultivar_supported("mango", None)
        assert not is_cultivar_supported("mango", "not_a_cultivar")
        assert not is_cultivar_supported("dragonfruit", "any")


class TestWatchEligibility:
    """Four conditions, all required."""

    def test_a_confirmed_shared_located_scan_is_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.CONFIRMATORY), make_reading(accepted=True)
        )
        assert eligible is True
        assert reason is None

    def test_screening_only_is_not_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.SCREENING_ONLY), make_reading(True)
        )
        assert eligible is False
        assert reason == "not_confirmatory"

    def test_unshared_is_not_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.CONFIRMATORY, shared=False), make_reading(True)
        )
        assert eligible is False
        assert reason == "not_shared_by_user"

    def test_a_scan_without_a_location_is_not_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.CONFIRMATORY, location=None), make_reading(True)
        )
        assert eligible is False
        assert reason == "no_location"

    def test_a_known_duplicate_is_not_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.CONFIRMATORY, duplicate_of=uuid.uuid4()),
            make_reading(True),
        )
        assert eligible is False
        assert reason == "duplicate_submission"

    def test_a_refused_reading_does_not_make_a_scan_eligible(self):
        eligible, reason = eligible_for_watch(
            make_scan(EvidenceGrade.CONFIRMATORY), make_reading(accepted=False)
        )
        assert eligible is False
        assert reason == "no_accepted_reading"

    def test_no_reading_at_all_is_not_eligible(self):
        eligible, reason = eligible_for_watch(make_scan(EvidenceGrade.CONFIRMATORY), None)
        assert eligible is False
        assert reason == "no_accepted_reading"


class TestRetention:
    """Spec 15.3: images retained only as long as a complaint is live."""

    def test_default_retention_is_short(self):
        now = datetime(2026, 9, 4, tzinfo=UTC)
        deadline = retention_deadline(now, has_live_complaint=False)
        assert deadline > now
        assert deadline - now <= timedelta(days=180)

    def test_a_live_complaint_extends_retention(self):
        now = datetime(2026, 9, 4, tzinfo=UTC)
        short = retention_deadline(now, has_live_complaint=False)
        long = retention_deadline(now, has_live_complaint=True)
        assert long > short


class TestPromotionRequiresTheStripPhotograph:
    """The handset computes the number; the photograph is what keeps it honest.

    The server cannot recompute a reading it has no image for, so a scan
    promoted without one would rest entirely on the client's word -- and a
    fabricated reading would be indistinguishable from a measured one.
    """

    def test_an_accepted_reading_alone_does_not_promote(self):
        scan = make_scan(EvidenceGrade.SCREENING_ONLY)
        reading = make_reading(accepted=True, scan_id=scan.id)
        promote_to_confirmatory(FakeSession(strip_image=False), scan, reading)
        assert scan.evidence_grade == EvidenceGrade.SCREENING_ONLY

    def test_the_stored_photograph_completes_the_promotion(self):
        scan = make_scan(EvidenceGrade.SCREENING_ONLY)
        reading = make_reading(accepted=True, scan_id=scan.id)
        promote_to_confirmatory(FakeSession(strip_image=True), scan, reading)
        assert scan.evidence_grade == EvidenceGrade.CONFIRMATORY

    def test_a_refused_reading_is_rejected_photograph_or_not(self):
        scan = make_scan(EvidenceGrade.SCREENING_ONLY)
        reading = make_reading(accepted=False, scan_id=scan.id)
        promote_to_confirmatory(FakeSession(strip_image=True), scan, reading)
        assert scan.evidence_grade == EvidenceGrade.REJECTED

    def test_a_reading_from_another_scan_is_refused(self):
        scan = make_scan(EvidenceGrade.SCREENING_ONLY)
        reading = make_reading(accepted=True, scan_id=uuid.uuid4())
        with pytest.raises(EvidenceRuleViolation):
            promote_to_confirmatory(FakeSession(strip_image=True), scan, reading)

    def test_an_accepted_reading_with_no_value_is_refused(self):
        scan = make_scan(EvidenceGrade.SCREENING_ONLY)
        reading = make_reading(accepted=True, scan_id=scan.id, value=None)
        with pytest.raises(EvidenceRuleViolation):
            promote_to_confirmatory(FakeSession(strip_image=True), scan, reading)
