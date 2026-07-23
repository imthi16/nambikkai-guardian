"""Types for atomic-claim entailment analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EntailmentVerdict(StrEnum):
    """How a claim stands against the evidence span it is checked against.

    ``PARTIAL`` is the "partially supported" outcome: some of the claim's
    content is entailed but not all of it. The RAG pipeline maps it onto the
    persisted ``AMBIGUOUS`` verdict and drops the claim, because a partially
    supported statement is not safe to assert whole.
    """

    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class EntailmentResult:
    """The verdict for one claim plus a human-readable explanation.

    ``coverage`` is the fraction of the claim's content tokens found in the
    aligned evidence sentence, exposed so callers can log or calibrate on it.
    ``explanation`` states *why* the verdict was reached (numeric mismatch,
    negation flip, missing terms, full support) and never contains secrets.
    """

    verdict: EntailmentVerdict
    explanation: str
    coverage: float
