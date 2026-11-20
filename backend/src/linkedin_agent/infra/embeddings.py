"""Text embeddings behind a provider interface (local fastembed model today)."""

from functools import lru_cache
from typing import Protocol

from fastembed import TextEmbedding

from ..core.settings import get_settings


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Runs a small ONNX model locally. The model is loaded on first use and reused."""

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: TextEmbedding | None = None

    def embed(self, text: str) -> list[float]:
        if self._model is None:
            self._model = TextEmbedding(model_name=self._model_name)
        vector: list[float] = next(iter(self._model.embed([text]))).tolist()
        return vector


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Process-wide embedder, selected from settings."""
    return FastEmbedEmbedder(get_settings().embedding_model)
