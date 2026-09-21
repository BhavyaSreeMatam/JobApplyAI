"""Import one job from any URL the user pastes.

Three strategies, cheapest first:
  1. The URL points at a supported ATS board - pull the exact job from its feed.
  2. The page is plain enough to read over HTTP - extract and let Claude structure.
  3. Anything else - render it in the browser first, then structure it.

Strategy 1 costs nothing and is exact. 2 and 3 spend one small model call.
"""
import re
from urllib.parse import urlparse

import httpx

from app.schemas.tailoring import JobPosting
from app.services import claude_client
from app.sources import boards
from app.sources.posting_dates import extract_page_date

EXTRACTOR_SYSTEM = """You read one web page and decide whether it is a single job posting.

If it is, extract the posting exactly as written:
- `title`: the role title alone, no company name, no location, no requisition id.
- `company`: the hiring company.
- `location`: as stated; "" when the page does not say.
- `employment_type`: full-time, internship, contract, part-time, or "".
- `description`: the FULL posting body - responsibilities, requirements,
  qualifications, and tech stack. This is what the resume gets tailored against,
  so completeness matters far more than brevity. Copy it faithfully; do not
  summarise or paraphrase. Drop only navigation, cookie banners, and footers.
- `is_job_posting`: false if this is a job LIST, a search page, a login wall, or
  an error page rather than one specific job. Leave the other fields empty then."""


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("That does not look like a job URL.")
    return url


async def _from_board(url: str):
    """Exact match against a supported ATS feed, when the URL is one."""
    platform, slug = boards.detect_in_text(url)
    if not platform or not slug:
        return None
    try:
        listings = await boards.fetch(platform, slug, slug)
    except Exception:
        return None

    target = url.split("?")[0].rstrip("/")
    for listing in listings:
        if (listing.get("application_url") or "").split("?")[0].rstrip("/") == target:
            return listing

    # Board URLs carry the posting id; match on that when the host differs.
    identifier = re.search(r"/(?:jobs/)?([0-9a-f-]{6,})/?$", target)
    if identifier:
        needle = identifier.group(1)
        for listing in listings:
            if listing.get("external_id") == needle or needle in (listing.get("application_url") or ""):
                return listing
    return None


async def _page_text(url: str) -> tuple[str, str, str]:
    """Return (visible text, final url, HTML), rendering in the browser if needed."""
    headers = {"User-Agent": "JobApplyAI/1.0 (personal job search assistant)"}
    text, final, html = "", url, ""
    try:
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            final = str(response.url)
            html = response.text
            text = boards.clean_html(html)
    except Exception:
        text = ""

    # Job pages are usually client-rendered; fall back to the real browser.
    if len(text) < 900:
        from app.services import browser_session

        def render(worker):
            page = worker.context().new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_selector("h1, #jobDescriptionText, #job-details", timeout=8000)
                except Exception:
                    pass
                return page.content(), page.url
            finally:
                page.close()

        rendered_html, rendered_url = await browser_session.run(render, timeout=120)
        rendered = boards.clean_html(rendered_html)
        if len(rendered) > len(text):
            text, html, final = rendered, rendered_html, rendered_url
    return text[:60000], final, html


async def import_job(url: str) -> dict:
    """Turn a pasted URL into a job row ready for the apply pipeline."""
    url = _clean_url(url)

    listing = await _from_board(url)
    if listing:
        listing["import_method"] = "ats-feed"
        return listing

    text, final, html = await _page_text(url)
    if len(text) < 300:
        raise ValueError(
            "Could not read that page. If it needs a login, sign in via the browser "
            "first, or paste the job description into the job manually."
        )

    posting = claude_client.parse(
        system=EXTRACTOR_SYSTEM,
        user=f"URL: {final}\n\nPAGE CONTENT:\n{text}",
        output_format=JobPosting,
        effort="low",
        max_tokens=16000,
    )
    if not posting.is_job_posting or not posting.title:
        raise ValueError(
            "That page is not a single job posting - it looks like a job list or a "
            "search page. Add it as a career page instead, or paste one job's URL."
        )

    evidence = extract_page_date(html, {
        "title": posting.title, "company": posting.company, "application_url": final,
    }, final)
    return {
        "external_id": None,
        "source": urlparse(final).hostname or "link",
        "company": posting.company or "Unknown company",
        "title": posting.title,
        "location": posting.location or None,
        "workplace_type": None,
        "employment_type": posting.employment_type or None,
        "description": posting.description,
        "application_url": final,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "posted_at": evidence.value if evidence else None,
        "import_method": "page-extract",
    }
