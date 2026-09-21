"""Read publication dates from evidence belonging to one job, never crawl time.

Pure HTML parsing is shared by browser sync, pasted links and date recovery.
Relative dates are estimates; updated/reposted/expiry dates are not substitutes
for an original publication date. No model call is needed.
"""
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

UTC = timezone.utc
RELATIVE = re.compile(
    r"(\d+)\s*(\+)?\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|mo|wk|[dwyh])(?:\s+ago)?",
    re.I,
)
OTHER_DATE = re.compile(r"\b(?:reposted|re-posted|updated|modified|expires?|expiry|deadline|closing)\b", re.I)
PREFIX = re.compile(r"^(?:(?:originally\s+)?(?:date\s+)?(?:posted|published)(?:\s+date)?\s*(?:on\b|:)?\s*)", re.I)


def parse_posted(value, *, now=None):
    """ISO, epoch seconds/milliseconds, named months and relative English.

    Callers must already have identified this as a posting-date field. Ambiguous
    numeric calendar dates and a bare 'm' (months/minutes) stay unknown.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, datetime):
            result = value
        elif isinstance(value, (int, float)):
            if not math.isfinite(value):
                return None
            result = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
        else:
            text = " ".join(str(value).split()).strip(" .")
            if not text or OTHER_DATE.search(text):
                return None
            text = PREFIX.sub("", text)
            if re.fullmatch(r"\d{10}|\d{13}", text):
                return parse_posted(int(text), now=now)
            try:
                result = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                result = None
                for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
                    try:
                        result = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
                if result is None:
                    lowered = text.casefold()
                    if lowered in {"today", "just posted", "just now"}:
                        return now
                    if lowered == "yesterday":
                        return now - timedelta(days=1)
                    match = RELATIVE.fullmatch(lowered)
                    if not match:
                        return None
                    amount, _, unit = match.groups()
                    seconds = {
                        "minute": 60, "minutes": 60, "min": 60, "mins": 60,
                        "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
                        "day": 86400, "days": 86400, "d": 86400,
                        "week": 604800, "weeks": 604800, "wk": 604800, "w": 604800,
                        "month": 2592000, "months": 2592000, "mo": 2592000,
                        "year": 31536000, "years": 31536000, "y": 31536000,
                    }[unit]
                    result = now - timedelta(seconds=int(amount) * seconds)
        result = result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
        # Allow a calendar date in a timezone ahead of UTC, not a future expiry.
        return result if datetime(1990, 1, 1, tzinfo=UTC) <= result <= now + timedelta(days=1) else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


@dataclass(frozen=True)
class DateEvidence:
    value: datetime
    raw: str
    source: str
    estimated: bool


def _evidence(raw, source, now=None):
    value = parse_posted(raw, now=now)
    if value is None:
        return None
    text = PREFIX.sub("", str(raw).strip())
    estimated = bool(RELATIVE.fullmatch(text)) or text.casefold() in {
        "today", "yesterday", "just posted", "just now",
    }
    return DateEvidence(value, str(raw)[:160], source, estimated)


def _norm(value):
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def _url_key(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").removeprefix("www.").casefold()
    query = parse_qs(parsed.query)
    if host.endswith("indeed.com"):
        key = (query.get("jk") or query.get("vjk") or [""])[0]
        if key:
            return "indeed", key
    if host.endswith("linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)/?$", parsed.path)
        if match:
            return "linkedin", match.group(1)
    if host.endswith("joinhandshake.com"):
        match = re.search(r"/(?:jobs|job-search)/(\d+)", parsed.path)
        if match:
            return "handshake", match.group(1)
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.search(r"/([^/]+)/jobs/(\d+)", parsed.path)
        if match:
            return "greenhouse", "/".join(match.groups())
    # Keep identifiers in query strings; discard only known tracking fields.
    query = {k: v for k, v in query.items() if not k.startswith("utm_") and k not in {"trk", "trackingId", "refId", "source"}}
    return host, parsed.path.rstrip("/") + "?" + urlencode(sorted(query.items()), doseq=True)


def same_job_url(left, right):
    return bool(left and right and _url_key(left) == _url_key(right))


def _job_nodes(value):
    if isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and any(str(t).rsplit("/", 1)[-1] == "JobPosting" for t in types):
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _job_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _job_nodes(child)


def _matches(node, job, page_url):
    urls = [v for v in (node.get("url"), node.get("@id")) if isinstance(v, str) and not v.startswith("#")]
    expected = [job.get("application_url"), job.get("source_url")]
    if not any(expected):
        expected = [page_url]
    organization = node.get("hiringOrganization") or {}
    company = organization.get("name", "") if isinstance(organization, dict) else organization
    names_match = bool(_norm(job.get("title")) and _norm(company)
                      and _norm(node.get("title")) == _norm(job.get("title"))
                      and _norm(company) == _norm(job.get("company")))
    if not urls:
        return names_match
    if any(same_job_url(urljoin(page_url, url), target) for url in urls for target in expected):
        return True
    # An old sponsored link may contain no verified jk. After it redirects,
    # require both the saved title/company and the final URL to match the schema.
    target = job.get("application_url") or ""
    parsed = urlparse(target)
    opaque_indeed_redirect = (
        (parsed.hostname == "indeed.com" or (parsed.hostname or "").endswith(".indeed.com"))
        and parsed.path in {"/pagead/clk", "/rc/clk"} and _url_key(target)[0] != "indeed"
    )
    return bool(opaque_indeed_redirect and names_match
                and any(same_job_url(urljoin(page_url, url), page_url) for url in urls))


def _hidden(node):
    for parent in [node, *node.parents]:
        if getattr(parent, "attrs", None):
            style = str(parent.get("style", "")).replace(" ", "").casefold()
            if parent.has_attr("hidden") or parent.get("aria-hidden") == "true" or "display:none" in style or "visibility:hidden" in style:
                return True
    return False


def _node_evidence(node, source, now=None):
    text = node.get_text(" ", strip=True)
    context = " ".join((text, node.get("title", ""), node.get("aria-label", "")))
    if _hidden(node) or OTHER_DATE.search(context):
        return None
    # A machine-readable time takes precedence over rounded visible wording.
    for value in (node.get("datetime"), node.get("content"), text):
        if value:
            result = _evidence(value, source, now)
            if result:
                return result
    return None


CARD_SELECTORS = {
    "linkedin": 'time, .job-card-container__footer-item, [class*="posted"]',
    "indeed": 'time, [data-testid="myJobsStateDate"], [data-testid="job-age"], span.date, [class*="date"]',
}
HEADER_SELECTORS = (
    '.job-details-jobs-unified-top-card__primary-description-container, '
    '.jobs-unified-top-card__primary-description, .topcard__flavor-row, '
    '.posted-time-ago__text, .jobsearch-JobMetadataFooter, '
    '[data-testid="jobsearch-JobMetadataFooter"], '
    '[data-testid="jobsearch-JobInfoHeader-companyLocation"]'
)
DATE_SELECTORS = (
    '[itemprop="datePosted"], [data-testid="job-age"], '
    '[data-testid="posted-date"], [data-testid="posting-date"], '
    '[data-testid="job-posted-date"], [data-testid="jobsearch-JobMetadataFooter-datePosted"], '
    'time[class*="posted"], time[class*="posting"], time[data-testid*="posted"]'
)


def extract_card_date(html, platform, *, now=None):
    soup = BeautifulSoup(html or "", "html.parser")
    if re.search(r"\bre-?posted\b", soup.get_text(" ", strip=True), re.I):
        return None
    results = []
    for node in soup.select(CARD_SELECTORS.get(platform, "time")):
        # Do not turn a nearby 'Reposted' label into an original date.
        parent_text = node.parent.get_text(" ", strip=True) if node.parent else ""
        if node.name == "time" and len(parent_text) < 180 and OTHER_DATE.search(parent_text):
            continue
        found = _node_evidence(node, "listing card", now)
        if found:
            results.append(found)
    return next((r for r in results if not r.estimated), results[0] if results else None)


def extract_page_date(html, job, page_url="", *, now=None):
    """Match JSON-LD to this job, then inspect its posting header/explicit labels.

    Never select the first date anywhere in body text: recommendations, company
    news, deadlines and reposting timestamps are often on the same page.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            nodes = _job_nodes(json.loads(script.string or script.get_text()))
            for node in nodes:
                if _matches(node, job, page_url):
                    found = _evidence(node.get("datePosted"), "JobPosting.datePosted", now)
                    if found:
                        return found
        except (ValueError, TypeError, RecursionError):
            continue

    # For unstructured markup, demand the expected title in the main heading.
    # The recommendation rail can repeat it, so neither body text nor h2 suffice.
    expected = _norm(job.get("title"))
    headings = [h for h in soup.select("h1") if not _hidden(h) and _norm(h.get_text(" ", strip=True)) == expected]
    if not expected or not headings:
        return None
    if job.get("application_url") and page_url:
        old, new = _url_key(job["application_url"]), _url_key(page_url)
        if old[0] == new[0] and old != new and old[0] in {"indeed", "linkedin", "handshake"}:
            return None
    # Ignore other job cards and navigation even when they use the same classes.
    for element in list(soup.select("aside, nav, footer, [data-testid*='recommend'], .similar-jobs, .jobs-similar-jobs, .job_seen_beacon, .job-card-container")):
        element.decompose()
    for node in soup.select(DATE_SELECTORS):
        scope = node.find_parent(attrs={"itemtype": re.compile(r"JobPosting/?$")})
        if scope:
            title_node = scope.select_one('[itemprop="title"]')
            if title_node and _norm(title_node.get_text(" ", strip=True)) != expected:
                continue
        found = _node_evidence(node, "posting date element", now)
        if found:
            return found
    for node in soup.select(HEADER_SELECTORS):
        if _hidden(node):
            continue
        text = node.get_text(" ", strip=True)
        for fragment in re.split(r"[•·|\n]", text):
            found = _evidence(fragment.strip(), "job header", now)
            if found:
                return found
    # Explicitly labelled metadata, including Handshake and unfamiliar boards.
    # Limit to the main job's header before the job-description boundary.
    heading = next((h for h in soup.select("h1") if _norm(h.get_text(" ", strip=True)) == expected), None)
    if heading:
        for node in heading.find_all_next(["p", "span", "div", "time", "h2", "section", "article"]):
            text = node.get_text(" ", strip=True)
            if re.match(r"^(?:about (?:the|this) (?:job|role)|job description|responsibilities|similar jobs|more jobs)", text, re.I):
                break
            if len(text) <= 120 and PREFIX.match(text) and not OTHER_DATE.search(text):
                found = _evidence(text, "labelled posting date", now)
                if found:
                    return found
    return None
