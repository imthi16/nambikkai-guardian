"""Unit tests for the deterministic factual-signal extractors."""

from __future__ import annotations

from app.verification.signals import extract_dates, extract_signals, extract_unit_numbers, tokenize


def test_number_words_and_digits_bind_to_the_same_value() -> None:
    word_units, _ = extract_unit_numbers(tokenize("due within thirty days"))
    digit_units, _ = extract_unit_numbers(tokenize("due within 30 days"))
    assert word_units == {"days": {30.0}}
    assert digit_units == {"days": {30.0}}


def test_unit_binding_skips_modifiers() -> None:
    units, bare = extract_unit_numbers(tokenize("refunds within five business days"))
    assert units == {"days": {5.0}}
    assert bare == set()


def test_iso_and_month_dates_are_extracted() -> None:
    assert "2024-03-31" in extract_dates("the term ends on 2024-03-31.")
    assert "2024-03" in extract_dates("effective March 2024 onward")


def test_iso_date_digits_do_not_become_numbers() -> None:
    signals = extract_signals("The term ends on 2024-03-31.")
    assert signals.dates == frozenset({"2024-03-31"})
    # The date's digits must not leak into number extraction.
    assert signals.unit_numbers == {}
    assert signals.bare_numbers == frozenset()


def test_negation_polarity_and_content_exclusion() -> None:
    positive = extract_signals("the contract may be terminated")
    negative = extract_signals("the contract may not be terminated")
    assert positive.negated is False
    assert negative.negated is True
    # "not" is a polarity signal, never a content token, so the two spans share
    # identical content (only their polarity differs).
    assert positive.content_tokens == negative.content_tokens


def test_condition_cue_and_names_detected() -> None:
    signals = extract_signals("Renewal is automatic unless Acme gives notice")
    assert signals.conditional is True
    assert "acme" in signals.names
