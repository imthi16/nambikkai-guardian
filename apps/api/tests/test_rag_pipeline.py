"""Unit tests for the grounded RAG graph, generation, and verification.

These exercise the pipeline with fakes only: no database, no LLM, no network.
They assert the two hard gates (authorization and evidence sufficiency), that
generation sees only supplied evidence, that verification drops claims whose
quote is not in the cited chunk or that cite an unknown chunk, that citations
carry exact provenance, and that the trace never leaks the query or evidence.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from app.rag.config import RagConfig
from app.rag.generation import CandidateClaim, ExtractiveGenerator
from app.rag.graph import RagGraph
from app.rag.state import RagState
from app.rag.types import AnswerOutcome, EvidencePassage, RagTrace
from app.rag.verification import ClaimVerifier

WORKSPACE = uuid.UUID(int=1)


def _passage(
    text: str,
    order: int,
    *,
    chunk_id: uuid.UUID | None = None,
    fused: float = 0.9,
    rerank: float | None = 0.8,
    rerank_raw: float | None = None,
    ocr_engine: str | None = None,
    ocr_confidence: float | None = None,
) -> EvidencePassage:
    return EvidencePassage(
        chunk_id=chunk_id or uuid.UUID(int=100 + order),
        document_id=uuid.UUID(int=9),
        document_version_id=uuid.UUID(int=8),
        content=text,
        page_number=order + 1,
        section="S",
        char_start=order * 1000,
        char_end=order * 1000 + len(text),
        language="eng",
        ocr_engine=ocr_engine,
        ocr_confidence=ocr_confidence,
        fused_score=fused,
        rerank_score=rerank,
        order=order,
        rerank_raw_score=rerank_raw,
    )


class FakeRetriever:
    """Returns a fixed evidence list and records the scope it was called with."""

    def __init__(self, passages: Sequence[EvidencePassage]) -> None:
        self._passages = tuple(passages)
        self.calls: list[dict[str, object]] = []

    async def retrieve(self, *, workspace_id, query, top_k, document_id, language):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "query": query,
                "top_k": top_k,
                "document_id": document_id,
                "language": language,
            }
        )
        return self._passages, {"returned_count": len(self._passages)}


def _state(query: str = "invoice payment due date", top_k: int = 8) -> RagState:
    return RagState(
        workspace_id=WORKSPACE,
        query=query,
        top_k=top_k,
        trace=RagTrace(workspace_id=WORKSPACE, detected_language="unknown", top_k=top_k),
    )


async def test_answers_from_evidence_with_exact_citation() -> None:
    evidence = [
        _passage("The invoice payment is due within thirty days of receipt.", 0),
        _passage("The office is closed on public holidays.", 1),
    ]
    graph = RagGraph(FakeRetriever(evidence), config=RagConfig())
    terminal = await graph.run(_state())

    assert terminal.outcome is AnswerOutcome.ANSWERED
    assert terminal.claims, "expected at least one supported claim"
    claim = terminal.claims[0]
    # The cited quote must be a real substring of the cited chunk's content.
    cited = next(p for p in evidence if p.chunk_id == claim.citation.chunk_id)
    assert claim.citation.quote in cited.content
    assert claim.citation.page_number == cited.page_number
    assert terminal.confidence > 0.0


async def test_abstains_on_empty_evidence() -> None:
    graph = RagGraph(FakeRetriever([]), config=RagConfig())
    terminal = await graph.run(_state())

    assert terminal.outcome is AnswerOutcome.ABSTAINED
    assert terminal.claims == ()
    assert terminal.confidence == 0.0
    assert terminal.abstention_reason == "insufficient_evidence"


async def test_abstains_when_evidence_below_sufficiency_threshold() -> None:
    evidence = [_passage("weakly related text about weather", 0, fused=0.05)]
    config = RagConfig(min_evidence=1, min_evidence_score=0.5)
    graph = RagGraph(FakeRetriever(evidence), config=config)
    terminal = await graph.run(_state())

    assert terminal.outcome is AnswerOutcome.ABSTAINED
    assert terminal.abstention_reason == "insufficient_evidence"
    assert terminal.trace.evidence_count == 0


async def test_unauthorized_state_routes_to_abstention() -> None:
    """A state that never authorizes cannot reach retrieval or generation."""
    evidence = [_passage("The invoice payment is due within thirty days.", 0)]
    retriever = FakeRetriever(evidence)
    graph = RagGraph(retriever, config=RagConfig())

    # Force the authorize node to deny by monkeypatching the router precondition
    # through a workspace-less clone is impossible (typed), so simulate an
    # unauthorized decision by patching the node.
    async def deny(state: RagState) -> dict[str, object]:
        return {"authorized": False}

    graph._authorize = deny  # type: ignore[method-assign]
    graph._compiled = graph._build()
    terminal = await graph.run(_state())

    assert terminal.outcome is AnswerOutcome.ABSTAINED
    assert terminal.abstention_reason == "unauthorized"
    # The retriever must never have been consulted on the unauthorized path.
    assert retriever.calls == []


async def test_evidence_is_bounded_to_max_evidence() -> None:
    evidence = [_passage(f"claim number {i} about invoice payment", i) for i in range(10)]
    config = RagConfig(max_evidence=3)
    graph = RagGraph(FakeRetriever(evidence), config=config)
    terminal = await graph.run(_state())

    assert terminal.trace.retrieved_count == 10
    assert terminal.trace.evidence_count == 3


async def test_retriever_receives_workspace_and_filters() -> None:
    retriever = FakeRetriever([_passage("invoice payment due in thirty days", 0)])
    graph = RagGraph(retriever, config=RagConfig())
    document_id = uuid.UUID(int=77)
    state = RagState(
        workspace_id=WORKSPACE,
        query="invoice",
        top_k=5,
        document_id=document_id,
        language_filter="eng",
        trace=RagTrace(workspace_id=WORKSPACE, detected_language="unknown", top_k=5),
    )
    await graph.run(state)

    assert retriever.calls[0]["workspace_id"] == WORKSPACE
    assert retriever.calls[0]["document_id"] == document_id
    assert retriever.calls[0]["language"] == "eng"
    assert retriever.calls[0]["top_k"] == 5


async def test_trace_carries_no_query_or_evidence_text() -> None:
    evidence = [_passage("secret evidence about invoice payment terms", 0)]
    graph = RagGraph(FakeRetriever(evidence), config=RagConfig())
    terminal = await graph.run(_state(query="my very secret query string"))

    serialized = str(terminal.trace.as_metadata())
    assert "my very secret query string" not in serialized
    assert "secret evidence" not in serialized
    assert "detected_language" in terminal.trace.as_metadata()


# --- verification behaviour -------------------------------------------------


def test_verifier_drops_claim_citing_unknown_chunk() -> None:
    evidence = [_passage("invoice payment is due within thirty days", 0)]
    verifier = ClaimVerifier()
    forged = CandidateClaim(
        chunk_id=uuid.UUID(int=999),  # not in the evidence set
        text="fabricated claim",
        quote="fabricated claim",
        quote_char_start=0,
        quote_char_end=16,
    )
    claims = verifier.verify("invoice payment", [forged], evidence)
    assert claims == []


def test_verifier_drops_claim_whose_quote_is_not_in_chunk() -> None:
    passage = _passage("invoice payment is due within thirty days", 0)
    verifier = ClaimVerifier()
    hallucination = CandidateClaim(
        chunk_id=passage.chunk_id,
        text="payment is due within seven days",  # contradicts / not present
        quote="payment is due within seven days",
        quote_char_start=0,
        quote_char_end=32,
    )
    claims = verifier.verify("invoice payment", [hallucination], [passage])
    assert claims == []


def test_verifier_marks_weakly_connected_claim_ambiguous_and_drops_it() -> None:
    passage = _passage("the cafeteria menu changes every week", 0, fused=0.01, rerank=0.0)
    verifier = ClaimVerifier()
    quote = "the cafeteria menu changes every week"
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    # Query shares no tokens, so overlap is 0 and confidence falls below floor.
    claims = verifier.verify("invoice payment due date", [candidate], [passage])
    assert claims == []


def test_verifier_confidence_blends_signals_not_model_score() -> None:
    passage = _passage(
        "invoice payment is due within thirty days",
        0,
        fused=0.9,
        rerank=0.9,
        ocr_engine="paddle",
        ocr_confidence=0.4,
    )
    quote = "invoice payment is due within thirty days"
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    claims = ClaimVerifier().verify("invoice payment thirty days", [candidate], [passage])
    assert len(claims) == 1
    # Low OCR confidence must pull the blended score below the max signal.
    assert 0.0 < claims[0].confidence < 0.9


def test_verifier_born_digital_text_ignores_ocr_signal() -> None:
    """A chunk with no OCR engine is treated as fully reliable, not penalized."""
    from app.rag.verification import VerificationConfig

    passage = _passage(
        "invoice payment is due within thirty days",
        0,
        fused=0.9,
        rerank=0.9,
        ocr_engine=None,
        ocr_confidence=None,
    )
    quote = "invoice payment is due within thirty days"
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    verifier = ClaimVerifier(config=VerificationConfig(min_confidence=0.0))
    claims = verifier.verify("invoice payment thirty days", [candidate], [passage])
    assert len(claims) == 1
    assert claims[0].confidence > 0.5


def test_verifier_rejects_empty_quote() -> None:
    passage = _passage("invoice payment is due within thirty days", 0)
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text="",
        quote="",
        quote_char_start=0,
        quote_char_end=0,
    )
    assert ClaimVerifier().verify("invoice payment", [candidate], [passage]) == []


def test_verifier_rejects_unsupported_claim_text_beside_valid_quote() -> None:
    """A grounded quote cannot smuggle in an assertion absent from the chunk."""
    content = "invoice payment is due within thirty days"
    passage = _passage(content, 0)
    # The quote is verbatim and its offsets are exact, but the asserted text
    # states something the chunk never says.
    quote = "within thirty days"
    start = content.index(quote)
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text="payment is waived entirely",
        quote=quote,
        quote_char_start=start,
        quote_char_end=start + len(quote),
    )
    assert ClaimVerifier().verify("invoice payment", [candidate], [passage]) == []


def test_verifier_rejects_quote_whose_offsets_do_not_recover_it() -> None:
    """Stale/fabricated offsets that don't recover the quote are rejected."""
    content = "invoice payment is due within thirty days"
    passage = _passage(content, 0)
    # The quote text occurs in the chunk, but the offsets point elsewhere, so a
    # viewer would highlight the wrong span.
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text="thirty days",
        quote="thirty days",
        quote_char_start=0,  # points at "invoice pay", not "thirty days"
        quote_char_end=11,
    )
    assert ClaimVerifier().verify("thirty days", [candidate], [passage]) == []


