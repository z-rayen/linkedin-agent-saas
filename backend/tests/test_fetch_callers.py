"""The two callers that fetch outside pages: job postings and search results.

The network layer is replaced here, so these tests check what the callers do with the text: extraction,
cleaning, truncation, and the empty result on any refusal.
"""

import asyncio

import pytest

from linkedin_agent.infra.safe_http import FetchError
from linkedin_agent.services import agent_service, apply_service

PAGE = """
<html><head><script>var secret = 1;</script><style>p{}</style></head>
<body><nav>menu</nav><header>top</header>
<p>We need   Python and FastAPI.</p><footer>legal</footer>
<p>Contact jobs@acme.example</p></body></html>
"""


def _fake_fetch(answer: str | Exception):  # type: ignore[no-untyped-def]
    async def fetch(url: str, **kwargs: object) -> str:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


def test_job_page_keeps_the_html_and_returns_clean_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_service, "fetch_text", _fake_fetch(PAGE))
    html, text = asyncio.run(apply_service.fetch_job_page("https://jobs.example.com/1"))
    assert html == PAGE
    assert "We need Python and FastAPI." in text
    assert "Contact jobs@acme.example" in text
    for dropped in ("var secret", "menu", "top", "legal", "p{}"):
        assert dropped not in text


def test_job_page_text_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_service, "PAGE_MAX_TEXT_CHARS", 20)
    monkeypatch.setattr(apply_service, "fetch_text", _fake_fetch(PAGE))
    _, text = asyncio.run(apply_service.fetch_job_page("https://jobs.example.com/1"))
    assert len(text) == 20


@pytest.mark.parametrize("error", [FetchError("refused"), FetchError("non-public address")])
def test_a_refused_job_page_yields_nothing(monkeypatch: pytest.MonkeyPatch, error: FetchError) -> None:
    monkeypatch.setattr(apply_service, "fetch_text", _fake_fetch(error))
    assert asyncio.run(apply_service.fetch_job_page("http://169.254.169.254/")) == ("", "")


def test_a_search_result_page_is_read_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_service, "fetch_text", _fake_fetch(PAGE))
    text = asyncio.run(agent_service.read_page("https://blog.example.com/post"))
    assert "We need Python and FastAPI." in text
    assert "var secret" not in text


def test_a_search_result_page_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_service, "fetch_text", _fake_fetch(PAGE))
    assert len(asyncio.run(agent_service.read_page("https://blog.example.com/post", max_chars=12))) == 12


def test_a_refused_search_result_yields_an_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_service, "fetch_text", _fake_fetch(FetchError("only text/html is read")))
    assert asyncio.run(agent_service.read_page("https://files.example.com/report.pdf")) == ""
