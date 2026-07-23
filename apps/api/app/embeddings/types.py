"""Embedding-provider interface and shared types.

Providers are kept behind the `EmbeddingProvider` protocol so the local
BGE-M3-compatible provider used by the MVP can be replaced by a hosted model
without touching persistence or orchestration. Every provider declares its
`model` name and `dimensions`, and returns a typed `EmbeddingResult` whose
vectors are validated deterministically before they are ever persisted.

Vectors are plain `list[float]` at the boundary: no numpy or provider objects
leak into the domain, which keeps the contract trivial to fake in tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class EmbeddingError(Exception):
    """A provider failed to produce a valid embedding."""


class DimensionMismatchError(EmbeddingError):
    """A returned vector did not match the provider's declared dimensions."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"expected {expected}-dim vector, got {actual}")
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True)
class EmbeddingVector:
    """One embedding with the exact provenance needed to reproduce it."""

    values: tuple[float, ...]
    model: str
    model_version: str
    dimensions: int

    def __post_init__(self) -> None:
        if len(self.values) != self.dimensions:
            raise DimensionMismatchError(self.dimensions, len(self.values))


@dataclass(frozen=True)
class EmbeddingResult:
    """The vectors for one batch of inputs, in the same order as the inputs."""

    vectors: tuple[EmbeddingVector, ...]
    model: str
    model_version: str
    dimensions: int


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into fixed-dimension vectors.

    Implementations must be deterministic for a given input and model version
    so that persisted provenance (`model`, `model_version`) is meaningful and
    retrieval is reproducible.
    """

    model: str
    model_version: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> EmbeddingResult: ...
