"""Spelling normalization for romanized Tamil behind a replaceable interface.

Tanglish has no fixed orthography: "epadi", "eppadi", and "epdi" all mean the
same. This adapter maps known spelling variants to a single canonical token so
that transliteration and lexical retrieval see one form. It only rewrites
tokens it recognizes; anything unknown is passed through untouched, and the
`original` query is never mutated (that lives in `ProcessedQuery.original`).

Provide a richer dictionary- or model-backed normalizer later by implementing
`SpellingNormalizer`.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

# variant -> canonical. Kept small, auditable, and reversible in review.
_VARIANTS: dict[str, str] = {
    "epdi": "eppadi",
    "epadi": "eppadi",
    "eppdi": "eppadi",
    "iruku": "irukku",
    "pannu": "panna",
    "pannuga": "pannunga",
    "pannunga": "pannunga",
    "vanakam": "vanakkam",
    "vanakkam": "vanakkam",
    "nandri": "nandri",
    "nanri": "nandri",
    "romba": "romba",
    "rmba": "romba",
    "konjm": "konjam",
    "yaru": "yaaru",
    "amaa": "aamaa",
    "ama": "aamaa",
}

_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


@runtime_checkable
class SpellingNormalizer(Protocol):
    """Canonicalizes spelling variants of romanized Tamil tokens."""

    name: str

    def normalize(self, text: str) -> str: ...


class DictionarySpellingNormalizer:
    """Token-level variant folding using a fixed, reviewable dictionary."""

    name = "dictionary-v1"

    def __init__(self, variants: dict[str, str] | None = None) -> None:
        self._variants = variants if variants is not None else _VARIANTS

    def normalize(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            token = match.group()
            canonical = self._variants.get(token.casefold())
            return canonical if canonical is not None else token

        return _TOKEN.sub(replace, text)


def get_default_spelling_normalizer() -> SpellingNormalizer:
    """The provider used by the query pipeline unless one is injected."""
    return DictionarySpellingNormalizer()
