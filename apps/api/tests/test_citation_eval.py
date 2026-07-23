"""Evaluation: every citation the pipeline emits resolves and validates.

This is the measurable AI-evaluation gate for the citation system. It runs the
real extractive generator and verifier over a small corpus, then feeds each
citation the pipeline produced back through :class:`CitationResolver` — the same
validation the public API uses. The property under test is integrity: a claim
the pipeline surfaces must carry a citation that resolves to authorized
provenance whose supporting text is exactly the cited quote. Any drift between
what generation cites and what resolution accepts fails here rather than in
production.
"""

from __future__ import annotations

import uuid

from app.citations.resolver import CitationResolver
from app.citations.types import ChunkProvenance, CitationReference
from app.rag.generation import ExtractiveGenerator
from app.rag.types import EvidencePassage
from app.rag.verification import ClaimVerifier

_CASES: list[tuple[str, list[str]]] = [
    (
        "invoice payment due date",
        [
            "The invoice payment is due within thirty days of receipt.",
            "The office is closed on public holidays.",
        ],
    ),
    (
        "refund processing time",
        ["Refunds are processed within five business days of approval."],
    ),
    (
        "contract renewal notice period",
        [
            "Either party may terminate with sixty days written notice.",
            "Renewal is automatic unless notice is given before the term ends.",
        ],
    ),
]


def _passages(texts: list[str]) -> list[EvidencePassage]:
    version_id = uuid.uuid4()
    passages: list[EvidencePassage] = []
    for order, text in enumerate(texts):
        passages.append(
            EvidencePassage(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                document_version_id=version_id,
                content=text,
                page_number=order + 1,
                section=None,
                char_start=order * 1000,
                char_end=order * 1000 + len(text),
                language="eng",
                ocr_engine=None,
                ocr_confidence=None,
                fused_score=0.9,
                rerank_score=0.9,
                rerank_raw_score=0.9,
                order=order,
            )
        )
    return passages


class PassageReader:
    """Serves chunk provenance from an in-memory evidence set."""

    def __init__(self, passages: list[EvidencePassage]) -> None:
        self._by_id = {p.chunk_id: p for p in passages}

    async def get_provenance(self, chunk_id: uuid.UUID) -> ChunkProvenance | None:
        passage = self._by_id.get(chunk_id)
        if passage is None:
            return None
        return ChunkProvenance(
            chunk_id=passage.chunk_id,
            chunk_index=passage.order,
            document_id=passage.document_id,
            document_title="Doc",
            document_version_id=passage.document_version_id,
            version_number=1,
            content=passage.content,
            page_number=passage.page_number,
            section=passage.section,
            char_start=passage.char_start,
            char_end=passage.char_end,
            language=passage.language,
            ocr_engine=passage.ocr_engine,
            ocr_confidence=passage.ocr_confidence,
        )


async def test_every_pipeline_citation_resolves_and_matches() -> None:
    generator = ExtractiveGenerator()
    verifier = ClaimVerifier()
    total_citations = 0

    for query, texts in _CASES:
        passages = _passages(texts)
        candidates = generator.generate(query, passages)
        claims = verifier.verify(query, candidates, passages)
        assert claims, f"expected a grounded claim for {query!r}"

        resolver = CitationResolver(PassageReader(passages))
        for claim in claims:
            citation = claim.citation
            resolved = await resolver.resolve(
                CitationReference(
                    document_version_id=citation.document_version_id,
                    chunk_id=citation.chunk_id,
                    quote=citation.quote,
                    quote_char_start=citation.quote_char_start,
                    quote_char_end=citation.quote_char_end,
                )
            )
            # The resolver's supporting text is the exact cited quote.
            assert resolved.supporting_text == citation.quote
            # Born-digital corpus: reliability is known and fully reliable.
            assert resolved.support_score == 1.0
            total_citations += 1

    # The evaluation is only meaningful if it actually exercised citations.
    assert total_citations >= len(_CASES)
