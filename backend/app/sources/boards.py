"""Public ATS job-board adapters.

Every board here publishes an unauthenticated JSON feed intended for public
consumption, so pulling from them needs no browser, no credentials, and breaks no
terms of service. Each adapter normalises to the same job dict.

Adapters are written defensively: board payloads vary between tenants, so every
field is looked up with a fallback rather than indexed.
"""
import re
from html import unescape
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.sources.posting_dates import parse_posted

TIMEOUT = 45.0

# Workday tenants can hold thousands of roles; keep one sync bounded.
WORKDAY_MAX_JOBS = 120
HEADERS = {"User-Agent": "JobApplyAI/1.0 (personal job search assistant)"}


def clean_html(content: str) -> str:
    if not content:
        return ""
    text = BeautifulSoup(unescape(str(content)), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _job(**kwargs) -> dict:
    base = {
        "external_id": None, "source": "", "company": "", "title": "",
        "location": None, "workplace_type": None, "employment_type": None,
        "description": "", "application_url": "", "source_url": "",
        "salary_min": None, "salary_max": None, "salary_currency": None,
        "posted_at": None,
    }
    base.update(kwargs)
    return base


async def _get_json(url: str):
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


# --------------------------------------------------------------------------- #
# Individual boards
# --------------------------------------------------------------------------- #

async def greenhouse(slug: str, company: str) -> list[dict]:
    data = await _get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    )
    jobs = []
    for item in data.get("jobs", []):
        url = item.get("absolute_url")
        if not url:
            continue
        jobs.append(_job(
            external_id=str(item.get("id", "")),
            source="greenhouse",
            company=company or slug,
            title=item.get("title") or "Untitled position",
            location=(item.get("location") or {}).get("name"),
            description=clean_html(item.get("content", "")),
            application_url=url,
            posted_at=parse_posted(item.get("first_published")),
        ))
    return jobs


async def lever(slug: str, company: str) -> list[dict]:
    data = await _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for item in data if isinstance(data, list) else []:
        url = item.get("hostedUrl") or item.get("applyUrl")
        if not url:
            continue
        categories = item.get("categories") or {}
        description = clean_html(item.get("descriptionPlain") or item.get("description", ""))
        for section in item.get("lists") or []:
            description += " " + clean_html(section.get("text", "")) + " " + clean_html(section.get("content", ""))
        jobs.append(_job(
            external_id=str(item.get("id", "")),
            source="lever",
            company=company or slug,
            title=item.get("text") or "Untitled position",
            location=categories.get("location"),
            workplace_type=categories.get("workplaceType"),
            employment_type=categories.get("commitment"),
            description=description.strip(),
            application_url=url,
            posted_at=parse_posted(item.get("createdAt")),
        ))
    return jobs


async def ashby(slug: str, company: str) -> list[dict]:
    data = await _get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    jobs = []
    for item in data.get("jobs", []):
        url = item.get("applyUrl") or item.get("jobUrl")
        if not url:
            continue
        comp = item.get("compensation") or {}
        summary = (comp.get("compensationTierSummary") or "") if isinstance(comp, dict) else ""
        low = high = currency = None
        match = re.search(r"([€$£])\s?([\d,]+)\s*[-–]\s*[€$£]?\s?([\d,]+)", summary or "")
        if match:
            currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(match.group(1))
            low = float(match.group(2).replace(",", ""))
            high = float(match.group(3).replace(",", ""))
        jobs.append(_job(
            external_id=str(item.get("id", "")),
            source="ashby",
            company=company or slug,
            title=item.get("title") or "Untitled position",
            location=item.get("location"),
            workplace_type="Remote" if item.get("isRemote") else None,
            employment_type=item.get("employmentType"),
            description=clean_html(item.get("descriptionHtml") or item.get("descriptionPlain", "")),
            application_url=url,
            salary_min=low, salary_max=high, salary_currency=currency,
            posted_at=parse_posted(item.get("publishedAt")),
        ))
    return jobs


