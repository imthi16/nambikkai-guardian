"""Application service that assembles and drives the embedding provider.

`build_embedding_provider` wires the configured base provider inside the
batching and retry decorators, so callers get one object honoring the
`EmbeddingProvider` contract with batch sizing, retries, and validated
dimensions. `EmbeddingService` offers the two call shapes the pipeline needs:
embedding many chunk texts and embedding a single query.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config import Settings, get_settings
from app.embeddings.batching import BatchingEmbeddingProvider, RetryingEmbeddingProvider
from app.embeddings.provider import LocalHashingEmbeddingProvider
from app.embeddings.types import (
    DimensionMismatchError,
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingVector,
)


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Construct the configured provider wrapped for batching and retries."""
    resolved = settings or get_settings()
    base: EmbeddingProvider = LocalHashingEmbeddingProvider(
        dimensions=resolved.embedding_dimensions,
        model=resolved.embedding_model,
        model_version=resolved.embedding_model_version,
    )
    retrying = RetryingEmbeddingProvider(
        base,
        max_attempts=resolved.embedding_max_attempts,
        backoff_seconds=resolved.embedding_backoff_seconds,
    )
    return BatchingEmbeddingProvider(retrying, batch_size=resolved.embedding_batch_size)


class EmbeddingService:
    """Coordinates a provider and validates its outputs before use."""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider or build_embedding_provider()

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def model_version(self) -> str:
        return self._provider.model_version

    @property
    def dimensions(self) -> int:
        return self._provider.dimensions

    def embed_texts(self, texts: Sequence[str]) -> EmbeddingResult:
        result = self._provider.embed(texts)
        self._validate(result, expected_count=len(texts))
        return result

    def embed_query(self, text: str) -> EmbeddingVector:
        result = self.embed_texts([text])
        return result.vectors[0]

    def _validate(self, result: EmbeddingResult, *, expected_count: int) -> None:
        if len(result.vectors) != expected_count:
            raise DimensionMismatchError(expected_count, len(result.vectors))
        for vector in result.vectors:
            if vector.dimensions != self.dimensions or len(vector.values) != self.dimensions:
                raise DimensionMismatchError(self.dimensions, len(vector.values))
