import asyncio

from sqlalchemy.orm import Session

from ..core.exceptions import ExternalServiceError
from ..core.settings import get_settings
from ..infra.llm import get_llm_client
from ..prompts import render
from .rag_service import search_knowledge
from .search_service import search_web

COMMENT_FALLBACK = "Génération échouée, réessaie."
POSTS_TO_CONSIDER = 3
POST_SEARCH_RESULTS = 5
BACKGROUND_ENTRIES = 3


async def suggest_engagement(db: Session, user_id: int, topic: str, profile: dict):
    pause = get_settings().llm_min_interval_seconds
    posts = await search_web(f"site:linkedin.com/posts {topic}", num_results=POST_SEARCH_RESULTS)

    suggestions = []
    for post in posts[:POSTS_TO_CONSIDER]:
        background = search_knowledge(db, user_id, f"{post['title']} {post['summary']}", top_k=BACKGROUND_ENTRIES)
        prompt = render(
            "engage_comment.v1",
            field=profile["field"],
            level=profile["level"],
            background="\n".join(f"- {b}" for b in background) or "(none)",
            post_title=post["title"],
            post_summary=post["summary"],
        )
        try:
            comment = (await get_llm_client().complete(prompt)).strip() or COMMENT_FALLBACK
        except ExternalServiceError:
            comment = COMMENT_FALLBACK
        suggestions.append(
            {
                "post_title": post["title"],
                "post_link": post["link"],
                "suggested_comment": comment,
            }
        )
        await asyncio.sleep(pause)
    return suggestions
