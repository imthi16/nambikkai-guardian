"""Evaluation: abstention precision and recall of the decision policy.

Each fixture is a labeled signal set tagged with whether the *correct* action is
to withhold an answer (abstain / ask / escalate) or to answer. The suite scores
the policy as a binary abstention classifier and asserts precision and recall
against documented thresholds, so a regression that makes the pipeline answer
when it should withhold (precision drop) or withhold when it should answer
(recall drop) fails visibly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.decision import ConfidencePolicy, DecisionSignals

# Minimum abstention precision/recall the policy must sustain on these fixtures.
PRECISION_THRESHOLD = 0.9
RECALL_THRESHOLD = 0.9


@dataclass(frozen=True)
class Fixture:
    signals: DecisionSignals
    should_withhold: bool


def _s(**kw: object) -> DecisionSignals:
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
    base.update(kw)
    return DecisionSignals(**base)  # type: ignore[arg-type]


_FIXTURES: list[Fixture] = [
    # Should answer.
    Fixture(_s(), should_withhold=False),
    Fixture(_s(verifier_confidence=0.62), should_withhold=False),
    Fixture(_s(verifier_confidence=0.45), should_withhold=False),  # answer_with_warning
    Fixture(_s(partial_claims=2), should_withhold=False),
    Fixture(_s(min_ocr_confidence=0.4), should_withhold=False),
    Fixture(_s(supported_claims=3, verifier_confidence=0.9), should_withhold=False),
    # Should withhold.
    Fixture(_s(supported_claims=0, verifier_confidence=None, evidence_count=0), True),
    Fixture(_s(supported_claims=0, verifier_confidence=None, evidence_count=3), True),
    Fixture(_s(contradicted_claims=1), should_withhold=True),
    Fixture(
        _s(supported_claims=0, verifier_confidence=None, evidence_count=2, contradicted_claims=2),
        should_withhold=True,
    ),
]


def _confusion() -> tuple[int, int, int, int]:
    """Return (tp, fp, fn, tn) treating 'withhold' as the positive class."""
    policy = ConfidencePolicy()
    tp = fp = fn = tn = 0
    for fx in _FIXTURES:
        withheld = not policy.decide(fx.signals).outcome.is_answering
        if fx.should_withhold and withheld:
            tp += 1
        elif fx.should_withhold and not withheld:
            fn += 1
        elif not fx.should_withhold and withheld:
            fp += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def test_abstention_precision_meets_threshold() -> None:
    tp, fp, _fn, _tn = _confusion()
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    assert precision >= PRECISION_THRESHOLD, f"abstention precision {precision:.2f}"


def test_abstention_recall_meets_threshold() -> None:
    tp, _fp, fn, _tn = _confusion()
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    assert recall >= RECALL_THRESHOLD, f"abstention recall {recall:.2f}"
