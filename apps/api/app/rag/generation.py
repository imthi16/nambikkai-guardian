"""The answer generator interface and a deterministic extractive generator.

Generation is kept behind the ``AnswerGenerator`` protocol so a hosted LLM can
replace the MVP generator without changing the pipeline. Whatever the provider,
the contract is the same and enforced downstream: a generator proposes
*candidate claims*, each of which must quote a single supplied evidence passage
and name that passage's ``chunk_id``. The verifier then confirms the quote
actually appears in the cited chunk, so a hallucinating LLM cannot smuggle an
unsupported claim past the gate.

``ExtractiveGenerator`` is the local default. It performs no language modelling:
it selects the evidence passages most relevant to the query and emits one claim
per passage that quotes the passage verbatim. Because the claim *is* a substring
of its cited chunk, it is grounded by construction, which makes the pipeline's
citation and verification stages exercisable offline and in CI without weights.

Evidence content is untrusted data. The generator only measures overlap and
copies spans out of it; it never parses or follows anything the text says.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.language import normalize_for_match
from app.rag.types import EvidencePassage

_SENTENCE = re.compile(r"[^.!?।॥\n]+[.!?।॥]?", re.UNICODE)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class GenerationError(Exception):
    """A generator failed to produce candidate claims."""


@dataclass(frozen=True)
class CandidateClaim:
    """A generator's proposed claim: a quote pinned to one evidence passage.

    ``quote`` must be a substring of the cited chunk's ``content``; the
    verifier rejects the claim otherwise. ``quote_char_start`` is the offset of
    the quote within that chunk's content (not the document), so citations can
    highlight it precisely.
    """

    chunk_id: uuid.UUID
    text: str
    quote: str
    quote_char_start: int
    quote_char_end: int


@runtime_checkable
class AnswerGenerator(Protocol):
    """Proposes grounded candidate claims from minimal evidence.

    Implementations must be deterministic for a given (query, evidence, model
    version) so answers are reproducible and telemetry is meaningful. They must
    only reference the passages they are given and must never invent a
    ``chunk_id``.
    """

    model: str
    model_version: str

    def generate(
        self,
        query: str,
        evidence: Sequence[EvidencePassage],
    ) -> Sequence[CandidateClaim]: ...


class ExtractiveGenerator:
    """Deterministic quote-selecting generator with no external dependencies."""

    def __init__(
        self,
        *,
        model: str = "extractive-local",
        model_version: str = "quote-v1",
        max_claims: int = 3,
        min_overlap: float = 0.1,
    ) -> None:
        if max_claims < 1:
            msg = "max_claims must be at least 1"
            raise ValueError(msg)
        if not 0.0 <= min_overlap <= 1.0:
            msg = "min_overlap must be within [0, 1]"
            raise ValueError(msg)
        self.model = model
        self.model_version = model_version
        self._max_claims = max_claims
        self._min_overlap = min_overlap

    def generate(
        self,
        query: str,
        evidence: Sequence[EvidencePassage],
    ) -> Sequence[CandidateClaim]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return ()

        scored: list[tuple[float, int, CandidateClaim]] = []
        for passage in evidence:
            best = self._best_sentence(query_tokens, passage)
            if best is None:
                continue
            score, claim = best
            if score < self._min_overlap:
                continue
            # Tie-break on the passage's own rank (``order``) so the highest
            # ranked evidence wins when overlap is equal; deterministic.
            scored.append((score, passage.order, claim))

        # Highest overlap first; ties broken by better (lower) evidence rank.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(claim for _, _, claim in scored[: self._max_claims])

    def _best_sentence(
        self,
        query_tokens: set[str],
        passage: EvidencePassage,
    ) -> tuple[float, CandidateClaim] | None:
        """Pick the single sentence in a passage that best covers the query."""
        best_score = 0.0
        best_claim: CandidateClaim | None = None
        for match in _SENTENCE.finditer(passage.content):
            raw = match.group(0)
            quote = raw.strip()
            if not quote:
                continue
            score = self._coverage(query_tokens, quote)
            if best_claim is not None and score <= best_score:
                continue
            # Recover the trimmed span's offsets within the chunk content.
            leading = len(raw) - len(raw.lstrip())
            start = match.start() + leading
            end = start + len(quote)
            best_score = score
            best_claim = CandidateClaim(
                chunk_id=passage.chunk_id,
                text=quote,
                quote=quote,
                quote_char_start=start,
                quote_char_end=end,
            )
        if best_claim is None:
            return None
        return best_score, best_claim

    def _coverage(self, query_tokens: set[str], text: str) -> float:
        """Fraction of distinct query features that appear in ``text``, in [0, 1].

        Features are unigrams plus character trigrams (the same basis the local
        reranker and embedder use), so morphological variants such as
        "refund"/"refunds" and Tamil inflections still overlap without any
        language-specific stemming.
        """
        passage_features = self._features(text)
        if not passage_features or not query_tokens:
            return 0.0
        shared = query_tokens & passage_features
        return len(shared) / len(query_tokens)

    def _tokens(self, text: str) -> set[str]:
        return self._features(text)

    def _features(self, text: str) -> set[str]:
        """Unigrams plus character trigrams over normalized text."""
        tokens = _TOKEN.findall(normalize_for_match(text))
        features: set[str] = set(tokens)
        for token in tokens:
            padded = f"#{token}#"
            features.update(padded[i : i + 3] for i in range(len(padded) - 2))
        return features


def get_default_generator() -> AnswerGenerator:
    """The extractive generator wired for the MVP."""
    return ExtractiveGenerator()
