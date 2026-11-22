"""Text embeddings behind a provider interface (local fastembed model today)."""

import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from fastembed import TextEmbedding

from ..core.settings import get_settings


class Embedder(Protocol):
    def warm(self) -> None:
        """Load the model now, so that no caller waits for the download or the load later."""
        ...

    def embed(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Runs a small ONNX model locally. The model is loaded once: by warm() at startup, or on first use."""

    def __init__(self, model_name: str, cache_dir: str) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()  # startup warms in a worker thread while a request may already embed

    def warm(self) -> None:
        with self._lock:
            if self._model is None:
                Path(self._cache_dir).mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir)

    def embed(self, text: str) -> list[float]:
        self.warm()
        assert self._model is not None  # warm() sets it or raises
        vector: list[float] = next(iter(self._model.embed([text]))).tolist()
        return vector


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Process-wide embedder, selected from settings."""
    settings = get_settings()
    return FastEmbedEmbedder(settings.embedding_model, settings.fastembed_cache_dir)
