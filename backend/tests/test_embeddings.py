"""The embedding model: where it is cached, when it is loaded, and what startup does when loading fails.

fastembed is replaced by a fake, so no model is downloaded.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from linkedin_agent import main as main_module
from linkedin_agent.core.settings import Settings
from linkedin_agent.infra import embeddings


def _settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=Fernet.generate_key().decode(),
        **overrides,
    )


class _Vector:
    def tolist(self) -> list[float]:
        return [0.5, 0.25]


def test_default_cache_is_a_relative_project_data_path() -> None:
    default = Settings.model_fields["fastembed_cache_dir"].default
    assert default == "data/models/fastembed"  # under the project data directory, like private_data_dir
    assert not Path(default).is_absolute()


def test_cache_dir_is_read_from_fastembed_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FASTEMBED_CACHE_DIR", str(tmp_path / "models"))
    assert _settings().fastembed_cache_dir == str(tmp_path / "models")


def test_warm_loads_the_model_once_into_the_configured_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created: list[tuple[str, str | None]] = []

    class FakeTextEmbedding:
        def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
            created.append((model_name, cache_dir))

    monkeypatch.setattr(embeddings, "TextEmbedding", FakeTextEmbedding)
    cache = tmp_path / "models" / "fastembed"
    embedder = embeddings.FastEmbedEmbedder("model-x", str(cache))
    embedder.warm()
    embedder.warm()

    assert created == [("model-x", str(cache))]
    assert cache.is_dir()


def test_embed_uses_the_model_loaded_by_warm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeTextEmbedding:
        def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
            self.calls = 0

        def embed(self, texts: list[str]) -> Any:
            self.calls += 1
            return iter([_Vector() for _ in texts])

    monkeypatch.setattr(embeddings, "TextEmbedding", FakeTextEmbedding)
    embedder = embeddings.FastEmbedEmbedder("model-x", str(tmp_path / "cache"))
    embedder.warm()
    assert embedder.embed("hello") == [0.5, 0.25]


def test_startup_warms_the_embedding_model(monkeypatch: pytest.MonkeyPatch) -> None:
    warmed: list[bool] = []

    class Embedder:
        def warm(self) -> None:
            warmed.append(True)

    monkeypatch.setattr(main_module, "get_embedder", lambda: Embedder())
    asyncio.run(main_module.warm_embedding_model(_settings(embedding_warm_on_startup=True)))
    assert warmed == [True]


def test_warm_on_startup_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_be_called() -> None:
        raise AssertionError("the embedder must not be touched when warm-up is off")

    monkeypatch.setattr(main_module, "get_embedder", must_not_be_called)
    asyncio.run(main_module.warm_embedding_model(_settings(embedding_warm_on_startup=False)))


def test_a_failed_startup_load_is_logged_and_does_not_stop_the_api(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def warm(self) -> None:
            raise OSError("cannot reach the model host")

    monkeypatch.setattr(main_module, "get_embedder", lambda: Broken())
    with caplog.at_level(logging.ERROR, logger="linkedin_agent.main"):
        asyncio.run(main_module.warm_embedding_model(_settings(embedding_warm_on_startup=True)))
    assert "embedding model not loaded at startup error_type=OSError" in caplog.text
