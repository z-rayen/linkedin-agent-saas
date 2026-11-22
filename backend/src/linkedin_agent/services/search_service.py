from ..infra.search import SearchResult, get_search_provider


async def search_web(query: str, num_results: int = 10) -> list[SearchResult]:
    return await get_search_provider().search(query, num_results)
