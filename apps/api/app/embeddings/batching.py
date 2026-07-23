"""Batching and retry decorators over any `EmbeddingProvider`.

These are cross-cutting concerns kept out of the providers themselves so every
provider (local or hosted) gets consistent batch sizing and transient-failure
handling. Both wrappers preserve the provider contract, so they compose and
can be swapped freely.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence

from app.embeddings.types import (
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingResult,
    EmbeddingVector,
)

logger = logging.getLogger("app.embeddings")


class BatchingEmbeddingProvider:
    """Splits large inputs into fixed-size batches before delegating.

    The vector order always matches the input order, so callers can zip
    results back onto their chunks without tracking batch boundaries.
    """

    def __init__(self, inner: EmbeddingProvider, *, batch_size: int = 32) -> None:
        if batch_size < 1:
            msg = "batch_size must be positive"
            raise ValueError(msg)
        self._inner = inner
        self._batch_size = batch_size
        self.model = inner.model
        self.model_version = inner.model_version
        self.dimensions = inner.dimensions

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        vectors: list[EmbeddingVector] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            result = self._inner.embed(batch)
            if len(result.vectors) != len(batch):
                msg = "provider returned the wrong number of vectors for a batch"
                raise EmbeddingError(msg)
            vectors.extend(result.vectors)
        return EmbeddingResult(
            vectors=tuple(vectors),
            model=self.model,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )


class RetryingEmbeddingProvider:
    """Retries transient provider failures with bounded backoff.

    Only `EmbeddingError` is retried; programming errors propagate immediately.
    Backoff is injectable so tests stay fast and deterministic.
    """

    def __init__(
        self,
        inner: EmbeddingProvider,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        self._inner = inner
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.model = inner.model
        self.model_version = inner.model_version
        self.dimensions = inner.dimensions

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        last_error: EmbeddingError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.embed(texts)
            except EmbeddingError as error:
                last_error = error
                if attempt == self._max_attempts:
                    break
                # Telemetry carries counts and the model, never the input text.
                logger.warning(
                    "embedding attempt failed",
                    extra={
                        "attempt": attempt,
                        "max_attempts": self._max_attempts,
                        "model": self.model,
                    },
                )
                self._sleep(self._backoff_seconds * attempt)
        # The loop only exits after at least one failure was recorded.
        raise last_error if last_error is not None else EmbeddingError("no attempts made")
