from ..infra.search import SearchResult, get_search_provider


async def search_web(query: str, num_results: int = 10) -> list[SearchResult]:
    return await get_search_provider().search(query, num_results)


def build_job_queries(field: str, level: str) -> list[str]:
    """Several search queries derived from the profile."""
    return [
        f"remote {field} jobs hiring now",
        f"{level} {field} job openings 2026",
    ]


def build_cert_queries(field: str, skills: str) -> list[str]:
    skill_list = [s.strip() for s in skills.split(",")][:3]  # top 3 skills
    queries = [f"free {field} certification 2026"]
    for skill in skill_list:
        queries.append(f"free {skill} course certification")
    return queries
