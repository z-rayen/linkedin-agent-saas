"""LinkedIn post generation for the content queue."""

from ..infra.llm import get_llm_client
from ..prompts import render


async def generate_post(title: str, context: str, profile_field: str = "Data Science / Machine Learning") -> str:
    prompt = render("content_post.v1", field=profile_field, title=title, context=context)
    return await get_llm_client().complete(prompt)
