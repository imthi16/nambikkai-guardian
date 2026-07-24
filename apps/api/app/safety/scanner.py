"""Document-level safety scanning: assess chunks and decide quarantine.

The ingestion worker calls :meth:`InjectionScanner.scan_chunks` after chunking
and before a document is marked ready. The scanner assesses each chunk's text
with the :class:`InjectionDetector`, checks bounded windows across adjacent
chunk boundaries, aggregates the verdicts into one document decision, and
returns a non-sensitive report the worker uses to quarantine the document and
emit audit/security telemetry.

A document is quarantined when *any* chunk or boundary window is quarantined: a
single hidden instruction anywhere in a file is enough to poison every answer
that might cite it, so the safe default is to withhold the whole document from
retrieval rather than try to serve its "clean" parts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.safety.detector import InjectionDetector, get_default_detector
from app.safety.types import (
    InjectionAssessment,
    SafetyDecision,
    SafetyScanTrace,
)

_BOUNDARY_WINDOW_CHARS = 160


@dataclass(frozen=True)
class ChunkAssessment:
    """A chunk or adjacent-chunk boundary paired with its assessment."""

    chunk_index: int
    assessment: InjectionAssessment
    next_chunk_index: int | None = None


@dataclass(frozen=True)
class DocumentSafetyReport:
    """The aggregate safety verdict for one document's chunks.

    ``decision`` is the document-level outcome (quarantine if any chunk or
    boundary window is quarantined, else flag if any is flagged, else allow).
    ``flagged`` lists the assessments that triggered; a non-null
    ``next_chunk_index`` identifies an adjacent-boundary match. ``trace`` carries
    only counts and categories for privacy-safe logging.
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
        """Assess chunks and bounded adjacent windows into one document report.

        Chunkers intentionally preserve page provenance and therefore do not
        create chunks spanning pages. Joining only the tail and head of adjacent
        chunks closes that security boundary without constructing or retaining a
        potentially huge document-wide string.
        """
        trace = SafetyScanTrace(chunk_count=len(chunks))
        flagged: list[ChunkAssessment] = []
        categories: dict[str, None] = {}
        decision = SafetyDecision.ALLOW

        def record(
            chunk_index: int,
            assessment: InjectionAssessment,
            *,
            next_chunk_index: int | None = None,
        ) -> None:
            nonlocal decision
            trace.max_score = max(trace.max_score, assessment.score)
            if assessment.decision is SafetyDecision.ALLOW:
                return
            flagged.append(
                ChunkAssessment(
                    chunk_index=chunk_index,
                    assessment=assessment,
                    next_chunk_index=next_chunk_index,
                )
            )
            for category in assessment.categories:
                categories.setdefault(category.value, None)
            if assessment.decision is SafetyDecision.QUARANTINE:
                trace.quarantined_count += 1
                decision = SafetyDecision.QUARANTINE
            else:
                trace.flagged_count += 1
                if decision is SafetyDecision.ALLOW:
                    decision = SafetyDecision.FLAG

        individual = [
            (chunk_index, content, self._detector.assess(content))
            for chunk_index, content in chunks
        ]
        for chunk_index, _content, assessment in individual:
            record(chunk_index, assessment)

        for left_item, right_item in zip(individual, individual[1:], strict=False):
            left_index, left, left_assessment = left_item
            right_index, right, right_assessment = right_item
            if (
                left_assessment.decision is SafetyDecision.QUARANTINE
                or right_assessment.decision is SafetyDecision.QUARANTINE
            ):
                continue
            left_tail = left[-_BOUNDARY_WINDOW_CHARS:].rstrip()
            right_head = right[:_BOUNDARY_WINDOW_CHARS].lstrip()
            if not left_tail or not right_head:
                continue
            boundary_views = [f"{left_tail} {right_head}"]
            if left_tail[-1].isalnum() and right_head[0].isalnum():
                # Also reconstruct a token split by an attacker-controlled page
                # break ("instru" / "ctions"). The spaced view remains necessary
                # for ordinary boundaries between complete words.
                boundary_views.append(f"{left_tail}{right_head}")
            assessment = max(
                (self._detector.assess(view) for view in boundary_views),
                key=lambda item: item.score,
            )
            # The detector offsets refer to a synthetic joined view, not either
            # original chunk. Preserve the rule/category evidence but make the
            # offsets explicitly non-highlightable rather than exposing false
            # provenance to a reviewer UI.
            assessment = replace(
                assessment,
                signals=tuple(
                    replace(
                        signal,
                        rule=f"{signal.rule}:boundary",
                        start=0,
                        end=0,
                    )
                    for signal in assessment.signals
                ),
            )
            record(
                left_index,
                assessment,
                next_chunk_index=right_index,
            )

        trace.categories = list(categories)
        return DocumentSafetyReport(
            decision=decision,
            trace=trace,
            flagged=tuple(flagged),
        )
