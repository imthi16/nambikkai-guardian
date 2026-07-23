"""Deterministic claim-to-evidence entailment analysis.

The analyzer decides whether an atomic claim is supported, partially supported,
contradicted, or unsupported by an evidence span, and explains why. It does not
model natural language; it compares the *signals* both sides assert (numbers
bound to units, dates, negation polarity, names, and lexical content) with a
fixed, documented rule set:

* **Contradiction wins.** A number bound to a shared unit with a different
  value, a conflicting date, or a flipped negation on an otherwise-aligned
  sentence means the evidence *refutes* the claim. Contradicted claims are
  never surfaced.
* **Support requires near-complete lexical coverage** (``SUPPORT_COVERAGE``) of
  the claim's content by a single evidence sentence, with every asserted number
  and date entailed and negation consistent.
* **Partial support** is moderate coverage (``PARTIAL_COVERAGE``) with no
  conflict — some of the claim is grounded, not all.
* Anything weaker is **unsupported**.

The analyzer is exposed behind :class:`EntailmentAnalyzer` so a hosted NLI model
could replace the local implementation without changing the verifier.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from app.verification.signals import ClaimSignals, extract_signals, tokenize
from app.verification.types import EntailmentResult, EntailmentVerdict

# Documented decision thresholds (see module docstring and the evaluation
# fixtures). Coverage is the fraction of claim content tokens present in the
# aligned evidence sentence.
SUPPORT_COVERAGE = 0.9
PARTIAL_COVERAGE = 0.5

_SENTENCE = re.compile(r"[^.!?।॥\n]+[.!?।॥]?", re.UNICODE)


@runtime_checkable
class EntailmentAnalyzer(Protocol):
    """Classifies a claim against an evidence span with an explanation."""

    def analyze(self, claim: str, evidence: str) -> EntailmentResult: ...


class LexicalEntailmentAnalyzer:
    """Signal-based entailment with strict numeric, date, and negation checks."""

    def __init__(
        self,
        *,
        support_coverage: float = SUPPORT_COVERAGE,
        partial_coverage: float = PARTIAL_COVERAGE,
    ) -> None:
        self._support = support_coverage
        self._partial = partial_coverage

    def analyze(self, claim: str, evidence: str) -> EntailmentResult:
        claim_sig = extract_signals(claim)
        if not claim_sig.content_tokens and not claim_sig.unit_numbers:
            return EntailmentResult(EntailmentVerdict.UNSUPPORTED, "empty claim", 0.0)

        # Align the claim to the single evidence sentence it overlaps most, so
        # negation and numeric checks compare like with like rather than against
        # unrelated sentences elsewhere in the chunk.
        aligned_text = self._aligned_sentence(claim_sig, evidence)
        aligned = extract_signals(aligned_text)
        coverage = self._coverage(claim_sig, aligned)

        conflict = self._contradiction(claim_sig, aligned, coverage)
        if conflict is not None:
            return EntailmentResult(EntailmentVerdict.CONTRADICTED, conflict, coverage)

        numbers_ok = self._numbers_entailed(claim_sig, aligned)
        dates_ok = claim_sig.dates <= aligned.dates or not claim_sig.dates
        names_ok = claim_sig.names <= aligned.names or not claim_sig.names

        if coverage >= self._support and numbers_ok and dates_ok and names_ok:
            return EntailmentResult(
                EntailmentVerdict.SUPPORTED,
                "all claim terms, numbers, and dates are present in the evidence",
                coverage,
            )
        if coverage >= self._partial:
            return EntailmentResult(
                EntailmentVerdict.PARTIAL,
                self._partial_reason(claim_sig, aligned, numbers_ok, dates_ok, names_ok, coverage),
                coverage,
            )
        return EntailmentResult(
            EntailmentVerdict.UNSUPPORTED,
            f"only {coverage:.0%} of the claim's terms appear in the evidence",
            coverage,
        )

    # --- alignment & coverage ----------------------------------------------

    def _aligned_sentence(self, claim: ClaimSignals, evidence: str) -> str:
        """Pick the evidence sentence sharing the most content with the claim."""
        best_text = evidence
        best_overlap = -1
        for match in _SENTENCE.finditer(evidence):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            tokens = set(tokenize(sentence))
            overlap = len(claim.content_tokens & tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_text = sentence
        return best_text

    def _coverage(self, claim: ClaimSignals, aligned: ClaimSignals) -> float:
        if not claim.content_tokens:
            return 1.0
        shared = claim.content_tokens & aligned.content_tokens
        return len(shared) / len(claim.content_tokens)

    # --- contradiction detection -------------------------------------------

    def _contradiction(
        self, claim: ClaimSignals, aligned: ClaimSignals, coverage: float
    ) -> str | None:
        """Return an explanation if the evidence refutes the claim, else None.

        A numeric, date, or negation conflict only counts as a contradiction
        when the two sides are actually *about the same statement* — i.e. the
        claim's terms are at least partially covered by the aligned sentence.
        Otherwise a shared unit like "days" across unrelated statements (payment
        due in 30 vs. late fees after 90) would masquerade as a refutation; with
        low coverage that is simply unsupported.
        """
        if coverage < self._partial:
            return None
        for unit, claim_values in claim.unit_numbers.items():
            evidence_values = aligned.unit_numbers.get(unit)
            if evidence_values and not (claim_values & evidence_values):
                return (
                    f"number mismatch for '{unit}': claim states "
                    f"{_fmt(claim_values)} but evidence states {_fmt(evidence_values)}"
                )
        if claim.dates and aligned.dates and not (claim.dates & aligned.dates):
            return (
                f"date mismatch: claim states {sorted(claim.dates)} "
                f"but evidence states {sorted(aligned.dates)}"
            )
        # A flipped negation only contradicts when the two sides are clearly
        # about the same statement (high lexical overlap); otherwise the
        # negation may belong to an unrelated clause.
        if coverage >= self._support and claim.negated != aligned.negated:
            return "negation mismatch: claim and evidence disagree on whether the statement holds"
        return None

    def _numbers_entailed(self, claim: ClaimSignals, aligned: ClaimSignals) -> bool:
        """Every number the claim asserts is present (same unit) in the evidence."""
        for unit, values in claim.unit_numbers.items():
            evidence_values = aligned.unit_numbers.get(unit, frozenset())
            if not values <= evidence_values:
                return False
        return bool(claim.bare_numbers <= aligned.bare_numbers) or not claim.bare_numbers

    def _partial_reason(
        self,
        claim: ClaimSignals,
        aligned: ClaimSignals,
        numbers_ok: bool,
        dates_ok: bool,
        names_ok: bool,
        coverage: float,
    ) -> str:
        if not numbers_ok:
            return "some numbers in the claim are not confirmed by the evidence"
        if not dates_ok:
            return "a date in the claim is not confirmed by the evidence"
        if not names_ok:
            missing = sorted(claim.names - aligned.names)
            return f"named entities not found in the evidence: {missing}"
        if aligned.conditional and not claim.conditional:
            return "the evidence qualifies the statement with a condition the claim omits"
        return f"only {coverage:.0%} of the claim's terms appear in the evidence"


def _fmt(values: frozenset[float]) -> str:
    return ", ".join(str(int(v) if v.is_integer() else v) for v in sorted(values))


def get_default_analyzer() -> EntailmentAnalyzer:
    """The local entailment analyzer wired for the MVP."""
    return LexicalEntailmentAnalyzer()