async def workable(slug: str, company: str) -> list[dict]:
    data = await _get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    )
    jobs = []
    for item in data.get("jobs", []):
        url = item.get("url") or item.get("application_url") or item.get("shortlink")
        if not url:
            continue
        description = " ".join(
            clean_html(item.get(k, "")) for k in ("description", "requirements", "benefits")
        )
        jobs.append(_job(
            external_id=str(item.get("id") or item.get("shortcode") or ""),
            source="workable",
            company=company or data.get("name") or slug,
            title=item.get("title") or "Untitled position",
            location=", ".join(
                x for x in (item.get("city"), item.get("state"), item.get("country")) if x
            ) or None,
            workplace_type="Remote" if item.get("telecommuting") else None,
            employment_type=item.get("employment_type"),
            description=description.strip(),
            application_url=url,
            posted_at=parse_posted(item.get("published_on") or item.get("created_at")),
        ))
    return jobs


async def smartrecruiters(slug: str, company: str) -> list[dict]:
    jobs, offset = [], 0
    while True:
        data = await _get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"
        )
        page = data.get("content", [])
        for item in page:
            job_id = item.get("id")
            if not job_id:
                continue
            location = item.get("location") or {}
            jobs.append(_job(
                external_id=str(job_id),
                source="smartrecruiters",
                company=company or (item.get("company") or {}).get("name") or slug,
                title=item.get("name") or "Untitled position",
                location=", ".join(
                    x for x in (location.get("city"), location.get("region"), location.get("country")) if x
                ) or None,
                workplace_type="Remote" if location.get("remote") else None,
                employment_type=(item.get("typeOfEmployment") or {}).get("label"),
                description=clean_html(item.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "")),
                application_url=item.get("applyUrl")
                or f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
                posted_at=parse_posted(item.get("releasedDate")),
            ))
        offset += len(page)
        if len(page) < 100 or offset >= data.get("totalFound", 0) or offset > 1000:
            break
    return jobs


async def recruitee(slug: str, company: str) -> list[dict]:
    data = await _get_json(f"https://{slug}.recruitee.com/api/offers/")
    jobs = []
    for item in data.get("offers", []):
        url = item.get("careers_url") or item.get("careers_apply_url")
        if not url:
            continue
        jobs.append(_job(
            external_id=str(item.get("id", "")),
            source="recruitee",
            company=company or slug,
            title=item.get("title") or "Untitled position",
            location=item.get("location") or item.get("city"),
            employment_type=item.get("employment_type_code"),
            description=clean_html(item.get("description", "")) + " " + clean_html(item.get("requirements", "")),
            application_url=url,
            posted_at=parse_posted(item.get("published_at") or item.get("created_at")),
        ))
    return jobs


async def workday(slug: str, company: str) -> list[dict]:
    """Workday's public careers API.

    `slug` is "host|tenant|site", e.g.
    "nvidia.wd5.myworkdayjobs.com|nvidia|NVIDIAExternalCareerSite".

    The listing endpoint returns titles and paths only, so each job's description
    is fetched from its own detail endpoint. Workday tenants are large (NVIDIA has
    ~2000 openings), so this caps how much it pulls in one sync.
    """
    try:
        host, tenant, site = slug.split("|", 2)
    except ValueError as error:
        raise ValueError("Workday source is missing its tenant or site name") from error

    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    headers = {**HEADERS, "Accept": "application/json", "Content-Type": "application/json"}
    jobs: list[dict] = []

    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers, follow_redirects=True) as client:
        offset = 0
        postings: list[dict] = []
        while offset < WORKDAY_MAX_JOBS:
            response = await client.post(
                f"{base}/jobs",
                json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("jobPostings") or []
            postings.extend(page)
            offset += len(page)
            if len(page) < 20 or offset >= int(payload.get("total") or 0):
                break

        for posting in postings[:WORKDAY_MAX_JOBS]:
            path = posting.get("externalPath")
            title = posting.get("title")
            if not path or not title:
                continue
            description, location, employment = "", posting.get("locationsText"), None
            external = f"https://{host}/{site}{path}"
            try:
                detail = await client.get(base + path)
                if detail.status_code == 200:
                    info = detail.json().get("jobPostingInfo") or {}
                    description = clean_html(info.get("jobDescription", ""))
                    location = info.get("location") or location
                    employment = info.get("timeType")
                    external = info.get("externalUrl") or external
            except httpx.HTTPError:
                pass

            jobs.append(_job(
                external_id=str(posting.get("bulletFields", [None])[0] or "") or None,
                source="workday",
                company=company or tenant,
                title=title,
                location=location,
                employment_type=employment,
                description=description,
                application_url=external.split("?")[0],
                posted_at=parse_posted(posting.get("postedOn")),
            ))
    return jobs


ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workable": workable,
    "smartrecruiters": smartrecruiters,
    "recruitee": recruitee,
    "workday": workday,
}


