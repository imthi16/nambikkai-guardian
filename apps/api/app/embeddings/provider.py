"""A local, deterministic BGE-M3-compatible embedding provider.

The production system will load `BAAI/bge-m3` (1024-dim, multilingual) behind
this same interface. Shipping model weights in the MVP is neither desirable
(size, licensing, offline CI) nor necessary to exercise the batching,
persistence, versioning, and retrieval plumbing. This provider therefore
produces *deterministic* 1024-dim unit vectors from a hashed bag-of-features
over Unicode-normalized text.

Properties that make it a faithful stand-in:
- Same dimensionality as BGE-M3 (1024), so schema and indexes are real.
- Deterministic per (text, model_version): identical inputs map to identical
  vectors, so persisted provenance is meaningful.
- Multilingual-agnostic: it normalizes with the shared `app.language`
  pipeline, so Tamil, English, and Tanglish are all embeddable.
- Unit-normalized, so cosine and inner-product distance behave sensibly.

It is a hashing embedder, not a semantic model: use it for wiring and tests,
not for measuring real retrieval quality.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from app.embeddings.types import EmbeddingResult, EmbeddingVector
from app.language import normalize_for_match

BGE_M3_DIMENSIONS = 1024

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class LocalHashingEmbeddingProvider:
    """Deterministic BGE-M3-shaped embeddings with no external dependencies."""

    def __init__(
        self,
        *,
        dimensions: int = BGE_M3_DIMENSIONS,
        model: str = "bge-m3-local",
        model_version: str = "hashing-v1",
    ) -> None:
        if dimensions < 1:
            msg = "dimensions must be positive"
            raise ValueError(msg)
        self.dimensions = dimensions
        self.model = model
        self.model_version = model_version

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        vectors = tuple(self._embed_one(text) for text in texts)
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )

    def _embed_one(self, text: str) -> EmbeddingVector:
        accumulator = [0.0] * self.dimensions
        tokens = self._features(text)
        for token in tokens:
            index, sign = self._project(token)
            accumulator[index] += sign
        normalized = self._l2_normalize(accumulator)
        return EmbeddingVector(
            values=tuple(normalized),
            model=self.model,
            model_version=self.model_version,
            dimensions=self.dimensions,
        )

    def _features(self, text: str) -> list[str]:
        """Unigrams plus character trigrams over normalized text.

        Character trigrams give partial robustness to Tamil sandhi and Tanglish
        spelling variation without any language-specific tables.
        """
        normalized = normalize_for_match(text)
        tokens = _TOKEN.findall(normalized)
        features = list(tokens)
        for token in tokens:
            padded = f"#{token}#"
            features.extend(padded[i : i + 3] for i in range(len(padded) - 2))
        return features

    def _project(self, token: str) -> tuple[int, float]:
        """Hash a feature to a dimension and a sign (the hashing trick).

        Salting with `model_version` means bumping the version deterministically
        changes the vector space, which is exactly the provenance behavior a
        real model upgrade would have.
        """
        digest = hashlib.blake2b(
            token.encode("utf-8"),
            digest_size=8,
            salt=self.model_version.encode("utf-8")[:16],
        ).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % self.dimensions
        sign = 1.0 if (raw >> 63) & 1 else -1.0
        return index, sign

    @staticmethod
    def _l2_normalize(values: list[float]) -> list[float]:
        magnitude = math.sqrt(sum(component * component for component in values))
        if magnitude == 0.0:
            # An empty/stopword-only input has no direction; a stable non-zero
            # unit vector keeps distance math well-defined.
            unit = [0.0] * len(values)
            unit[0] = 1.0
            return unit
        return [component / magnitude for component in values]
