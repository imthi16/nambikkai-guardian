"""Tunable configuration for the grounded-answer pipeline."""

from __future__ import annotations

from dataclasses import dataclass

# The fixed refusal surfaced whenever the pipeline cannot ground an answer. It
# is deliberately generic and identical across abstention reasons so a caller
# cannot infer whether a workspace holds matching-but-insufficient evidence.
DEFAULT_ABSTENTION_TEXT = (
    "I don't have enough evidence in this workspace to answer that. "
    "Try rephrasing, narrowing to a document, or uploading a source."
)


@dataclass(frozen=True)
class RagConfig:
    """Limits and thresholds for one grounded-answer run.

    ``min_evidence`` and ``min_evidence_score`` define the evidence-sufficiency
    gate: if fewer than ``min_evidence`` passages clear ``min_evidence_score``
    the pipeline abstains before generation, so a thin or irrelevant retrieval
    never produces an answer. ``max_evidence`` bounds how much evidence reaches
    generation, keeping the model's input minimal.
    """

    top_k: int = 8
    max_evidence: int = 6
    min_evidence: int = 1
    min_evidence_score: float = 0.0
    abstention_text: str = DEFAULT_ABSTENTION_TEXT
