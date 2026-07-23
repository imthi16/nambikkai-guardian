"""Atomic claim verification and calibrated confidence.

The verifier is the trust gate between a generator's *candidate* claims and the
*supported* claims that may appear in an answer. For each candidate it:

1. resolves the cited passage from the authorized evidence set (a claim that
   cites an unknown chunk is rejected: the generator may only cite what it was
   given);
2. confirms the quoted span actually occurs in that chunk's content, so a
   fabricated or paraphrased quote cannot be cited;
3. assigns a verdict and a *calibrated* confidence that blends retrieval,
   rerank, OCR, and lexical-overlap signals rather than trusting any single
   score or a model's self-reported confidence.

Only ``SUPPORTED`` claims survive into the answer. This is deliberately
conservative: dropping a true-but-unverified claim is safer than surfacing an
unsupported one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.language import normalize_for_match
from app.rag.generation import CandidateClaim
from app.rag.types import AtomicClaim, Citation, ClaimVerdict, EvidencePassage

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class VerificationConfig:
    """Weights and floor for confidence calibration.

    Weights are normalized at use, so they express relative importance rather
    than needing to sum to one. ``min_confidence`` is the floor a supported
    claim must clear; below it the claim is treated as too weakly grounded and
    marked ambiguous so it is dropped from the answer.
    """

    fused_weight: float = 0.25
    rerank_weight: float = 0.35
    overlap_weight: float = 0.30
    ocr_weight: float = 0.10
    min_confidence: float = 0.3


class ClaimVerifier:
    """Verifies candidate claims against authorized evidence and scores them."""

    def __init__(
        self,
        *,
        config: VerificationConfig | None = None,
        verifier: str = "extractive-verifier-v1",
    ) -> None:
        self._config = config or VerificationConfig()
        self.verifier = verifier

    def verify(
        self,
        query: str,
        candidates: Sequence[CandidateClaim],
        evidence: Sequence[EvidencePassage],
    ) -> list[AtomicClaim]:
        by_chunk = {passage.chunk_id: passage for passage in evidence}
        query_tokens = self._tokens(query)

        verified: list[AtomicClaim] = []
        index = 0
        for candidate in candidates:
            passage = by_chunk.get(candidate.chunk_id)
            if passage is None:
                # The generator cited a chunk outside the authorized set. This
                # is never allowed; drop it rather than fabricate provenance.
                continue
            verdict, confidence = self._assess(query_tokens, candidate, passage)
            if verdict is not ClaimVerdict.SUPPORTED:
                continue
            verified.append(
                AtomicClaim(
                    index=index,
                    text=candidate.text,
                    citation=self._citation(candidate, passage),
                    verdict=verdict,
                    confidence=confidence,
                )
            )
            index += 1
        return verified

    def _assess(
        self,
        query_tokens: set[str],
        candidate: CandidateClaim,
        passage: EvidencePassage,
    ) -> tuple[ClaimVerdict, float]:
        """Return the verdict and calibrated confidence for one candidate."""
        # The citation offsets must recover the quote *exactly* from the cited
        # chunk. A normalized substring test would accept stale or fabricated
        # offsets and case/whitespace-shifted spans, so a viewer could highlight
        # the wrong source text; requiring exact recovery closes that gap.
        content = passage.content
        start, end = candidate.quote_char_start, candidate.quote_char_end
        if not candidate.quote or not (0 <= start <= end <= len(content)):
            return ClaimVerdict.UNSUPPORTED, 0.0
        if content[start:end] != candidate.quote:
            return ClaimVerdict.UNSUPPORTED, 0.0

        # The asserted claim text must itself be grounded in the cited chunk,
        # not merely accompanied by a harmless verbatim quote. Without this, a
        # hosted or compromised generator could surface an arbitrary assertion
        # while citing unrelated text that happens to occur in the chunk.
        claim_norm = normalize_for_match(candidate.text)
        if not claim_norm or claim_norm not in normalize_for_match(content):
            return ClaimVerdict.UNSUPPORTED, 0.0

        confidence = self._confidence(query_tokens, candidate, passage)
        if confidence < self._config.min_confidence:
            # Grounded in text but too weakly connected to the query to assert.
            return ClaimVerdict.AMBIGUOUS, confidence
        return ClaimVerdict.SUPPORTED, confidence

    def _confidence(
        self,
        query_tokens: set[str],
        candidate: CandidateClaim,
        passage: EvidencePassage,
    ) -> float:
        """Blend retrieval, rerank, OCR, and overlap signals into [0, 1].

        No single signal is trusted alone, and a model's self-reported score is
        never used. The rerank contribution uses the reranker's *absolute* raw
        score, not the within-set normalized rank (which maps the top candidate
        to 1.0 regardless of relevance and would let a weak-but-least-bad match
        cross the support floor). OCR confidence only participates when the
        chunk came from OCR; for born-digital text it is treated as reliable.
        """
        cfg = self._config
        fused = _clamp(passage.fused_score)
        # Fall back to the fused score only when the reranker did not run at all.
        if passage.rerank_raw_score is not None:
            rerank = _clamp(passage.rerank_raw_score)
        else:
            rerank = fused
        overlap = self._overlap(query_tokens, candidate.quote)
        # A born-digital chunk (no OCR engine) is fully reliable; an OCR chunk
        # uses its confidence verbatim, including a genuine 0.0 — treating a
        # missing value as perfect would inflate the least-reliable evidence.
        if passage.ocr_engine and passage.ocr_confidence is not None:
            ocr = _clamp(passage.ocr_confidence)
        else:
            ocr = 1.0

        weights = (cfg.fused_weight, cfg.rerank_weight, cfg.overlap_weight, cfg.ocr_weight)
        signals = (fused, rerank, overlap, ocr)
        total = sum(weights)
        if total <= 0.0:
            return 0.0
        blended = sum(w * s for w, s in zip(weights, signals, strict=True)) / total
        return round(_clamp(blended), 6)

    def _overlap(self, query_tokens: set[str], quote: str) -> float:
        if not query_tokens:
            return 0.0
        quote_tokens = self._tokens(quote)
        if not quote_tokens:
            return 0.0
        return len(query_tokens & quote_tokens) / len(query_tokens)

    def _citation(self, candidate: CandidateClaim, passage: EvidencePassage) -> Citation:
        return Citation(
            chunk_id=passage.chunk_id,
            document_id=passage.document_id,
            document_version_id=passage.document_version_id,
            quote=candidate.quote,
            quote_char_start=candidate.quote_char_start,
            quote_char_end=candidate.quote_char_end,
            page_number=passage.page_number,
            section=passage.section,
            language=passage.language,
            chunk_char_start=passage.char_start,
            chunk_char_end=passage.char_end,
            ocr_engine=passage.ocr_engine,
            ocr_confidence=passage.ocr_confidence,
        )

    def _tokens(self, text: str) -> set[str]:
        return set(_TOKEN.findall(normalize_for_match(text)))


def _clamp(value: float) -> float:
    """Clamp any score into [0, 1]; retrieval scores can drift slightly out."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