def test_verifier_zero_ocr_confidence_is_not_treated_as_perfect() -> None:
    """A genuine 0.0 OCR confidence must depress the score, not read as 1.0."""
    from app.rag.verification import VerificationConfig

    content = "invoice payment is due within thirty days"
    quote = content
    candidate = CandidateClaim(
        chunk_id=uuid.UUID(int=100),
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    verifier = ClaimVerifier(config=VerificationConfig(min_confidence=0.0))
    zero = _passage(content, 0, ocr_engine="paddle", ocr_confidence=0.0)
    perfect = _passage(content, 0, ocr_engine=None, ocr_confidence=None)
    zero_conf = verifier.verify("invoice payment thirty days", [candidate], [zero])
    perfect_conf = verifier.verify("invoice payment thirty days", [candidate], [perfect])
    assert zero_conf and perfect_conf
    assert zero_conf[0].confidence < perfect_conf[0].confidence


def test_verifier_uses_absolute_rerank_score_not_relative_rank() -> None:
    """A weak match whose normalized rerank rank is 1.0 must not be inflated."""
    # Query shares no tokens with the chunk, so lexical overlap is 0. The
    # normalized rerank score is 1.0 (it is always 1.0 for the top candidate),
    # but the absolute raw score is low, so confidence must stay below the floor.
    passage = _passage(
        "the cafeteria menu changes every week",
        0,
        fused=0.02,
        rerank=1.0,
        rerank_raw=0.05,
    )
    quote = "the cafeteria menu changes every week"
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    assert ClaimVerifier().verify("invoice payment due date", [candidate], [passage]) == []


def test_citation_carries_page_relative_offsets_and_ocr_provenance() -> None:
    passage = _passage(
        "invoice payment is due within thirty days",
        2,  # char_start = 2000 (page-relative)
        ocr_engine="paddle",
        ocr_confidence=0.6,
    )
    quote = "within thirty days"
    start = passage.content.index(quote)
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=start,
        quote_char_end=start + len(quote),
    )
    claims = ClaimVerifier().verify("thirty days invoice", [candidate], [passage])
    assert len(claims) == 1
    citation = claims[0].citation
    assert citation.chunk_char_start == passage.char_start
    assert citation.page_quote_char_start == passage.char_start + start
    assert citation.page_quote_char_end == passage.char_start + start + len(quote)
    assert citation.ocr_engine == "paddle"
    assert citation.ocr_confidence == 0.6


