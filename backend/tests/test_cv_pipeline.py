"""CV pipeline end to end, with the LLM replaced by a scripted fake. No network, no LaTeX."""

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from linkedin_agent.core.exceptions import (
    CV_STRUCTURE_MESSAGE,
    CVCompilationError,
    CVStructureError,
    LLMOutputError,
)
from linkedin_agent.core.settings import Settings
from linkedin_agent.cv import compile as compile_module
from linkedin_agent.cv.compile import compile_latex, find_tectonic
from linkedin_agent.cv.pipeline import load_master_cv, tailor_cv
from linkedin_agent.infra import llm as llm_module

SAMPLE_MASTER = Path(__file__).resolve().parents[1] / "samples" / "sample_master_cv.json"


class FakeLLM:
    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **options: object) -> str:
        self.prompts.append(prompt)
        return self._answer


def _use_fake_llm(monkeypatch: pytest.MonkeyPatch, answer: str) -> FakeLLM:
    fake = FakeLLM(answer)
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: fake)
    return fake


def test_tailoring_returns_grounded_result(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = json.dumps(
        {"lines": [{"section": "experience", "text": "Built a FastAPI service", "source_ids": ["exp-1"]}]}
    )
    fake = _use_fake_llm(monkeypatch, answer)
    result = asyncio.run(tailor_cv("ML Intern", "Python and FastAPI", load_master_cv(SAMPLE_MASTER)))
    assert result.grounded
    assert "Python and FastAPI" in fake.prompts[0]
    assert "[exp-1]" in fake.prompts[0]  # the model sees the fact ids it must cite


def test_tailoring_reports_ungrounded_lines_instead_of_hiding_them(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = json.dumps({"lines": [{"section": "experience", "text": "Led a team of 40", "source_ids": ["exp-1"]}]})
    _use_fake_llm(monkeypatch, answer)
    result = asyncio.run(tailor_cv("ML Intern", "job", load_master_cv(SAMPLE_MASTER)))
    assert not result.grounded
    assert any("'40'" in p for p in result.problems)


def test_malformed_model_output_is_rejected_after_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _use_fake_llm(monkeypatch, "Here is your CV, enjoy!")
    with pytest.raises(LLMOutputError):
        asyncio.run(tailor_cv("ML Intern", "job", load_master_cv(SAMPLE_MASTER)))
    assert len(fake.prompts) == 2


def test_invalid_cv_structure_fails_with_a_user_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The reported failure: the model keeps answering with a section that does not exist (education)."""
    bad = json.dumps({"lines": [{"section": "education", "text": "Diplome d'ingenieur", "source_ids": ["edu-1"]}]})
    fake = _use_fake_llm(monkeypatch, bad)
    with caplog.at_level(logging.WARNING, logger="linkedin_agent.infra.llm"), pytest.raises(CVStructureError) as info:
        asyncio.run(tailor_cv("ML Intern", "job", load_master_cv(SAMPLE_MASTER)))

    assert info.value.message == CV_STRUCTURE_MESSAGE
    assert isinstance(info.value, LLMOutputError)  # callers that catch the generic type keep working
    assert len(fake.prompts) == 2
    assert "lines.0.section: literal_error" in caplog.text  # the log says which rule failed...
    assert "Diplome" not in caplog.text  # ...and never what the model wrote


def test_the_retry_with_the_problem_fed_back_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [
        json.dumps({"lines": [{"section": "experience", "text": "Built a FastAPI service", "source_ids": []}]}),
        json.dumps({"lines": [{"section": "experience", "text": "Built a FastAPI service", "source_ids": ["exp-1"]}]}),
    ]

    class _Sequence(FakeLLM):
        async def complete(self, prompt: str, **options: object) -> str:
            self.prompts.append(prompt)
            return answers[len(self.prompts) - 1]

    fake = _Sequence("")
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: fake)
    result = asyncio.run(tailor_cv("ML Intern", "Python and FastAPI", load_master_cv(SAMPLE_MASTER)))

    assert result.grounded
    assert "lines.0.source_ids: too_short" in fake.prompts[1]


def test_sample_master_cv_is_valid() -> None:
    master = load_master_cv(SAMPLE_MASTER)
    assert master.full_name == "Jane Doe"
    assert len(master.facts) >= 5


# --- compilation plumbing (Tectonic itself is replaced) ----------------------


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_successful_compile_returns_the_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> _Completed:
        assert "--untrusted" in args  # generated LaTeX is never compiled in trusted mode
        (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.5 fake")
        return _Completed(0)

    monkeypatch.setattr(compile_module.subprocess, "run", fake_run)
    pdf = compile_latex("\\documentclass{article}", tmp_path, Path("tectonic"), timeout_seconds=5)
    assert pdf.read_bytes().startswith(b"%PDF")


def test_failed_compile_keeps_the_log_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compile_module.subprocess, "run", lambda args, **kw: _Completed(1, stdout="", stderr="! Undefined control")
    )
    with pytest.raises(CVCompilationError):
        compile_latex("broken", tmp_path, Path("tectonic"), timeout_seconds=5)
    assert "Undefined control" in (tmp_path / "cv-compile.log").read_text(encoding="utf-8")


def test_compile_timeout_is_reported_as_a_cv_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(args: list[str], **kwargs: object) -> _Completed:
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    monkeypatch.setattr(compile_module.subprocess, "run", hang)
    with pytest.raises(CVCompilationError, match="timed out"):
        compile_latex("x", tmp_path, Path("tectonic"), timeout_seconds=1)


def _settings(tectonic_path: str = "") -> Settings:
    return Settings(
        database_url="postgresql://unused/unused_test",
        secret_key="a-long-enough-secret-key",
        token_encryption_key=_fernet_key(),
        tectonic_path=tectonic_path,
    )


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_missing_configured_binary_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(CVCompilationError, match="missing file"):
        find_tectonic(_settings(tectonic_path=str(tmp_path / "nope.exe")))


def test_binary_not_configured_and_not_on_path_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(CVCompilationError, match="TECTONIC_PATH"):
        find_tectonic(_settings())


def test_the_api_refuses_to_start_when_tectonic_is_required_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from linkedin_agent.cv.compile import ensure_tectonic_at_startup

    monkeypatch.setattr(shutil, "which", lambda name: None)
    settings = _settings().model_copy(update={"tectonic_required": True})
    with pytest.raises(RuntimeError, match="API not started.*TECTONIC_PATH.*TECTONIC_REQUIRED=false"):
        ensure_tectonic_at_startup(settings)


def test_the_startup_check_names_a_wrong_windows_path(tmp_path: Path) -> None:
    from linkedin_agent.cv.compile import ensure_tectonic_at_startup

    settings = _settings(tectonic_path=r"C:\tools\tectonic\tectonic.exe").model_copy(update={"tectonic_required": True})
    with pytest.raises(RuntimeError, match="missing file"):
        ensure_tectonic_at_startup(settings)


def test_the_startup_check_passes_with_a_binary_and_is_skipped_when_not_required(tmp_path: Path) -> None:
    from linkedin_agent.cv.compile import ensure_tectonic_at_startup

    binary = tmp_path / "tectonic"
    binary.write_bytes(b"")
    ensure_tectonic_at_startup(_settings(tectonic_path=str(binary)).model_copy(update={"tectonic_required": True}))
    ensure_tectonic_at_startup(
        _settings(tectonic_path=str(tmp_path / "absent")).model_copy(update={"tectonic_required": False})
    )
