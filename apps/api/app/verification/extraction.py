"""Split answer text into atomic claims for independent verification.

An "atomic" claim is a single assertion that can be checked against evidence on
its own. The extractor breaks text at sentence boundaries and at clause
connectives (``;`` and coordinating ``and``) when each side is a substantial
clause, so a compound sentence like "Payment is due in 30 days and refunds take
5 days" becomes two claims that are verified — and cited — separately.

The extractor is behind :class:`AtomicClaimExtractor` so a model-based splitter
can replace the deterministic one. The local splitter never rewrites text: each
atomic claim is a verbatim, trimmed substring of the input.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_SENTENCE = re.compile(r"[^.!?।॥\n]+[.!?।॥]?", re.UNICODE)
_CLAUSE_SPLIT = re.compile(r";|\band\b", re.IGNORECASE)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# A clause must carry at least this many words to stand as its own claim;
# below it, a fragment like "and later" is kept attached to its neighbour.
_MIN_CLAUSE_WORDS = 3


@runtime_checkable
class AtomicClaimExtractor(Protocol):
    """Splits a block of text into independently verifiable atomic claims."""

    def extract(self, text: str) -> list[str]: ...


class SentenceClaimExtractor:
    """Deterministic sentence/clause splitter that never rewrites text."""

    def __init__(self, *, min_clause_words: int = _MIN_CLAUSE_WORDS) -> None:
        self._min_clause_words = min_clause_words

    def extract(self, text: str) -> list[str]:
        claims: list[str] = []
        for match in _SENTENCE.finditer(text):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            claims.extend(self._split_clauses(sentence))
        return claims

    def _split_clauses(self, sentence: str) -> list[str]:
        parts = [part.strip() for part in _CLAUSE_SPLIT.split(sentence)]
        substantial = [part for part in parts if len(_WORD.findall(part)) >= self._min_clause_words]
        # Only treat the split as atomic clauses when *every* piece is a real
        # clause; otherwise keep the sentence whole so we never emit fragments.
        if len(substantial) >= 2 and len(substantial) == len([p for p in parts if p]):
            return substantial
        return [sentence]


def get_default_extractor() -> AtomicClaimExtractor:
    """The local atomic-claim extractor wired for the MVP."""
    return SentenceClaimExtractor()
