"""Grounded RAG answer pipeline: typed LangGraph workflow with hard safety gates.

Public surface: run :class:`RagService.answer` to get a :class:`RagResult`
holding a :class:`GroundedAnswer` (answered, partial, or abstained) plus a
non-sensitive :class:`RagTrace`. Everything else is an internal stage kept
behind a typed interface so providers can be swapped without touching callers.
"""

from app.rag.config import DEFAULT_ABSTENTION_TEXT, RagConfig
from app.rag.generation import (
    AnswerGenerator,
    CandidateClaim,
    ExtractiveGenerator,
    GenerationError,
    get_default_generator,
)
from app.rag.graph import EvidenceRetriever, RagGraph
from app.rag.retriever import HybridEvidenceRetriever
from app.rag.service import RagService
from app.rag.state import RagState
from app.rag.types import (
    AnswerOutcome,
    AtomicClaim,
    Citation,
    ClaimVerdict,
    EvidencePassage,
    GroundedAnswer,
    RagResult,
    RagTrace,
)
from app.rag.verification import ClaimVerifier, VerificationConfig

__all__ = [
    "DEFAULT_ABSTENTION_TEXT",
    "AnswerGenerator",
    "AnswerOutcome",
    "AtomicClaim",
    "CandidateClaim",
    "Citation",
    "ClaimVerdict",
    "ClaimVerifier",
    "EvidencePassage",
    "EvidenceRetriever",
    "ExtractiveGenerator",
    "GenerationError",
    "GroundedAnswer",
    "HybridEvidenceRetriever",
    "RagConfig",
    "RagGraph",
    "RagResult",
    "RagService",
    "RagState",
    "RagTrace",
    "VerificationConfig",
    "get_default_generator",
]
