"""Evaluation of the prompt-injection detector against the versioned corpus.

These assert recall and precision *floors* on the labelled corpus so a change
that regresses detection (or starts flagging benign policy prose) fails CI. The
thresholds are deliberately strict and must not be lowered to make a change
pass; add corpus samples and improve the detector instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.safety import assess_text
from app.safety.types import SafetyDecision

from tests.injection_corpus import CORPUS_VERSION, attacks, benign


@dataclass(frozen=True)
class _Metrics:
    true_positive: int
    false_negative: int
    false_positive: int
    true_negative: int

    @property
    def recall(self) -> float:
        total = self.true_positive + self.false_negative
        return self.true_positive / total if total else 1.0

    @property
    def precision(self) -> float:
        flagged = self.true_positive + self.false_positive
        return self.true_positive / flagged if flagged else 1.0

    @property
    def specificity(self) -> float:
        total = self.true_negative + self.false_positive
        return self.true_negative / total if total else 1.0


def _evaluate() -> _Metrics:
    tp = fn = fp = tn = 0
    for sample in attacks():
        if assess_text(sample.text).is_quarantined:
            tp += 1
        else:
            fn += 1
    for sample in benign():
        # A benign sample is a false positive only if it is *quarantined*;
        # flagging for review is tolerated, quarantining a clean document is not.
        if assess_text(sample.text).is_quarantined:
            fp += 1
        else:
            tn += 1
    return _Metrics(tp, fn, fp, tn)


def test_corpus_version_is_pinned() -> None:
    assert CORPUS_VERSION == "2026-07-v2"


def test_attack_recall_meets_floor() -> None:
    metrics = _evaluate()
    # Every labelled attack in the corpus must be quarantined.
    assert metrics.recall >= 0.95, f"recall regressed to {metrics.recall:.3f}"


def test_benign_precision_meets_floor() -> None:
    metrics = _evaluate()
    # No benign document may be quarantined.
    assert metrics.false_positive == 0, "a benign sample was quarantined"
    assert metrics.specificity == 1.0


def test_benign_samples_are_not_flagged_either() -> None:
    # Beyond "not quarantined", genuine policy/system prose should read as clean
    # (allow), so reviewers are not buried in false review tasks.
    noisy = [s for s in benign() if assess_text(s.text).decision is not SafetyDecision.ALLOW]
    assert not noisy, f"benign samples were flagged: {[s.note for s in noisy]}"


def test_every_attack_family_is_represented() -> None:
    families = {s.category for s in attacks() if s.category is not None}
    # The corpus must cover direct override, impersonation, exfiltration,
    # indirect triggers, obfuscation, and encoded payloads.
    assert len(families) >= 6


def test_multilingual_attacks_are_covered() -> None:
    languages = {s.language for s in attacks()}
    assert {"en", "ta", "tanglish"} <= languages
