"""Tanglish-to-Tamil transliteration behind a replaceable interface.

The MVP ships a deterministic rule-based transliterator so retrieval works
with zero external dependencies and no paid credentials. It maps common
romanized-Tamil syllables to Tamil script using a longest-match scan. This is
deliberately approximate: transliteration is a *retrieval aid*, and the
`original`/`normalized` forms remain authoritative for provenance.

Swap in a statistical or model-based transliterator later by implementing
`Transliterator`; the query pipeline depends only on the protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Ordered longest-first so multi-character clusters win over their prefixes.
_ROMAN_TO_TAMIL: tuple[tuple[str, str], ...] = (
    ("ndh", "ந்த்"),
    ("zh", "ழ்"),
    ("ng", "ங்"),
    ("nj", "ஞ்"),
    ("th", "த்"),
    ("dh", "த்"),
    ("sh", "ஷ்"),
    ("ch", "ச்"),
    ("kk", "க்க்"),
    ("aa", "ஆ"),
    ("ee", "ஈ"),
    ("ii", "ஈ"),
    ("oo", "ஊ"),
    ("uu", "ஊ"),
    ("ai", "ஐ"),
    ("au", "ஔ"),
    ("a", "அ"),
    ("e", "எ"),
    ("i", "இ"),
    ("o", "ஒ"),
    ("u", "உ"),
    ("k", "க்"),
    ("g", "க்"),
    ("s", "ஸ்"),
    ("t", "ட்"),
    ("d", "ட்"),
    ("n", "ன்"),
    ("p", "ப்"),
    ("b", "ப்"),
    ("m", "ம்"),
    ("y", "ய்"),
    ("r", "ர்"),
    ("l", "ல்"),
    ("v", "வ்"),
    ("w", "வ்"),
    ("h", "ஹ்"),
    ("j", "ஜ்"),
    ("f", "ஃப்"),
)
_MAX_CLUSTER = max(len(key) for key, _ in _ROMAN_TO_TAMIL)
_LOOKUP = dict(_ROMAN_TO_TAMIL)


@runtime_checkable
class Transliterator(Protocol):
    """Renders romanized Tamil (Tanglish) into Tamil script."""

    name: str

    def transliterate(self, text: str) -> str: ...


class RuleBasedTransliterator:
    """Longest-match romanization to Tamil script; no external dependencies."""

    name = "rule-based-v1"

    def transliterate(self, text: str) -> str:
        result: list[str] = []
        for token in text.split(" "):
            result.append(self._transliterate_word(token))
        return " ".join(result)

    def _transliterate_word(self, word: str) -> str:
        if not word:
            return word
        lowered = word.casefold()
        # Non-Latin words (already Tamil, digits, punctuation) pass through.
        if not any(char.isascii() and char.isalpha() for char in lowered):
            return word
        out: list[str] = []
        index = 0
        length = len(lowered)
        while index < length:
            char = lowered[index]
            if not (char.isascii() and char.isalpha()):
                out.append(word[index])
                index += 1
                continue
            matched = False
            for size in range(min(_MAX_CLUSTER, length - index), 0, -1):
                cluster = lowered[index : index + size]
                mapped = _LOOKUP.get(cluster)
                if mapped is not None:
                    out.append(mapped)
                    index += size
                    matched = True
                    break
            if not matched:  # pragma: no cover - table covers a..z
                out.append(word[index])
                index += 1
        return "".join(out)


def get_default_transliterator() -> Transliterator:
    """The provider used by the query pipeline unless one is injected."""
    return RuleBasedTransliterator()
