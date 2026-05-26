"""Local sentence-transformers embeddings.

The model (default `all-MiniLM-L6-v2`, 384 dims) is loaded lazily on first call
and cached for the lifetime of the process. Cold start ~3 s; subsequent calls
are millisecond-scale on CPU for personal-scale batches.

Per `feedback-claude-first` memory: this is intentionally local — Synapse must
not spend the user's budget on external embedding APIs unless quality proves
insufficient at scale.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import numpy as np
from loguru import logger

from synapse.config import EMBEDDING_DIMENSION, EMBEDDING_MODEL

_model_lock = threading.Lock()
_model: Any | None = None


def _load_model() -> Any:
    """Load the SentenceTransformer model once, thread-safe."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            # Import here so test environments without sentence-transformers
            # can mock the module without touching torch at collection time.
            from sentence_transformers import SentenceTransformer

            logger.info("loading embedding model {name}", name=EMBEDDING_MODEL)
            _model = SentenceTransformer(EMBEDDING_MODEL)
            logger.info("embedding model loaded (dim={dim})", dim=EMBEDDING_DIMENSION)
    return _model


def reset_model_cache() -> None:
    """Drop the cached model. Test-only."""
    global _model
    _model = None


def embed_text(text: str) -> np.ndarray:
    """Embed a single text into a 1-D float32 vector.

    Args:
        text: Input string. Empty input returns a zero vector (callers should
            avoid embedding empty content but the API tolerates it).

    Returns:
        A `numpy.ndarray` of shape `(EMBEDDING_DIMENSION,)`, dtype float32.
    """
    if not text or not text.strip():
        return np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
    model = _load_model()
    vec = model.encode(text, convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(vec, dtype=np.float32)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Embed a list of texts in a single forward pass.

    Args:
        texts: Inputs. Empty strings yield zero vectors.

    Returns:
        A list of float32 ndarrays of shape `(EMBEDDING_DIMENSION,)`.
    """
    if not texts:
        return []
    # Replace empty strings with a single space so the model produces a real vector.
    safe = [t if t.strip() else " " for t in texts]
    model = _load_model()
    vecs = model.encode(safe, convert_to_numpy=True, show_progress_bar=False, batch_size=32)
    out: list[np.ndarray] = []
    for text, vec in zip(texts, vecs, strict=True):
        if not text.strip():
            out.append(np.zeros(EMBEDDING_DIMENSION, dtype=np.float32))
        else:
            out.append(np.asarray(vec, dtype=np.float32))
    return out


@lru_cache(maxsize=1)
def model_metadata() -> dict[str, Any]:
    """Return descriptive metadata for the loaded model."""
    return {
        "name": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIMENSION,
    }
