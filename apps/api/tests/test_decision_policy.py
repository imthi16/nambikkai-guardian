"""Unit tests for the confidence-and-abstention decision policy.

Cover every outcome, the threshold boundary, missing signals (``None``
confidence / OCR), the conflicting-signal case (support and contradiction
coexisting), and determinism (identical signals always yield the identical
decision).
"""

from __future__ import annotations

from app.decision import ConfidencePolicy, DecisionOutcome, DecisionSignals
from app.decision.policy import DecisionPolicyConfig


def _signals(**overrides: object) -> DecisionSignals:
    base: dict[str, object] = {
        "supported_claims": 1,
        "partial_claims": 0,
        "contradicted_claims": 0,
        "unsupported_claims": 0,
        "evidence_count": 2,
        "retrieved_count": 4,
        "verifier_confidence": 0.8,
        "min_ocr_confidence": None,
    }
    base.update(overrides)
    return DecisionSignals(**base)  # type: ignore[arg-type]


def test_confident_support_answers() -> None:
    result = ConfidencePolicy().decide(_signals())
    assert result.outcome is DecisionOutcome.ANSWER
    assert result.outcome.is_answering


def test_moderate_confidence_answers_with_warning() -> None:
    result = ConfidencePolicy().decide(_signals(verifier_confidence=0.5))
    assert result.outcome is DecisionOutcome.ANSWER_WITH_WARNING
    assert "moderate" in result.reason


def test_dropped_claims_answer_with_warning() -> None:
    result = ConfidencePolicy().decide(_signals(partial_claims=1))
    assert result.outcome is DecisionOutcome.ANSWER_WITH_WARNING
    assert "dropped" in result.reason


def test_low_ocr_answers_with_warning() -> None:
    result = ConfidencePolicy().decide(_signals(min_ocr_confidence=0.3))
    assert result.outcome is DecisionOutcome.ANSWER_WITH_WARNING
    assert "OCR" in result.reason


def test_unknown_ocr_reliability_answers_with_warning() -> None:
    # OCR evidence with no recorded confidence must not pass as an unqualified
    # answer; it is flagged distinctly from born-digital (no OCR) evidence.
    result = ConfidencePolicy().decide(_signals(ocr_unknown_reliability=True))
    assert result.outcome is DecisionOutcome.ANSWER_WITH_WARNING
    assert "unknown reliability" in result.reason


def test_dropped_candidate_without_verdict_answers_with_warning() -> None:
    # A candidate dropped without a verdict (e.g. unknown chunk id) still counts
    # as a dropped claim, so a confident answer is warned rather than clean.
    result = ConfidencePolicy().decide(_signals(dropped_claims=1))
    assert result.outcome is DecisionOutcome.ANSWER_WITH_WARNING
    assert "dropped" in result.reason


def test_confidence_threshold_boundary_is_answer() -> None:
    # Exactly at the threshold answers without a warning (>= is the rule).
    result = ConfidencePolicy(DecisionPolicyConfig(answer_confidence=0.6)).decide(
        _signals(verifier_confidence=0.6)
    )
    assert result.outcome is DecisionOutcome.ANSWER
    # A hair below flips to a warning.
    below = ConfidencePolicy(DecisionPolicyConfig(answer_confidence=0.6)).decide(
        _signals(verifier_confidence=0.5999)
    )
    assert below.outcome is DecisionOutcome.ANSWER_WITH_WARNING


def test_support_with_contradiction_escalates() -> None:
    result = ConfidencePolicy().decide(_signals(contradicted_claims=1))
    assert result.outcome is DecisionOutcome.ESCALATE_FOR_REVIEW


def test_no_support_with_contradiction_escalates() -> None:
    result = ConfidencePolicy().decide(
        _signals(supported_claims=0, verifier_confidence=None, contradicted_claims=1)
    )
    assert result.outcome is DecisionOutcome.ESCALATE_FOR_REVIEW


def test_no_support_no_evidence_abstains() -> None:
    result = ConfidencePolicy().decide(
        _signals(supported_claims=0, verifier_confidence=None, evidence_count=0)
    )
    assert result.outcome is DecisionOutcome.ABSTAIN
    assert result.reason == "insufficient_evidence"


def test_no_support_with_evidence_asks_for_clarification() -> None:
    result = ConfidencePolicy().decide(
        _signals(supported_claims=0, verifier_confidence=None, evidence_count=3)
    )
    assert result.outcome is DecisionOutcome.ASK_FOR_CLARIFICATION


def test_missing_confidence_is_handled_without_error() -> None:
    # No supported claims → confidence is None; the policy must not treat it as
    # a number or crash.
    result = ConfidencePolicy().decide(
        _signals(supported_claims=0, verifier_confidence=None, evidence_count=1)
    )
    assert result.confidence == 0.0
    assert not result.outcome.is_answering


def test_decision_is_deterministic() -> None:
    signals = _signals(verifier_confidence=0.72, partial_claims=1)
    policy = ConfidencePolicy()
    first = policy.decide(signals)
    second = policy.decide(signals)
    assert first == second