# --- generation behaviour ---------------------------------------------------


def test_generator_only_quotes_supplied_evidence() -> None:
    passage = _passage("Refunds are processed within five business days.", 0)
    generator = ExtractiveGenerator()
    candidates = generator.generate("refund processing time", [passage])
    assert candidates
    for candidate in candidates:
        assert candidate.chunk_id == passage.chunk_id
        assert candidate.quote in passage.content
        # Offsets must recover the quote exactly.
        assert (
            passage.content[candidate.quote_char_start : candidate.quote_char_end]
            == candidate.quote
        )


def test_generator_returns_nothing_for_empty_query() -> None:
    passage = _passage("some evidence", 0)
    assert ExtractiveGenerator().generate("   ", [passage]) == ()


def test_generator_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="max_claims"):
        ExtractiveGenerator(max_claims=0)
    with pytest.raises(ValueError, match="min_overlap"):
        ExtractiveGenerator(min_overlap=2.0)


def test_generator_skips_passages_below_min_overlap() -> None:
    relevant = _passage("invoice payment due within thirty days", 0)
    noise = _passage("the weather is pleasant by the coast today", 1)
    generator = ExtractiveGenerator(min_overlap=0.5)
    candidates = generator.generate("invoice payment thirty days", [relevant, noise])
    assert candidates
    assert all(c.chunk_id == relevant.chunk_id for c in candidates)


