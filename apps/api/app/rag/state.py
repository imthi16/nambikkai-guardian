"""The deterministic, validated state carried through the LangGraph workflow.

LangGraph merges each node's returned partial dict into this model, so the
graph's state is a single typed object rather than an untyped dict. Keeping it a
Pydantic model means every transition is validated and the fields a node may
read or write are explicit.

The state also encodes the pipeline's safety invariants as data:

* ``authorized`` must be set by the authorization node before any retrieval
  node runs; the graph routes to abstention if it is ever false.
* ``evidence`` only ever holds passages the retrieval layer already authorized
  and scoped to the workspace; nodes may narrow it but never widen it.
* the raw ``query`` and evidence text stay in state for processing but are
  excluded from :meth:`RagTrace` telemetry, which carries only counts.

Evidence content is untrusted data; no node treats it as an instruction.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from app.rag.generation import CandidateClaim
from app.rag.types import (
    AnswerOutcome,
    AtomicClaim,
    EvidencePassage,
    GroundedAnswer,
    RagTrace,
)


class RagState(BaseModel):
    """The single validated state object threaded through the answer graph."""

    # Arbitrary types (frozen dataclasses, UUIDs) are stored as-is; the model
    # is a typed container, not a serialization boundary.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # --- inputs (set once, never mutated by nodes) ---
    workspace_id: uuid.UUID
    query: str
    top_k: int
    document_id: uuid.UUID | None = None
    language_filter: str | None = None

    # --- authorization gate ---
    authorized: bool = False

    # --- query analysis ---
    detected_language: str = "unknown"

    # --- retrieval ---
    evidence: tuple[EvidencePassage, ...] = ()
    retrieved_count: int = 0
    sufficient: bool = False

    # --- generation and verification ---
    candidates: tuple[CandidateClaim, ...] = ()
    claims: tuple[AtomicClaim, ...] = ()

    # --- outcome ---
    outcome: AnswerOutcome = AnswerOutcome.ABSTAINED
    answer_text: str = ""
    confidence: float = 0.0
    abstention_reason: str | None = None
    # The calibrated 5-way operational decision and its rationale.
    decision: str = "abstain"
    decision_reason: str = ""

    # --- telemetry (never carries query or evidence text) ---
    trace: RagTrace

    def to_answer(self) -> GroundedAnswer:
        """Project the terminal state into the immutable public answer."""
        return GroundedAnswer(
            outcome=self.outcome,
            text=self.answer_text,
            claims=self.claims,
            confidence=self.confidence,
            abstention_reason=self.abstention_reason,
            decision=self.decision,
            decision_reason=self.decision_reason,
        )


# Field used by the graph's conditional edges to branch to abstention.
GATE_FIELD = "authorized"
SUFFICIENCY_FIELD = "sufficient"
