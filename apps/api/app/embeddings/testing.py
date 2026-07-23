"""Safe, in-memory embedding providers for tests and offline development.

`StaticEmbeddingProvider` returns pre-scripted vectors; `FailingEmbeddingProvider`
fails a configurable number of times before succeeding, to exercise retry
behavior. Neither performs I/O, so they are safe to use anywhere without
credentials.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from app.embeddings.types import (
    EmbeddingError,
    EmbeddingResult,
    EmbeddingVector,
)


class StaticEmbeddingProvider:
    """Returns fixed vectors; deterministic and dependency-free.

    Each input maps to a stable one-hot vector chosen by hashing its text, so
    results depend only on content (never on batch position). This makes it
    usable for asserting that batching preserves input order.
    """

    def __init__(
        self,
        *,
        dimensions: int = 4,
        model: str = "static-test",
        model_version: str = "v1",
        fill: float | None = None,
    ) -> None:
        self.dimensions = dimensions
        self.model = model
        self.model_version = model_version
        self._fill = fill
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls.append(tuple(texts))
        vectors = tuple(self._vector(text) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )

    def _vector(self, text: str) -> EmbeddingVector:
        if self._fill is not None:
            values = tuple(self._fill for _ in range(self.dimensions))
        else:
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
            position = int.from_bytes(digest, "big") % self.dimensions
            values = tuple(1.0 if index == position else 0.0 for index in range(self.dimensions))
        return EmbeddingVector(
            values=values,
            model=self.model,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )


class FailingEmbeddingProvider:
    """Raises `EmbeddingError` for the first `failures` calls, then delegates."""

    def __init__(
        self,
        inner: StaticEmbeddingProvider | None = None,
        *,
        failures: int = 1,
    ) -> None:
        self._inner = inner or StaticEmbeddingProvider()
        self._remaining = failures
        self.attempts = 0
        self.model = self._inner.model
        self.model_version = self._inner.model_version
        self.dimensions = self._inner.dimensions

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            msg = "transient provider failure"
            raise EmbeddingError(msg)
        return self._inner.embed(texts)
