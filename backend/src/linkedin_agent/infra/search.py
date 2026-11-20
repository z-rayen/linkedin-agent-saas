"""Web search behind a provider interface (Serper.dev today, any similar API later)."""

import logging
from functools import lru_cache
from typing import Protocol, TypedDict

import httpx

from ..core.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class SearchResult(TypedDict):
    title: str
    link: str
    summary: str


class SearchProvider(Protocol):
    async def search(self, query: str, num_results: int) -> list[SearchResult]: ...


class SerperSearch:
    """Google results through serper.dev. Returns an empty list on any HTTP failure."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def search(self, query: str, num_results: int) -> list[SearchResult]:
        s = self._settings
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                s.serper_url,
                headers={"X-API-KEY": s.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "num": num_results, "hl": "en"},
                timeout=s.serper_timeout_seconds,
            )
        if resp.status_code != 200:
            logger.error("search failed status=%s", resp.status_code)
            return []
        organic = resp.json().get("organic", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                link=r.get("link", ""),
                summary=r.get("snippet", ""),
            )
            for r in organic
        ]


@lru_cache(maxsize=1)
def get_search_provider() -> SearchProvider:
    """Process-wide search provider, selected from settings."""
    return SerperSearch(get_settings())
