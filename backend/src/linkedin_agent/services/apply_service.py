import re

from bs4 import BeautifulSoup

from ..core.exceptions import ExternalServiceError
from ..domain.outputs import ApplicationEmailOutput
from ..infra.llm import complete_structured, get_llm_client
from ..infra.safe_http import FetchError, fetch_text
from ..prompts import render

EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
BAD_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "woff", "woff2"}
BAD_PARTS = ("noreply", "no-reply", "donotreply", "example.com", "sentry", "wixpress")
PREFERRED = ("job", "career", "recruit", "hr", "rh", "talent", "hiring", "stage", "intern", "contact")
PAGE_MAX_HTML_CHARS = 300000
PAGE_MAX_TEXT_CHARS = 3000
JOB_TEXT_MAX_CHARS = 1500
CV_TEXT_FALLBACK = "Génération échouée, réessaie."


async def fetch_job_page(url: str) -> tuple[str, str]:
    """Return (raw html, cleaned text) of the job posting. A refused or failed fetch yields ("", "")."""
    try:
        html = await fetch_text(url)
    except FetchError:
        return "", ""
    soup = BeautifulSoup(html[:PAGE_MAX_HTML_CHARS], "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return html, text[:PAGE_MAX_TEXT_CHARS]


def find_application_email(html: str) -> str | None:
    """Email published on the job page only. Never guessed."""
    found = []
    for e in re.findall(EMAIL_REGEX, html):
        e = e.lower()
        if e.rsplit(".", 1)[-1] in BAD_TLDS or any(b in e for b in BAD_PARTS):
            continue
        if e not in found:
            found.append(e)
    found.sort(key=lambda e: 0 if any(p in e for p in PREFERRED) else 1)
    return found[0] if found else None


async def generate_tailored_cv_text(job_title: str, job_text: str, profile: dict, knowledge_text: str) -> str:
    prompt = render(
        "cv_section.v1",
        field=profile["field"],
        level=profile["level"],
        skills=profile["skills"],
        knowledge=knowledge_text or "(empty)",
        job_title=job_title,
        job_text=job_text[:JOB_TEXT_MAX_CHARS],
    )
    try:
        return (await get_llm_client().complete(prompt)).strip() or CV_TEXT_FALLBACK
    except ExternalServiceError:
        return CV_TEXT_FALLBACK


async def generate_application_email(job_title: str, cv_summary: str, profile: dict, feedback: str = "(none)") -> dict:
    prompt = render(
        "application_email.v1",
        field=profile["field"],
        level=profile["level"],
        cv_summary=cv_summary,
        job_title=job_title,
        feedback=feedback,
    )
    fallback_subject = f"Candidature - {job_title}"
    try:
        email = await complete_structured(prompt, ApplicationEmailOutput)
    except ExternalServiceError:
        return {"subject": fallback_subject, "body": ""}
    return {"subject": email.subject or fallback_subject, "body": email.body}
