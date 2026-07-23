"""The deterministic confidence-and-abstention decision policy.

Given the aggregated signals of one query, the policy chooses exactly one of
five operational outcomes. The rules are ordered and total, so the same signals
always yield the same decision and every missing or conflicting signal has a
defined result:

1. **No supported claim.** If any claim was contradicted, the evidence actively
   refutes the query and a human should look — ``ESCALATE_FOR_REVIEW``. With no
   evidence at all, ``ABSTAIN``. With evidence but nothing verifiable,
   ``ASK_FOR_CLARIFICATION`` (the user can narrow or rephrase).
2. **Supported claim(s) present, but a contradiction also present.** Support and
   refutation coexist, which no threshold can safely reconcile —
   ``ESCALATE_FOR_REVIEW``.
3. **Supported, no contradiction.** ``ANSWER`` when confidence clears the bar and
   nothing is flagged; otherwise ``ANSWER_WITH_WARNING`` (moderate confidence,
   dropped claims, or low-reliability OCR).

Thresholds live in :class:`DecisionPolicyConfig` so they are tunable and testable
at their boundaries, and confidence is never a model's self-report.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.decision.types import DecisionOutcome, DecisionResult, DecisionSignals


@dataclass(frozen=True)
class DecisionPolicyConfig:
    """Tunable thresholds for the decision policy.

    ``answer_confidence`` is the floor a supported answer must clear to be
    surfaced without a warning; at or above it (and with no other flag) the
    decision is ``ANSWER``. ``low_ocr_confidence`` flags cited OCR evidence whose
    recorded confidence is below it.
    """

    answer_confidence: float = 0.6
    low_ocr_confidence: float = 0.5


class ConfidencePolicy:
    """Maps aggregated signals to one calibrated operational decision."""

    def __init__(self, config: DecisionPolicyConfig | None = None) -> None:
        self._config = config or DecisionPolicyConfig()

    def decide(self, signals: DecisionSignals) -> DecisionResult:
        if signals.supported_claims == 0:
            return self._decide_without_support(signals)
        return self._decide_with_support(signals)

    def _decide_without_support(self, signals: DecisionSignals) -> DecisionResult:
        if signals.contradicted_claims > 0:
            return DecisionResult(
                DecisionOutcome.ESCALATE_FOR_REVIEW,
                0.0,
                "evidence contradicts the query and no claim could be supported",
            )
        if signals.evidence_count == 0:
            return DecisionResult(DecisionOutcome.ABSTAIN, 0.0, "insufficient_evidence")
        return DecisionResult(
            DecisionOutcome.ASK_FOR_CLARIFICATION,
            0.0,
            "evidence was retrieved but no claim could be verified",
        )

    def _decide_with_support(self, signals: DecisionSignals) -> DecisionResult:
        confidence = signals.verifier_confidence or 0.0
        if signals.contradicted_claims > 0:
            return DecisionResult(
                DecisionOutcome.ESCALATE_FOR_REVIEW,
                confidence,
                "supported and contradicted claims coexist in the evidence",
            )
        warnings = self._warnings(signals, confidence)
        if warnings:
            return DecisionResult(
                DecisionOutcome.ANSWER_WITH_WARNING, confidence, "; ".join(warnings)
            )
        return DecisionResult(
            DecisionOutcome.ANSWER, confidence, "well-supported by cited evidence"
        )

    def _warnings(self, signals: DecisionSignals, confidence: float) -> list[str]:
        warnings: list[str] = []
        if confidence < self._config.answer_confidence:
            warnings.append("supporting confidence is moderate")
        if (
            signals.partial_claims > 0
            or signals.unsupported_claims > 0
            or signals.dropped_claims > 0
        ):
            warnings.append("some proposed claims were dropped as unverifiable")
        if (
            signals.min_ocr_confidence is not None
            and signals.min_ocr_confidence < self._config.low_ocr_confidence
        ):
            warnings.append("cited evidence has low OCR reliability")
        if signals.ocr_unknown_reliability:
            warnings.append("cited evidence has OCR of unknown reliability")
        return warnings


def get_default_policy() -> ConfidencePolicy:
    """The decision policy wired for the MVP."""
    return ConfidencePolicy()
