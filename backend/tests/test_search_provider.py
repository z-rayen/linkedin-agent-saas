"""Serper search provider, with the HTTP transport mocked. No network."""

import asyncio
import json

import httpx

from linkedin_agent.core.settings import get_settings
from linkedin_agent.infra.search import SerperSearch, get_search_provider


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_search_posts_query_with_api_key_and_maps_organic_results(mock_http) -> None:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "organic": [
                    {"title": "Job A", "link": "https://jobs.example/a", "snippet": "Python role"},
                    {"title": "Job B", "link": "https://jobs.example/b"},
                ]
            },
        )

    mock_http(handler)
    settings = get_settings()
    results = _run(SerperSearch(settings).search("ml engineer", num_results=2))

    assert results == [
        {"title": "Job A", "link": "https://jobs.example/a", "summary": "Python role"},
        {"title": "Job B", "link": "https://jobs.example/b", "summary": ""},
    ]
    request = seen[0]
    assert str(request.url) == settings.serper_url
    assert request.headers["X-API-KEY"] == settings.serper_api_key
    assert json.loads(request.content) == {"q": "ml engineer", "num": 2, "hl": "en"}


def test_search_returns_empty_list_on_http_failure(mock_http) -> None:  # type: ignore[no-untyped-def]
    mock_http(lambda request: httpx.Response(429, json={"message": "rate limited"}))

    assert _run(SerperSearch(get_settings()).search("anything", num_results=3)) == []


def test_search_returns_empty_list_when_no_organic_key(mock_http) -> None:  # type: ignore[no-untyped-def]
    mock_http(lambda request: httpx.Response(200, json={"answerBox": {}}))

    assert _run(SerperSearch(get_settings()).search("anything", num_results=3)) == []


def test_get_search_provider_is_a_process_wide_singleton() -> None:
    get_search_provider.cache_clear()
    first = get_search_provider()

    assert isinstance(first, SerperSearch)
    assert get_search_provider() is first
    get_search_provider.cache_clear()