def test_generator_caps_claims_at_max_claims() -> None:
    evidence = [_passage(f"invoice payment detail {i} thirty days", i) for i in range(5)]
    generator = ExtractiveGenerator(max_claims=2, min_overlap=0.0)
    candidates = generator.generate("invoice payment thirty days", evidence)
    assert len(candidates) == 2


async def test_partial_outcome_when_some_candidates_dropped() -> None:
    """A claim that fails verification is dropped and the outcome is PARTIAL."""

    good = _passage("invoice payment is due within thirty days", 0)
    good_quote = "invoice payment is due within thirty days"

    class TwoClaimGenerator:
        model = "two-claim"
        model_version = "v1"

        def generate(self, query, evidence):  # type: ignore[no-untyped-def]
            return (
                CandidateClaim(
                    chunk_id=good.chunk_id,
                    text=good_quote,
                    quote=good_quote,
                    quote_char_start=0,
                    quote_char_end=len(good_quote),
                ),
                # Fabricated quote not present in any chunk: must be dropped.
                CandidateClaim(
                    chunk_id=good.chunk_id,
                    text="payment is due within seven days",
                    quote="payment is due within seven days",
                    quote_char_start=0,
                    quote_char_end=32,
                ),
            )

    graph = RagGraph(FakeRetriever([good]), generator=TwoClaimGenerator(), config=RagConfig())
    terminal = await graph.run(_state("invoice payment thirty days"))

    assert terminal.outcome is AnswerOutcome.PARTIAL
    assert len(terminal.claims) == 1
    assert terminal.trace.dropped_claim_count == 1


def test_domain_metadata_views_are_json_safe() -> None:
    passage = _passage("invoice payment thirty days", 0)
    quote = "invoice payment thirty days"
    candidate = CandidateClaim(
        chunk_id=passage.chunk_id,
        text=quote,
        quote=quote,
        quote_char_start=0,
        quote_char_end=len(quote),
    )
    claims = ClaimVerifier().verify("invoice payment thirty days", [candidate], [passage])
    assert claims
    claim = claims[0]
    assert claim.is_supported
    meta = claim.as_metadata()
    assert meta["verdict"] == "supported"
    citation_meta = meta["citation"]
    assert isinstance(citation_meta, dict)
    assert citation_meta["chunk_id"] == str(passage.chunk_id)
