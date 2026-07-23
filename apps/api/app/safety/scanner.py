"""Document-level safety scanning: assess chunks and decide quarantine.

The ingestion worker calls :meth:`InjectionScanner.scan_chunks` after chunking
and before a document is marked ready. The scanner assesses each chunk's text
with the :class:`InjectionDetector`, aggregates the per-chunk verdicts into one
document decision, and returns a non-sensitive report the worker uses to
quarantine the document and emit audit/security telemetry.

A document is quarantined when *any* chunk is quarantined: a single hidden
instruction anywhere in a file is enough to poison every answer that might cite
it, so the safe default is to withhold the whole document from retrieval rather
than try to serve its "clean" parts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.safety.detector import InjectionDetector, get_default_detector
from app.safety.types import (
    InjectionAssessment,
    SafetyDecision,
    SafetyScanTrace,
)


@dataclass(frozen=True)
class ChunkAssessment:
    """One chunk's index paired with its injection assessment."""

    chunk_index: int
    assessment: InjectionAssessment


@dataclass(frozen=True)
class DocumentSafetyReport:
    """The aggregate safety verdict for one document's chunks.

    ``decision`` is the document-level outcome (quarantine if any chunk is
    quarantined, else flag if any is flagged, else allow). ``flagged`` lists the
    chunk assessments that were flagged or quarantined so a reviewer can inspect
    exactly which spans triggered, while ``trace`` carries only counts and
    categories for privacy-safe logging.
    """

    decision: SafetyDecision
    trace: SafetyScanTrace
    flagged: tuple[ChunkAssessment, ...]

    @property
    def is_quarantined(self) -> bool:
        return self.decision.is_blocking

    @property
    def reason(self) -> str:
        """A stable, non-sensitive quarantine reason string for audit logs."""
        categories = ", ".join(self.trace.categories) or "instruction_like_content"
        return f"prompt_injection: {categories}"


class InjectionScanner:
    """Scans a document's chunks and decides whether to quarantine it."""

    def __init__(self, detector: InjectionDetector | None = None) -> None:
        self._detector = detector or get_default_detector()

    def scan_text(self, text: str) -> InjectionAssessment:
        """Assess a single untrusted text (query, chunk, or page)."""
        return self._detector.assess(text)

    def scan_chunks(self, chunks: Sequence[tuple[int, str]]) -> DocumentSafetyReport:
        """Assess ``(chunk_index, content)`` pairs into one document report."""
        trace = SafetyScanTrace(chunk_count=len(chunks))
        flagged: list[ChunkAssessment] = []
        categories: dict[str, None] = {}
        decision = SafetyDecision.ALLOW

        for chunk_index, content in chunks:
            assessment = self._detector.assess(content)
            trace.max_score = max(trace.max_score, assessment.score)
            if assessment.decision is SafetyDecision.ALLOW:
                continue
            flagged.append(ChunkAssessment(chunk_index=chunk_index, assessment=assessment))
            for category in assessment.categories:
                categories.setdefault(category.value, None)
            if assessment.decision is SafetyDecision.QUARANTINE:
                trace.quarantined_count += 1
                decision = SafetyDecision.QUARANTINE
            else:
                trace.flagged_count += 1
                if decision is SafetyDecision.ALLOW:
                    decision = SafetyDecision.FLAG

        trace.categories = list(categories)
        return DocumentSafetyReport(
            decision=decision,
            trace=trace,
            flagged=tuple(flagged),
        )
