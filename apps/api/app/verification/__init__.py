"""Atomic claim extraction and claim-to-evidence entailment verification."""

from app.verification.entailment import (
    EntailmentAnalyzer,
    LexicalEntailmentAnalyzer,
    get_default_analyzer,
)
from app.verification.extraction import (
    AtomicClaimExtractor,
    SentenceClaimExtractor,
    get_default_extractor,
)
from app.verification.types import EntailmentResult, EntailmentVerdict

__all__ = [
    "AtomicClaimExtractor",
    "EntailmentAnalyzer",
    "EntailmentResult",
    "EntailmentVerdict",
    "LexicalEntailmentAnalyzer",
    "SentenceClaimExtractor",
    "get_default_analyzer",
    "get_default_extractor",
]
