"""Embedding provider behavior: batching, dimensions, versioning, retries.

These are deterministic and require no credentials or network. Persistence and
tenant-isolation are covered by the integration suite.
"""

from __future__ import annotations

import math

import pytest
from app.embeddings import (
    BGE_M3_DIMENSIONS,
    BatchingEmbeddingProvider,
    DimensionMismatchError,
    EmbeddingError,
    EmbeddingService,
    LocalHashingEmbeddingProvider,
    RetryingEmbeddingProvider,
    build_embedding_provider,
)
from app.embeddings.testing import FailingEmbeddingProvider, StaticEmbeddingProvider
from app.embeddings.types import EmbeddingVector


class TestLocalProvider:
    def test_dimensions_match_bge_m3(self) -> None:
        provider = LocalHashingEmbeddingProvider()
        assert provider.dimensions == BGE_M3_DIMENSIONS == 1024
        result = provider.embed(["hello"])
        assert result.vectors[0].dimensions == 1024
        assert len(result.vectors[0].values) == 1024

    def test_vectors_are_unit_normalized(self) -> None:
        result = LocalHashingEmbeddingProvider().embed(["refund policy details"])
        magnitude = math.sqrt(sum(component**2 for component in result.vectors[0].values))
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    def test_embedding_is_deterministic(self) -> None:
        provider = LocalHashingEmbeddingProvider()
        first = provider.embed(["vanakkam ulagam"]).vectors[0].values
        second = provider.embed(["vanakkam ulagam"]).vectors[0].values
        assert first == second

    def test_multilingual_inputs_all_embed(self) -> None:
        provider = LocalHashingEmbeddingProvider()
        result = provider.embed(["refund policy", "எனது ஆவணம்", "eppadi panna"])
        assert len(result.vectors) == 3
        assert all(v.dimensions == 1024 for v in result.vectors)

    def test_model_version_changes_the_vector_space(self) -> None:
        text = "same input text"
        v1 = LocalHashingEmbeddingProvider(model_version="a").embed([text]).vectors[0]
        v2 = LocalHashingEmbeddingProvider(model_version="b").embed([text]).vectors[0]
        assert v1.values != v2.values
        assert v1.model_version != v2.model_version

    def test_empty_input_yields_defined_unit_vector(self) -> None:
        result = LocalHashingEmbeddingProvider().embed([""])
        magnitude = math.sqrt(sum(component**2 for component in result.vectors[0].values))
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    def test_empty_batch_returns_no_vectors(self) -> None:
        assert LocalHashingEmbeddingProvider().embed([]).vectors == ()

    def test_zero_dimensions_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="dimensions"):
            LocalHashingEmbeddingProvider(dimensions=0)


class TestDimensionValidation:
    def test_vector_rejects_wrong_length(self) -> None:
        with pytest.raises(DimensionMismatchError) as info:
            EmbeddingVector(values=(1.0, 2.0), model="m", model_version="v", dimensions=4)
        assert info.value.expected == 4
        assert info.value.actual == 2

    def test_service_validates_provider_dimensions(self) -> None:
        # Provider claims 1024 but actually returns 4-dim vectors.
        class LyingProvider:
            model = "bge-m3-local"
            model_version = "v1"
            dimensions = 1024

            def embed(self, texts):  # type: ignore[no-untyped-def]
                from app.embeddings.types import EmbeddingResult, EmbeddingVector

                vectors = tuple(
                    EmbeddingVector(
                        values=(0.0, 0.0, 0.0, 1.0),
                        model=self.model,
                        model_version=self.model_version,
                        dimensions=4,
                    )
                    for _ in texts
                )
                return EmbeddingResult(
                    vectors=vectors,
                    model=self.model,
                    model_version=self.model_version,
                    dimensions=4,
                )

        service = EmbeddingService(LyingProvider())
        with pytest.raises(DimensionMismatchError):
            service.embed_texts(["x"])


