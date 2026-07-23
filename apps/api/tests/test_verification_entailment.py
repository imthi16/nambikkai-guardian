"""Unit tests for the entailment analyzer and atomic-claim extractor."""

from __future__ import annotations

from app.verification.entailment import LexicalEntailmentAnalyzer
from app.verification.extraction import SentenceClaimExtractor
from app.verification.types import EntailmentVerdict

EVIDENCE = "The invoice payment is due within thirty days of receipt."


def _verdict(claim: str, evidence: str = EVIDENCE) -> EntailmentVerdict:
    return LexicalEntailmentAnalyzer().analyze(claim, evidence).verdict


def test_verbatim_claim_is_supported() -> None:
    assert _verdict(EVIDENCE) is EntailmentVerdict.SUPPORTED


def test_digit_and_word_numbers_are_equivalent_for_support() -> None:
    assert _verdict("The invoice payment is due within 30 days of receipt.") is (
        EntailmentVerdict.SUPPORTED
    )


def test_numeric_mismatch_is_contradicted() -> None:
    result = LexicalEntailmentAnalyzer().analyze(
        "The invoice payment is due within sixty days of receipt.", EVIDENCE
    )
    assert result.verdict is EntailmentVerdict.CONTRADICTED
    assert "number mismatch" in result.explanation


def test_negation_flip_is_contradicted() -> None:
    assert _verdict("The invoice payment is not due within thirty days of receipt.") is (
        EntailmentVerdict.CONTRADICTED
    )


def test_date_mismatch_is_contradicted() -> None:
    result = LexicalEntailmentAnalyzer().analyze(
        "The term ends on 2024-06-30.", "The term ends on 2024-03-31 per the agreement."
    )
    assert result.verdict is EntailmentVerdict.CONTRADICTED
    assert "date mismatch" in result.explanation


def test_shared_unit_across_unrelated_statements_is_not_a_contradiction() -> None:
    # Both mention "days" but the claim is about a different subject with little
    # lexical overlap, so it is unsupported, not falsely contradicted.
    assert _verdict("Late fees accrue after ninety days of arrears.") is (
        EntailmentVerdict.UNSUPPORTED
    )


def test_partial_support_when_only_some_terms_match() -> None:
    assert (
        _verdict(
            "Either party may terminate the contract.",
            "Either party may terminate with sixty days written notice.",
        )
        is EntailmentVerdict.PARTIAL
    )


def test_unrelated_claim_is_unsupported() -> None:
    assert _verdict("The cafeteria menu changes every week.") is EntailmentVerdict.UNSUPPORTED


def test_supported_claim_explains_itself() -> None:
    result = LexicalEntailmentAnalyzer().analyze(EVIDENCE, EVIDENCE)
    assert "present in the evidence" in result.explanation


def test_extractor_splits_compound_sentence_into_atomic_clauses() -> None:
    claims = SentenceClaimExtractor().extract(
        "Payment is due in 30 days and refunds take 5 days. Fees apply."
    )
    assert "Payment is due in 30 days" in claims
    assert "refunds take 5 days." in claims
    # A short trailing sentence stays whole rather than being fragmented.
    assert "Fees apply." in claims


def test_extractor_keeps_short_sentence_whole() -> None:
    # "and later" is too short to stand alone, so the sentence is not split.
    assert SentenceClaimExtractor().extract("It ships and later returns.") == [
        "It ships and later returns."
    ]


def test_extractor_never_rewrites_text() -> None:
    text = "The invoice payment is due within thirty days of receipt."
    for claim in SentenceClaimExtractor().extract(text):
        assert claim in text
