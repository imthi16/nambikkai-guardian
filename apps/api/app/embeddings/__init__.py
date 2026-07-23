"""Multilingual embeddings: provider interface, local provider, and service."""

from app.embeddings.batching import BatchingEmbeddingProvider, RetryingEmbeddingProvider
from app.embeddings.provider import BGE_M3_DIMENSIONS, LocalHashingEmbeddingProvider
from app.embeddings.service import EmbeddingService, build_embedding_provider
from app.embeddings.types import (
    DimensionMismatchError,
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingVector,
)

__all__ = [
    "BGE_M3_DIMENSIONS",
    "BatchingEmbeddingProvider",
    "DimensionMismatchError",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingService",
    "EmbeddingVector",
    "LocalHashingEmbeddingProvider",
    "RetryingEmbeddingProvider",
    "build_embedding_provider",
]
