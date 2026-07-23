"""Deterministic factual signals extracted from a claim or evidence span.

Claim verification cannot lean on a model's say-so, so entailment is decided
from signals that are extracted the same way every time: the numbers a sentence
asserts (and the unit each is attached to), the calendar dates it names, its
negation polarity, the proper names it mentions, and whether it is hedged by a
condition or exception. Every function here is pure and language-agnostic where
it can be (digits, ISO dates), with a small English lexicon for number words,
month names, and negation/condition cues.

Nothing here interprets text as instructions; it only measures it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.language import normalize_for_match

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Small-integer number words and multiples of ten, enough for durations,
# counts, and notice periods that appear in contracts and invoices.
_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_NUMBER_WORDS = {**_ONES, **_TENS, "hundred": 100}

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# Words that sit between a number and its unit ("five business days") and must
# be skipped so the number binds to the real unit noun.
_UNIT_MODIFIERS = frozenset({"business", "calendar", "working", "full", "whole", "net"})

# Negation cues. Presence flips a sentence's polarity for the shared predicate.
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "cannot",
        "cant",
        "wont",
        "dont",
        "doesnt",
        "didnt",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "neither",
        "nor",
        "none",
        "excluding",
    }
)

# Cues that hedge an assertion with a condition or carve-out.
_CONDITION_CUES = frozenset(
    {"unless", "except", "if", "provided", "subject", "when", "whenever", "until", "only"}
)

# High-frequency function words excluded from "content" comparison so coverage
# reflects meaningful overlap rather than shared articles and prepositions.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "with",
        "as",
        "from",
        "will",
        "shall",
        "may",
        "must",
        "can",
        "within",
        "into",
        "per",
    }
)


@dataclass(frozen=True)
class ClaimSignals:
    """The factual signals a single span asserts."""

    tokens: tuple[str, ...]
    content_tokens: frozenset[str]
    unit_numbers: dict[str, frozenset[float]] = field(default_factory=dict)
    bare_numbers: frozenset[float] = frozenset()
    dates: frozenset[str] = frozenset()
    negated: bool = False
    names: frozenset[str] = frozenset()
    conditional: bool = False


def tokenize(text: str) -> list[str]:
    """Normalized word tokens (case-folded, punctuation stripped)."""
    return _TOKEN.findall(normalize_for_match(text))


def _as_number(token: str) -> float | None:
    if token.isdigit():
        return float(token)
    return float(_NUMBER_WORDS[token]) if token in _NUMBER_WORDS else None


def extract_unit_numbers(tokens: list[str]) -> tuple[dict[str, set[float]], set[float]]:
    """Bind each number to the unit noun it modifies.

    Returns ``(unit -> numbers, bare_numbers)``. ``five business days`` yields
    ``{"days": {5.0}}``; a number with no following noun before the next number
    or end of clause is recorded as a bare number so a plain count is not lost.
    Number words and digits normalize to the same value, so ``thirty`` and
    ``30`` never look like a conflict.
    """
    unit_numbers: dict[str, set[float]] = {}
    bare: set[float] = set()
    for i, token in enumerate(tokens):
        value = _as_number(token)
        if value is None:
            continue
        unit = None
        for candidate in tokens[i + 1 : i + 4]:
            if _as_number(candidate) is not None:
                break
            if candidate in _UNIT_MODIFIERS or candidate in _STOPWORDS:
                continue
            unit = candidate
            break
        if unit is None:
            bare.add(value)
        else:
            unit_numbers.setdefault(unit, set()).add(value)
    return unit_numbers, bare


def extract_dates(text: str) -> set[str]:
    """Calendar dates as normalized ``YYYY-MM-DD`` / ``YYYY-MM`` / ``month`` keys.

    ISO dates, ``March 2024``, and a bare month name are recognized; this is a
    conservative set aimed at the dates that appear in the evaluation fixtures,
    not a general date parser.
    """
    dates: set[str] = set()
    for match in _ISO_DATE.findall(text):
        dates.add(match)
    tokens = tokenize(text)
    for i, token in enumerate(tokens):
        if token in _MONTHS:
            month = _MONTHS[token]
            year = None
            for nxt in tokens[i + 1 : i + 3]:
                if nxt.isdigit() and len(nxt) == 4:
                    year = nxt
                    break
            dates.add(f"{year}-{month:02d}" if year else f"month-{month:02d}")
    return dates


def _has_negation(tokens: list[str]) -> bool:
    return any(token in _NEGATIONS for token in tokens)


def _is_conditional(tokens: list[str]) -> bool:
    return any(token in _CONDITION_CUES for token in tokens)


def _names(text: str) -> set[str]:
    """Proper names: capitalized word runs, excluding a sentence-initial word.

    Uses the raw (un-normalized) text so casing survives. The first token of the
    text is ignored because sentence-initial capitalization is not a name.
    """
    names: set[str] = set()
    raw_tokens = re.findall(r"[^\W_]+", text, re.UNICODE)
    for i, token in enumerate(raw_tokens):
        if i == 0:
            continue
        if token[0].isupper() and token.lower() not in _NUMBER_WORDS:
            names.add(token.casefold())
    return names


def extract_signals(text: str) -> ClaimSignals:
    """Extract the full signal set from one span of text.

    Number extraction runs on a copy with ISO dates removed, so the digits of a
    date (``2024-03-31``) are never mistaken for bare counts. Negation cues and
    numbers are excluded from ``content_tokens`` — negation is a separate
    polarity signal and numbers are compared by value — so lexical coverage
    reflects meaningful terms only.
    """
    normalized = normalize_for_match(text)
    tokens = _TOKEN.findall(normalized)
    number_tokens = _TOKEN.findall(_ISO_DATE.sub(" ", normalized))
    unit_numbers, bare = extract_unit_numbers(number_tokens)
    content = frozenset(
        t for t in tokens if t not in _STOPWORDS and t not in _NEGATIONS and _as_number(t) is None
    )
    return ClaimSignals(
        tokens=tuple(tokens),
        content_tokens=content,
        unit_numbers={unit: frozenset(values) for unit, values in unit_numbers.items()},
        bare_numbers=frozenset(bare),
        dates=frozenset(extract_dates(text)),
        negated=_has_negation(tokens),
        names=frozenset(_names(text)),
        conditional=_is_conditional(tokens),
    )
