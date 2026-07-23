"""Measurable evaluation of the grounded-answer pipeline.

Offline, deterministic, and DB-free: the pipeline runs against a fixed
multilingual evidence corpus through a fake retriever that returns the corpus
ranked by lexical overlap (a stand-in for the real hybrid retriever). The suite
asserts three measurable properties the feature promises:

* **grounding** — for answerable queries the pipeline answers and every cited
  quote is a real substring of its cited chunk (no hallucinated citations);
* **abstention** — for out-of-corpus queries the pipeline abstains rather than
  inventing an answer, above a minimum abstention rate;
* **multilingual coverage** — English, Tamil, and Tanglish answerable queries
  all ground at or above a minimum accuracy.

Thresholds guard against regressions; they are modest because the MVP generator
is extractive and the retriever here is lexical, not semantic.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.language import normalize_for_match
from app.rag.config import RagConfig
from app.rag.graph import RagGraph
from app.rag.state import RagState
from app.rag.types import AnswerOutcome, EvidencePassage, RagTrace

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
WORKSPACE = uuid.UUID(int=42)


@dataclass(frozen=True)
class Doc:
    chunk_id: uuid.UUID
    text: str
    language: str


# A small trilingual corpus. Each entry is one chunk of authorized evidence.
CORPUS: tuple[Doc, ...] = (
    Doc(uuid.UUID(int=1), "The invoice payment is due within thirty days of receipt.", "eng"),
    Doc(uuid.UUID(int=2), "Refunds are processed within five business days.", "eng"),
    Doc(uuid.UUID(int=3), "Annual leave accrues monthly and carries over once.", "eng"),
    Doc(uuid.UUID(int=4), "இந்த ஒப்பந்தம் மார்ச் மாதம் முடிவடைகிறது.", "tam"),
    Doc(uuid.UUID(int=5), "ஊழியர் விடுப்பு ஒவ்வொரு மாதமும் சேர்க்கப்படும்.", "tam"),
    Doc(uuid.UUID(int=6), "Payment thirty days la pannanum, illena penalty varum.", "tanglish"),
)


@dataclass(frozen=True)
class EvalCase:
    query: str
    answerable: bool
    language: str


CASES: tuple[EvalCase, ...] = (
    EvalCase("invoice payment due date", True, "eng"),
    EvalCase("refund processing time", True, "eng"),
    EvalCase("annual leave policy carries over", True, "eng"),
    EvalCase("ஒப்பந்தம் முடிவடைகிறது மார்ச்", True, "tam"),
    EvalCase("விடுப்பு ஒவ்வொரு மாதமும் சேர்க்கப்படும்", True, "tam"),
    EvalCase("payment thirty days penalty", True, "tanglish"),
    # Out-of-corpus queries: the pipeline must abstain, not invent.
    EvalCase("who won the football world cup", False, "eng"),
    EvalCase("recipe for chocolate cake", False, "eng"),
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(normalize_for_match(text)))


class LexicalCorpusRetriever:
    """Ranks the fixed corpus by query-token overlap; a stand-in for hybrid."""

    async def retrieve(self, *, workspace_id, query, top_k, document_id, language):  # type: ignore[no-untyped-def]
        query_tokens = _tokens(query)
        scored: list[tuple[float, Doc]] = []
        for doc in CORPUS:
            doc_tokens = _tokens(doc.text)
            if not doc_tokens:
                continue
            overlap = len(query_tokens & doc_tokens) / max(len(query_tokens), 1)
            if overlap > 0.0:
                scored.append((overlap, doc))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id.int))
        passages: list[EvidencePassage] = []
        for order, (score, doc) in enumerate(scored[:top_k]):
            passages.append(
                EvidencePassage(
                    chunk_id=doc.chunk_id,
                    document_id=uuid.UUID(int=900),
                    document_version_id=uuid.UUID(int=800),
                    content=doc.text,
                    page_number=1,
                    section=None,
                    char_start=0,
                    char_end=len(doc.text),
                    language=doc.language,
                    ocr_engine=None,
                    ocr_confidence=None,
                    fused_score=min(1.0, score),
                    rerank_score=min(1.0, score),
                    order=order,
                )
            )
        return passages, {"returned_count": len(passages)}


def _run(query: str) -> tuple[AnswerOutcome, RagState]:
    graph = RagGraph(
        LexicalCorpusRetriever(),
        # Require some overlap so pure-noise retrievals are gated out.
        config=RagConfig(min_evidence=1, min_evidence_score=0.2, max_evidence=4),
    )
    state = RagState(
        workspace_id=WORKSPACE,
        query=query,
        top_k=6,
        trace=RagTrace(workspace_id=WORKSPACE, detected_language="unknown", top_k=6),
    )
    import asyncio

    terminal = asyncio.run(graph.run(state))
    return terminal.outcome, terminal


def test_answerable_queries_ground_above_threshold() -> None:
    answerable = [case for case in CASES if case.answerable]
    grounded = 0
    for case in answerable:
        outcome, terminal = _run(case.query)
        if outcome in {AnswerOutcome.ANSWERED, AnswerOutcome.PARTIAL}:
            grounded += 1
            # Every citation must quote its cited chunk verbatim.
            by_id = {doc.chunk_id: doc.text for doc in CORPUS}
            for claim in terminal.claims:
                source = normalize_for_match(by_id[claim.citation.chunk_id])
                assert normalize_for_match(claim.citation.quote) in source
    accuracy = grounded / len(answerable)
    assert accuracy >= 0.8, f"grounding accuracy regressed to {accuracy:.2f}"


def test_out_of_corpus_queries_abstain() -> None:
    unanswerable = [case for case in CASES if not case.answerable]
    abstained = sum(1 for case in unanswerable if _run(case.query)[0] is AnswerOutcome.ABSTAINED)
    rate = abstained / len(unanswerable)
    assert rate == 1.0, f"pipeline answered an out-of-corpus query (rate {rate:.2f})"


def test_multilingual_answerable_queries_each_language_grounds() -> None:
    for language in ("eng", "tam", "tanglish"):
        cases = [c for c in CASES if c.answerable and c.language == language]
        grounded = sum(
            1 for c in cases if _run(c.query)[0] in {AnswerOutcome.ANSWERED, AnswerOutcome.PARTIAL}
        )
        assert grounded >= 1, f"no {language} query grounded"


def test_answers_never_cite_uncited_evidence() -> None:
    """A supported claim's citation always resolves to a real corpus chunk."""
    corpus_ids = {doc.chunk_id for doc in CORPUS}
    for case in CASES:
        _, terminal = _run(case.query)
        for claim in terminal.claims:
            assert claim.citation.chunk_id in corpus_ids