class TestBatching:
    def test_inputs_are_split_into_batches(self) -> None:
        inner = StaticEmbeddingProvider(dimensions=4)
        provider = BatchingEmbeddingProvider(inner, batch_size=2)
        result = provider.embed(["a", "b", "c", "d", "e"])
        assert len(result.vectors) == 5
        assert [len(call) for call in inner.calls] == [2, 2, 1]

    def test_batching_preserves_input_order(self) -> None:
        inner = StaticEmbeddingProvider(dimensions=64)
        provider = BatchingEmbeddingProvider(inner, batch_size=2)
        texts = ["alpha", "beta", "gamma", "delta", "epsilon"]
        batched = provider.embed(texts)
        # Each batched vector must equal that text's standalone embedding, in
        # the same order as the inputs.
        for index, text in enumerate(texts):
            expected = StaticEmbeddingProvider(dimensions=64).embed([text]).vectors[0]
            assert batched.vectors[index].values == expected.values

    def test_single_batch_when_smaller_than_batch_size(self) -> None:
        inner = StaticEmbeddingProvider(dimensions=4)
        BatchingEmbeddingProvider(inner, batch_size=32).embed(["a", "b"])
        assert len(inner.calls) == 1

    def test_invalid_batch_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            BatchingEmbeddingProvider(StaticEmbeddingProvider(), batch_size=0)

    def test_provider_metadata_is_exposed(self) -> None:
        inner = StaticEmbeddingProvider(dimensions=8, model="m", model_version="v9")
        provider = BatchingEmbeddingProvider(inner, batch_size=4)
        assert provider.model == "m"
        assert provider.model_version == "v9"
        assert provider.dimensions == 8

    def test_wrong_batch_count_from_provider_is_rejected(self) -> None:
        class DropsVectors:
            model = "x"
            model_version = "v"
            dimensions = 4

            def embed(self, texts):  # type: ignore[no-untyped-def]
                from app.embeddings.types import EmbeddingResult

                return EmbeddingResult(
                    vectors=(),  # returns nothing for a non-empty batch
                    model=self.model,
                    model_version=self.model_version,
                    dimensions=self.dimensions,
                )

        provider = BatchingEmbeddingProvider(DropsVectors(), batch_size=2)
        with pytest.raises(EmbeddingError):
            provider.embed(["a", "b"])


class TestRetries:
    def test_transient_failures_are_retried_then_succeed(self) -> None:
        failing = FailingEmbeddingProvider(StaticEmbeddingProvider(dimensions=4), failures=2)
        provider = RetryingEmbeddingProvider(
            failing, max_attempts=3, backoff_seconds=0.0, sleep=lambda _: None
        )
        result = provider.embed(["x"])
        assert failing.attempts == 3
        assert len(result.vectors) == 1

    def test_exhausted_retries_raise(self) -> None:
        failing = FailingEmbeddingProvider(failures=5)
        provider = RetryingEmbeddingProvider(
            failing, max_attempts=3, backoff_seconds=0.0, sleep=lambda _: None
        )
        with pytest.raises(EmbeddingError):
            provider.embed(["x"])
        assert failing.attempts == 3

    def test_backoff_is_applied_between_attempts(self) -> None:
        delays: list[float] = []
        failing = FailingEmbeddingProvider(StaticEmbeddingProvider(dimensions=4), failures=2)
        provider = RetryingEmbeddingProvider(
            failing, max_attempts=3, backoff_seconds=0.5, sleep=delays.append
        )
        provider.embed(["x"])
        assert delays == [0.5, 1.0]

    def test_invalid_max_attempts_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryingEmbeddingProvider(StaticEmbeddingProvider(), max_attempts=0)


class TestService:
    def test_build_provider_from_settings_is_wired_and_batched(self) -> None:
        provider = build_embedding_provider()
        assert provider.dimensions == 1024
        result = provider.embed(["a", "b", "c"])
        assert len(result.vectors) == 3

    def test_embed_query_returns_single_vector(self) -> None:
        service = EmbeddingService(LocalHashingEmbeddingProvider())
        vector = service.embed_query("what is the refund policy")
        assert vector.dimensions == 1024
        assert vector.model == "bge-m3-local"

    def test_provider_replacement_changes_output(self) -> None:
        # The service works with any provider implementing the protocol.
        static = StaticEmbeddingProvider(dimensions=4, model="swappable")
        service = EmbeddingService(static)
        assert service.model == "swappable"
        assert service.dimensions == 4
        assert service.embed_query("x").dimensions == 4

    def test_service_reports_provider_provenance(self) -> None:
        service = EmbeddingService(LocalHashingEmbeddingProvider(model_version="hashing-v1"))
        assert service.model_version == "hashing-v1"

    def test_service_rejects_wrong_vector_count(self) -> None:
        class ExtraVectors:
            model = "static-test"
            model_version = "v1"
            dimensions = 4

            def embed(self, texts):  # type: ignore[no-untyped-def]
                from app.embeddings.types import EmbeddingResult, EmbeddingVector

                one = EmbeddingVector(
                    values=(1.0, 0.0, 0.0, 0.0),
                    model=self.model,
                    model_version=self.model_version,
                    dimensions=4,
                )
                return EmbeddingResult(
                    vectors=(one, one),  # two vectors for one input
                    model=self.model,
                    model_version=self.model_version,
                    dimensions=4,
                )

        service = EmbeddingService(ExtraVectors())
        with pytest.raises(DimensionMismatchError):
            service.embed_texts(["only one"])

    def test_static_double_supports_constant_fill(self) -> None:
        provider = StaticEmbeddingProvider(dimensions=4, fill=0.5)
        vector = provider.embed(["anything"]).vectors[0]
        assert vector.values == (0.5, 0.5, 0.5, 0.5)
