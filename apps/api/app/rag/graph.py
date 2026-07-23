"""The typed LangGraph workflow that turns a query into a grounded answer.

The graph wires the pipeline stages into an explicit state machine with two
hard gates that cannot be bypassed:

* **authorization gate** — the first node sets ``authorized``; every retrieval
  path is only reachable when it is true, otherwise the graph routes straight
  to abstention. Retrieval itself goes through a workspace-scoped port, so even
  the authorized path can only ever see this tenant's evidence.
* **evidence-sufficiency gate** — after retrieval, the graph abstains unless
  enough sufficiently-scored evidence was found, so generation never runs on
  empty or thin evidence.

Nodes are small and pure-ish: each reads the validated :class:`RagState` and
returns a partial update that LangGraph merges back in. The retriever is
injected behind :class:`EvidenceRetriever` so the graph runs in tests without a
database or an LLM. Evidence text is untrusted data end to end.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.decision import ConfidencePolicy, DecisionOutcome, DecisionSignals, get_default_policy
from app.language import detect_language
from app.rag.config import RagConfig
from app.rag.generation import AnswerGenerator, get_default_generator
from app.rag.state import RagState
from app.rag.types import AnswerOutcome, ClaimVerdict, EvidencePassage
from app.rag.verification import ClaimVerifier


@runtime_checkable
class EvidenceRetriever(Protocol):
    """Returns workspace-authorized evidence for a query.

    Implementations must enforce workspace and document authorization in the
    data layer and return only passages this tenant may read. The returned
    ``dict`` is non-sensitive retrieval telemetry (counts, timings) merged into
    the trace; it must not contain evidence text or the raw query.
    """

    async def retrieve(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None,
        language: str | None,
    ) -> tuple[Sequence[EvidencePassage], dict[str, object]]: ...


class RagGraph:
    """Builds and runs the compiled grounded-answer graph."""

    def __init__(
        self,
        retriever: EvidenceRetriever,
        *,
        generator: AnswerGenerator | None = None,
        verifier: ClaimVerifier | None = None,
        policy: ConfidencePolicy | None = None,
        config: RagConfig | None = None,
    ) -> None:
        self._retriever = retriever
        self._generator = generator or get_default_generator()
        self._verifier = verifier or ClaimVerifier()
        self._policy = policy or get_default_policy()
        self._config = config or RagConfig()
        self._compiled = self._build()

    async def run(self, state: RagState) -> RagState:
        """Execute the graph and return the validated terminal state."""
        result = await self._compiled.ainvoke(state)
        # LangGraph returns the merged state as a dict-like mapping; re-validate
        # it back into the typed model so callers always get a RagState.
        return RagState.model_validate(dict(result))

    # --- graph construction -------------------------------------------------

    def _build(self) -> CompiledStateGraph[RagState]:
        graph: StateGraph[RagState] = StateGraph(RagState)
        graph.add_node("authorize", self._authorize)
        graph.add_node("analyze", self._analyze)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("generate", self._generate)
        graph.add_node("verify", self._verify)
        graph.add_node("decide", self._decide)
        graph.add_node("compose", self._compose)
        graph.add_node("abstain", self._abstain)

        graph.add_edge(START, "authorize")
        # Authorization gate: only an authorized request may proceed.
        graph.add_conditional_edges(
            "authorize",
            self._route_authorized,
            {"analyze": "analyze", "abstain": "abstain"},
        )
        graph.add_edge("analyze", "retrieve")
        # Evidence-sufficiency gate: abstain unless enough evidence was found.
        graph.add_conditional_edges(
            "retrieve",
            self._route_sufficient,
            {"generate": "generate", "abstain": "abstain"},
        )
        graph.add_edge("generate", "verify")
        graph.add_edge("verify", "decide")
        # Decision gate: the calibrated policy chooses whether to surface an
        # answer at all; only its answering outcomes reach composition.
        graph.add_conditional_edges(
            "decide",
            self._route_decision,
            {"compose": "compose", "abstain": "abstain"},
        )
        graph.add_edge("compose", END)
        graph.add_edge("abstain", END)
        return graph.compile()

    # --- routers ------------------------------------------------------------

    @staticmethod
    def _route_authorized(state: RagState) -> str:
        return "analyze" if state.authorized else "abstain"

    @staticmethod
    def _route_sufficient(state: RagState) -> str:
        return "generate" if state.sufficient else "abstain"

    @staticmethod
    def _route_decision(state: RagState) -> str:
        return "compose" if DecisionOutcome(state.decision).is_answering else "abstain"

    # --- nodes --------------------------------------------------------------

    @staticmethod
    async def _authorize(state: RagState) -> dict[str, object]:
        """Confirm the request carries a workspace scope before any data access.

        Membership and role are already proven by the route dependency and the
        workspace is bound for row-level security; this node makes that
        precondition an explicit, inspectable gate in the graph itself.
        """
        return {"authorized": state.workspace_id is not None}

    @staticmethod
    async def _analyze(state: RagState) -> dict[str, object]:
        """Detect the query language, preserving the user's exact query text."""
        detection = detect_language(state.query)
        state.trace.detected_language = detection.language.value
        return {"detected_language": detection.language.value, "trace": state.trace}

    async def _retrieve(self, state: RagState) -> dict[str, object]:
        """Fetch authorized evidence and apply the sufficiency gate."""
        start = time.perf_counter()
        evidence, retrieval_meta = await self._retriever.retrieve(
            workspace_id=state.workspace_id,
            query=state.query,
            top_k=state.top_k,
            document_id=state.document_id,
            language=state.language_filter,
        )
        elapsed = (time.perf_counter() - start) * 1000

        # Keep only the strongest evidence, bounded, to minimize model input.
        kept = tuple(
            passage
            for passage in evidence
            if passage.fused_score >= self._config.min_evidence_score
        )[: self._config.max_evidence]
        sufficient = len(kept) >= self._config.min_evidence

        trace = state.trace
        trace.retrieval_ms = elapsed
        trace.retrieved_count = len(evidence)
        trace.evidence_count = len(kept)
        trace.sufficient = sufficient
        trace.retrieval = retrieval_meta
        return {
            "evidence": kept,
            "retrieved_count": len(evidence),
            "sufficient": sufficient,
            "trace": trace,
        }

    async def _generate(self, state: RagState) -> dict[str, object]:
        """Propose candidate claims from the minimal evidence set."""
        start = time.perf_counter()
        candidates = tuple(self._generator.generate(state.query, state.evidence))
        state.trace.generation_ms = (time.perf_counter() - start) * 1000
        state.trace.generator = self._generator.model
        state.trace.draft_claim_count = len(candidates)
        return {"candidates": candidates, "trace": state.trace}

    async def _verify(self, state: RagState) -> dict[str, object]:
        """Verify each candidate against its cited evidence; drop unsupported."""
        start = time.perf_counter()
        outcome = self._verifier.verify_verbose(state.query, state.candidates, state.evidence)
        claims = tuple(outcome.claims)
        counts = outcome.counts
        trace = state.trace
        trace.verification_ms = (time.perf_counter() - start) * 1000
        trace.verifier = self._verifier.verifier
        trace.supported_claim_count = len(claims)
        trace.partial_claim_count = counts.get(ClaimVerdict.AMBIGUOUS, 0)
        trace.contradicted_claim_count = counts.get(ClaimVerdict.CONTRADICTED, 0)
        trace.unsupported_claim_count = counts.get(ClaimVerdict.UNSUPPORTED, 0)
        trace.dropped_claim_count = len(state.candidates) - len(claims)
        return {"claims": claims, "trace": trace}

    async def _decide(self, state: RagState) -> dict[str, object]:
        """Calibrate the operational decision from objective signals.

        Aggregates the verifier's per-verdict counts, the mean supported-claim
        confidence, evidence coverage, and the least reliable cited OCR source,
        then asks the policy for one of five outcomes. A model's self-reported
        confidence is never consulted.
        """
        claims = state.claims
        trace = state.trace
        confidence = sum(claim.confidence for claim in claims) / len(claims) if claims else None
        # Recorded OCR confidences among cited evidence, and whether any cited
        # chunk is OCR-derived with *no* recorded confidence (unknown quality,
        # which must not be mistaken for born-digital reliability).
        ocr_values = [
            claim.citation.ocr_confidence
            for claim in claims
            if claim.citation.ocr_engine and claim.citation.ocr_confidence is not None
        ]
        ocr_unknown = any(
            claim.citation.ocr_engine and claim.citation.ocr_confidence is None for claim in claims
        )
        signals = DecisionSignals(
            supported_claims=len(claims),
            partial_claims=trace.partial_claim_count,
            contradicted_claims=trace.contradicted_claim_count,
            unsupported_claims=trace.unsupported_claim_count,
            evidence_count=trace.evidence_count,
            retrieved_count=trace.retrieved_count,
            dropped_claims=trace.dropped_claim_count,
            verifier_confidence=confidence,
            min_ocr_confidence=min(ocr_values) if ocr_values else None,
            ocr_unknown_reliability=ocr_unknown,
        )
        result = self._policy.decide(signals)
        trace.decision = result.outcome.value
        trace.decision_reason = result.reason
        return {
            "decision": result.outcome.value,
            "decision_reason": result.reason,
            "trace": trace,
        }

    async def _compose(self, state: RagState) -> dict[str, object]:
        """Assemble the answer from supported claims only.

        Confidence is the mean of the supported claims' calibrated confidences.
        The outcome is ``PARTIAL`` when some candidates were dropped, signalling
        the answer covers only part of what was proposed.
        """
        claims = state.claims
        confidence = sum(claim.confidence for claim in claims) / len(claims)
        answer_text = "\n".join(f"- {claim.text}" for claim in claims)
        outcome = (
            AnswerOutcome.PARTIAL if state.trace.dropped_claim_count > 0 else AnswerOutcome.ANSWERED
        )

        trace = state.trace
        trace.outcome = outcome.value
        trace.confidence = confidence
        trace.abstained = False
        trace.abstention_reason = None
        return {
            "outcome": outcome,
            "answer_text": answer_text,
            "confidence": round(confidence, 6),
            "abstention_reason": None,
            "trace": trace,
        }

    async def _abstain(self, state: RagState) -> dict[str, object]:
        """Return the fixed refusal, clearing any claims and recording *why*.

        Claims are cleared here even if verification populated them: when the
        decision gate withholds an answer (e.g. support and contradiction
        coexist), the response must not still expose assertions the gate
        intended to suppress. ``abstention_reason`` stays a stable machine code;
        the human-readable rationale lives in ``decision_reason``, populated on
        the early gates too so it is never empty.
        """
        reason = self._abstention_reason(state)
        decision_reason = state.decision_reason or reason
        trace = state.trace
        trace.outcome = AnswerOutcome.ABSTAINED.value
        trace.abstained = True
        trace.abstention_reason = reason
        trace.decision = state.decision
        trace.decision_reason = decision_reason
        trace.confidence = 0.0
        return {
            "outcome": AnswerOutcome.ABSTAINED,
            "answer_text": self._config.abstention_text,
            "confidence": 0.0,
            "abstention_reason": reason,
            "decision": state.decision,
            "decision_reason": decision_reason,
            "claims": (),
            "trace": trace,
        }

    @staticmethod
    def _abstention_reason(state: RagState) -> str:
        """A stable machine code for *why* the pipeline withheld an answer."""
        if not state.authorized:
            return "unauthorized"
        if not state.sufficient:
            return "insufficient_evidence"
        # Reached the decision gate: the 5-way decision value is itself a stable
        # code (ask_for_clarification / escalate_for_review / abstain).
        return state.decision
