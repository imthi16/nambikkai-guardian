"""Evaluation fixtures for atomic-claim entailment.

This is the measurable AI-evaluation gate for claim verification. Each fixture
is a labeled (claim, evidence, expected verdict) triple grouped by the facet it
exercises — general claim support, contradiction detection, numerical
reasoning, and negation — and the analyzer's accuracy on each facet must meet a
documented threshold. The thresholds are intentionally below 100% so a single
hard example does not make the suite brittle, while still failing loudly on any
real regression in the deterministic analyzer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.verification.entailment import LexicalEntailmentAnalyzer
from app.verification.types import EntailmentVerdict

# Per-facet minimum accuracy the analyzer must sustain over its fixtures.
FACET_THRESHOLDS = {
    "claim_support": 0.85,
    "contradiction": 0.85,
    "numerical": 0.85,
    "negation": 0.85,
}


@dataclass(frozen=True)
class Fixture:
    facet: str
    claim: str
    evidence: str
    expected: EntailmentVerdict


V = EntailmentVerdict

_FIXTURES: list[Fixture] = [
    # --- general claim support -------------------------------------------
    Fixture(
        "claim_support",
        "The invoice payment is due within thirty days of receipt.",
        "The invoice payment is due within thirty days of receipt.",
        V.SUPPORTED,
    ),
    Fixture(
        "claim_support",
        "The invoice payment is due.",
        "The invoice payment is due within thirty days of receipt.",
        V.SUPPORTED,
    ),
    Fixture(
        "claim_support",
        "Refunds are processed within five business days.",
        "Refunds are processed within five business days of approval.",
        V.SUPPORTED,
    ),
    Fixture(
        "claim_support",
        "The cafeteria menu changes every week.",
        "The invoice payment is due within thirty days of receipt.",
        V.UNSUPPORTED,
    ),
    Fixture(
        "claim_support",
        "Either party may terminate the contract.",
        "Either party may terminate with sixty days written notice.",
        V.PARTIAL,
    ),
    # --- contradiction ----------------------------------------------------
    Fixture(
        "contradiction",
        "The invoice payment is due within sixty days of receipt.",
        "The invoice payment is due within thirty days of receipt.",
        V.CONTRADICTED,
    ),
    Fixture(
        "contradiction",
        "The term ends on 2024-06-30.",
        "The term ends on 2024-03-31 per the agreement.",
        V.CONTRADICTED,
    ),
    Fixture(
        "contradiction",
        "Refunds are processed within ten business days.",
        "Refunds are processed within five business days of approval.",
        V.CONTRADICTED,
    ),
    Fixture(
        "contradiction",
        "Late fees accrue after ninety days of arrears.",
        "The invoice payment is due within thirty days of receipt.",
        V.UNSUPPORTED,  # shared unit only; not a real contradiction
    ),
    # --- numerical --------------------------------------------------------
    Fixture(
        "numerical",
        "The invoice payment is due within 30 days of receipt.",
        "The invoice payment is due within thirty days of receipt.",
        V.SUPPORTED,
    ),
    Fixture(
        "numerical",
        "Notice of ninety days is required.",
        "Notice of 90 days is required.",
        V.SUPPORTED,
    ),
    Fixture(
        "numerical",
        "Notice of thirty days is required.",
        "Notice of 90 days is required.",
        V.CONTRADICTED,
    ),
    # --- negation ---------------------------------------------------------
    Fixture(
        "negation",
        "The invoice payment is not due within thirty days of receipt.",
        "The invoice payment is due within thirty days of receipt.",
        V.CONTRADICTED,
    ),
    Fixture(
        "negation",
        "The deposit is refundable on cancellation.",
        "The deposit is refundable on cancellation.",
        V.SUPPORTED,
    ),
    Fixture(
        "negation",
        "The deposit is not refundable on cancellation.",
        "The deposit is refundable on cancellation.",
        V.CONTRADICTED,
    ),
]


def _accuracy_by_facet() -> dict[str, float]:
    analyzer = LexicalEntailmentAnalyzer()
    totals: dict[str, int] = {}
    correct: dict[str, int] = {}
    for fx in _FIXTURES:
        totals[fx.facet] = totals.get(fx.facet, 0) + 1
        verdict = analyzer.analyze(fx.claim, fx.evidence).verdict
        if verdict is fx.expected:
            correct[fx.facet] = correct.get(fx.facet, 0) + 1
    return {facet: correct.get(facet, 0) / totals[facet] for facet in totals}


def test_each_facet_meets_its_accuracy_threshold() -> None:
    accuracy = _accuracy_by_facet()
    # Every documented facet must be represented and clear its threshold.
    assert set(accuracy) == set(FACET_THRESHOLDS)
    for facet, threshold in FACET_THRESHOLDS.items():
        assert accuracy[facet] >= threshold, f"{facet}: {accuracy[facet]:.0%} < {threshold:.0%}"


def test_overall_accuracy_is_high() -> None:
    analyzer = LexicalEntailmentAnalyzer()
    correct = sum(
        analyzer.analyze(fx.claim, fx.evidence).verdict is fx.expected for fx in _FIXTURES
    )
    assert correct / len(_FIXTURES) >= 0.9
