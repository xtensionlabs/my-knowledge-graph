"""Embeddings tests — load the real model.

These tests touch sentence-transformers. The model loads once per pytest session
(internal singleton); cold start is ~3-5s and subsequent calls are millisecond-scale.
"""

from __future__ import annotations

import numpy as np
import pytest

from synapse.config import EMBEDDING_DIMENSION
from synapse.graph.embeddings import embed_batch, embed_text, reset_model_cache


@pytest.fixture(scope="module", autouse=True)
def _warm_model():  # type: ignore[no-untyped-def]
    """Warm the model once for the module."""
    embed_text("warmup")
    yield


def test_embed_text_returns_expected_shape() -> None:
    vec = embed_text("graph theory is great")
    assert isinstance(vec, np.ndarray)
    assert vec.shape == (EMBEDDING_DIMENSION,)
    assert vec.dtype == np.float32


def test_embed_text_is_deterministic() -> None:
    v1 = embed_text("the same input")
    v2 = embed_text("the same input")
    assert np.array_equal(v1, v2)


def test_embed_text_handles_empty() -> None:
    vec = embed_text("")
    assert vec.shape == (EMBEDDING_DIMENSION,)
    assert np.allclose(vec, 0.0)


def test_embed_batch_matches_sequential_calls() -> None:
    texts = ["alpha beta gamma", "delta epsilon zeta"]
    batch = embed_batch(texts)
    sequential = [embed_text(t) for t in texts]
    assert len(batch) == 2
    for b, s in zip(batch, sequential, strict=True):
        # Tolerate tiny numeric drift from batching.
        assert np.allclose(b, s, atol=1e-5)


def test_embed_batch_zero_vector_for_empty_input() -> None:
    batch = embed_batch(["hello", "", "world"])
    assert np.allclose(batch[1], 0.0)
    assert not np.allclose(batch[0], 0.0)
    assert not np.allclose(batch[2], 0.0)
