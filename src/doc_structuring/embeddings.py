"""Embedding generator using fastembed for CPU-bound ONNX vector embeddings."""

from __future__ import annotations

import logging
from typing import Sequence
import numpy as np

logger = logging.getLogger(__name__)

_MODEL_INSTANCE = None
_DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def get_embedding_model(model_name: str = _DEFAULT_MODEL_NAME):
    """Lazy-initialize fastembed TextEmbedding model instance."""
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is None:
        try:
            from fastembed import TextEmbedding
            logger.info("Initializing fastembed model: %s", model_name)
            _MODEL_INSTANCE = TextEmbedding(model_name=model_name)
        except Exception as exc:
            logger.error("Failed to load fastembed model '%s': %s", model_name, exc)
            raise RuntimeError(f"Could not load fastembed model: {exc}") from exc
    return _MODEL_INSTANCE


def generate_embeddings(
    texts: Sequence[str],
    model_name: str = _DEFAULT_MODEL_NAME,
    batch_size: int = 64,
) -> list[np.ndarray]:
    """Generate L2-normalized float32 numpy embeddings for a list of texts."""
    if not texts:
        return []

    model = get_embedding_model(model_name)
    raw_embeddings = model.embed(texts, batch_size=batch_size)

    vectors: list[np.ndarray] = []
    for vec in raw_embeddings:
        arr = np.array(vec, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm > 1e-12:
            arr = arr / norm
        vectors.append(arr)

    return vectors