async def fetch(platform: str, slug: str, company: str) -> list[dict]:
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise ValueError(f"Unsupported board platform: {platform}")
    return await adapter(slug, company)


# --------------------------------------------------------------------------- #
# Detecting which board a company careers page is built on
# --------------------------------------------------------------------------- #

URL_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("recruitee", r"([A-Za-z0-9_-]+)\.recruitee\.com"),
]

UNSUPPORTED_PATTERNS = [
    ("taleo", r"taleo\.net", "Taleo"),
    ("icims", r"icims\.com", "iCIMS"),
    ("successfactors", r"successfactors\.(?:com|eu)", "SAP SuccessFactors"),
    ("bamboohr", r"bamboohr\.com/jobs", "BambooHR"),
]


WORKDAY_URL = re.compile(
    r"https?://(?P<host>(?P<tenant>[A-Za-z0-9_-]+)\.wd\d+\.myworkdayjobs\.com)"
    r"(?:/(?:wday/cxs/[^/]+))?"
    r"(?:/(?:[a-z]{2}-[A-Z]{2}))?"
    r"/(?P<site>[A-Za-z0-9_-]+)",
    re.I,
)


def detect_workday(text: str):
    """Return the "host|tenant|site" slug for a Workday careers URL."""
    match = WORKDAY_URL.search(text or "")
    if not match:
        return None
    site = match.group("site")
    if site.lower() in {"wday", "en-us", "job"}:
        return None
    return f"{match.group('host')}|{match.group('tenant')}|{site}"


def detect_in_text(text: str):
    """Return (platform, slug) for the first supported board referenced in text."""
    workday_slug = detect_workday(text)
    if workday_slug:
        return "workday", workday_slug

    for platform, pattern in URL_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            slug = match.group(1)
            if platform == "recruitee" and slug.lower() in {"www", "api", "help", "app"}:
                continue
            return platform, slug
    return None, None


def detect_unsupported(text: str):
    for key, pattern, label in UNSUPPORTED_PATTERNS:
        if re.search(pattern, text, re.I):
            return key, label
    return None, None


async def detect_from_url(url: str, use_browser: bool = True) -> dict:
    """Identify the ATS behind a careers URL.

    Handles a direct board link, a careers page that embeds or links to one, and
    (via the browser session) a careers page that only renders its board link
    after JavaScript. Returns {platform, slug, method, source_url} or raises
    ValueError with a message meant to be shown directly to the user.
    """
    parsed = urlparse(url if "://" in url else "https://" + url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a full careers page or job board URL.")

    platform, slug = detect_in_text(url)
    if platform:
        return {"platform": platform, "slug": slug, "method": "url", "source_url": url}

    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        try:
            response = await client.get(parsed.geturl())
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ValueError(f"Could not load {parsed.hostname}: {error}") from error
        html = response.text
        final_url = str(response.url)

    platform, slug = detect_in_text(final_url)
    if platform:
        return {"platform": platform, "slug": slug, "method": "redirect", "source_url": final_url}

    platform, slug = detect_in_text(html)
    if platform:
        return {"platform": platform, "slug": slug, "method": "page", "source_url": final_url}

    # Many careers pages render their board link only after JavaScript runs
    # (anthropic.com is one). Retry through the real browser before giving up.
    if use_browser:
        try:
            from app.services import browser_session

            rendered = await browser_session.render_html(final_url)
            platform, slug = detect_in_text(rendered)
            if platform:
                return {
                    "platform": platform, "slug": slug,
                    "method": "browser", "source_url": final_url,
                }
            html = html + " " + rendered
        except Exception:
            pass

    key, label = detect_unsupported(html + " " + final_url)
    if key:
        raise ValueError(
            f"This company uses {label}, which has no public job feed. "
            "Add it as a browser-assisted source instead, or paste individual job URLs."
        )
    raise ValueError(
        f"No readable job feed on {parsed.hostname}. Companies that run their own "
        "careers system - Google, Meta, TikTok, Apple and similar - publish nothing "
        "that can be listed automatically. Use \"Apply to one job\" above and paste "
        "individual job links from their site instead; that works on any site. "
        "If you landed on a marketing page, try their actual job-search page first."
    )
