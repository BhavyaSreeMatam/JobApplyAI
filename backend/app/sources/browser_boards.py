"""Job reading from sites that require your own logged-in session.

These sites have no public feed. Rather than scrape them headlessly - which gets
accounts restricted - this reads the search-results page inside the visible
browser the user is signed into, at human pace, one page at a time. It collects
the same listing cards a person would see, then follows each job's own link for
the full description.

Deliberate limits:
  - One page of results per sync; no deep pagination crawls.
  - Randomised human-scale delays between navigations.
  - Bails out immediately if the site shows a sign-in wall or challenge.

Runs on the browser thread with Playwright's sync API - see browser_session.
"""
import re
from urllib.parse import quote_plus, urljoin

from app.services import browser_session, job_description
from app.sources.boards import _job, clean_html
from app.sources.posting_dates import extract_card_date, extract_page_date

MAX_JOBS_PER_SYNC = 25

# Handshake headings that are page furniture rather than a job title.
HANDSHAKE_CHROME_TITLES = {
    "jobs", "search", "add personal email", "stay connected!", "stay connected",
    "career center", "saved", "messages", "inbox", "events", "employers",
}

# Headings that name the employer. Checked in order, and every one of them is
# conditional: "Apply to X" is replaced by the application's own progress panel
# once you have started applying, which is why every job already begun came back
# with no company at all.
HANDSHAKE_COMPANY_HEADINGS = ("apply to ", "about ", "more jobs at ", "jobs at ")

# Boilerplate that follows a company name and is not part of it. Kept to exactly
# this: "Inc.", "LLC" and the like are part of the registered name the employer
# chose, and trimming them turns "TikTok Inc." into "TikTok".
HANDSHAKE_COMPANY_NOISE = re.compile(r"\s*(logo|'s\s+(page|profile))\s*$", re.I)


def _handshake_company(page) -> str:
    """The employer's name from a Handshake job page.

    The logo's alt text is first because it is the only one of these that is
    always present: it reads "<Company> logo" whatever state the application is
    in. Later images on the page are other employers ("alumni in similar roles"),
    so only the first counts.
    """
    try:
        logos = page.locator('img[alt$=" logo" i]')
        for index in range(min(logos.count(), 3)):
            alt = " ".join((logos.nth(index).get_attribute("alt") or "").split())
            name = HANDSHAKE_COMPANY_NOISE.sub("", alt).strip()
            if name and name.casefold() not in HANDSHAKE_CHROME_TITLES:
                return name
    except Exception:
        pass

    try:
        headings = page.locator("h2").all_inner_texts()
    except Exception:
        return ""
    for text in headings:
        cleaned = " ".join(text.split())
        for prefix in HANDSHAKE_COMPANY_HEADINGS:
            if cleaned.casefold().startswith(prefix):
                name = HANDSHAKE_COMPANY_NOISE.sub("", cleaned[len(prefix):]).strip()
                if name and name.casefold() not in HANDSHAKE_CHROME_TITLES:
                    return name
    return ""

# Only the URL is trustworthy for this. Matching challenge words inside raw HTML
# produces constant false positives - these sites ship challenge-handling scripts
# on every page, signed in or not.
CHALLENGE_URLS = (
    "/checkpoint/challenge", "/checkpoint/rm", "captcha", "/authwall",
    "/uas/login", "/login", "/signup", "secure.indeed.com/auth", "/account/login",
)

# Visible text shown by an actual interstitial.
CHALLENGE_TEXT = (
    "verify you are human", "unusual activity", "please verify", "security check",
    "let's do a quick security check", "sign in to see",
)


class NeedsLogin(RuntimeError):
    """The site is not signed in, or is showing a challenge."""


def _url_blocked(url: str) -> bool:
    lowered = (url or "").casefold()
    return any(marker in lowered for marker in CHALLENGE_URLS)


def _visible_challenge(page) -> bool:
    """Check words a person would actually see, not script payloads."""
    try:
        body = (page.locator("body").inner_text(timeout=4000) or "").casefold()
    except Exception:
        return False
    return any(marker in body[:2500] for marker in CHALLENGE_TEXT)


def _count_any(page, selectors: list[str]) -> int:
    total = 0
    for selector in selectors:
        try:
            total += page.locator(selector).count()
        except Exception:
            continue
    return total


def _text_of(locator, fallback=""):
    try:
        return locator.inner_text().strip() if locator.count() else fallback
    except Exception:
        return fallback


# LinkedIn ships obfuscated class names that change often, so every documented
# job-description selector is dead. The page text itself is stable: the body sits
# between "About the job" and the related-jobs rail.
DESCRIPTION_START = (
    "about the job", "job description", "about this role", "about the role",
    "job details", "about the position",
)
DESCRIPTION_END = (
    "people also viewed", "similar jobs", "more jobs", "set alert",
    "looking for talent", "referrals increase your chances",
    "see who you know", "report this job", "explore collaborative articles",
    "alumni in similar roles", "stay connected", "add a personal email",
    "it's better on the app", "check out new search",
)


def _trim_description(text: str) -> str:
    """Cut page chrome off the top and the related-jobs rail off the bottom."""
    body = (text or "").strip()
    if not body:
        return ""
    lowered = body.casefold()

    start = -1
    for marker in DESCRIPTION_START:
        found = lowered.find(marker)
        if found != -1 and (start == -1 or found < start):
            start = found + len(marker)
    if start > 0:
        body = body[start:]
        lowered = body.casefold()

    end = len(body)
    for marker in DESCRIPTION_END:
        found = lowered.find(marker)
        if found > 200:
            end = min(end, found)
    return re.sub(r"\s+", " ", body[:end]).strip()



def _card_posted_at(card, platform):
    try:
        found = extract_card_date(card.inner_html(), platform)
        return found.value if found else None
    except Exception:
        return None


def _page_date(page, job):
    if _url_blocked(page.url) or _visible_challenge(page):
        return None
    try:
        return extract_page_date(page.content(), job, page.url)
    except Exception:
        return None


def _describe_pages(page, jobs, selectors, limit=40000) -> int:
    """Open each collected job in the same tab and pull its description.

    Returns how many were read. The count matters: this used to swallow every
    failure and return nothing, so a source that captured no descriptions at all
    looked exactly like a successful sync. Every Indeed job in one real database
    carried a placeholder because of it.

    `read_url` exists because the href on a sponsored Indeed card is a
    click-tracking redirect (`/pagead/clk?...`), not the posting. Navigating to
    it lands on an interstitial with no description on it, which is why the
    selector and the fallback both found nothing.

    Two rules decide what gets stored. The page is waited on until the posting
    body actually exists rather than for a fixed pause, because a fixed pause
    stores whatever the page happened to be showing - often a spinner. And the
    text has to pass as a posting for THIS job before it is kept: falling back to
    whole-page text meant sign-in walls and verification pages were stored as
    descriptions, which is worse than storing nothing, since nothing is visibly
    recoverable and a wall is not.
    """
    described = 0
    for job in jobs:
        failure = ""
        try:
            page.goto(job.get("read_url") or job["application_url"],
                      wait_until="domcontentloaded", timeout=45000)
            # Wait for the posting body itself. Only fall back to a short pause
            # when the selector never arrives, so a slow page is not truncated
            # into a spinner and a fast one is not waited on for nothing.
            try:
                page.wait_for_selector(selectors, timeout=15000, state="attached")
            except Exception:
                browser_session.human_pause(0.8, 1.6)

            text = ""
            body = page.locator(selectors).first
            if body.count():
                text = clean_html(body.inner_html())
            if len(text) < 300:
                main = page.locator("main").first
                if main.count():
                    text = _trim_description(main.inner_text(timeout=6000))
            if len(text) < 200:
                text = _trim_description(page.locator("body").inner_text(timeout=6000))

            try:
                job["description"] = job_description.check(
                    text, job.get("company", ""), job.get("title", ""),
                    require_identity=True,
                )[:limit]
                described += 1
                found = _page_date(page, job)
                if found:
                    # Precise page evidence wins over a rounded card date.
                    if not job.get("posted_at") or not found.estimated:
                        job["posted_at"] = found.value
            except job_description.NotAPosting as rejected:
                failure = rejected.reason
        except Exception as error:
            failure = f"{type(error).__name__}: {error}".split("\n")[0][:160]
        if failure:
            # Kept on the row so a sync that reads nothing can say why, instead
            # of reporting a clean success over an empty table.
            job["description_error"] = failure
    for job in jobs:
        job.pop("read_url", None)
    return described


# --------------------------------------------------------------------------- #
# LinkedIn
# --------------------------------------------------------------------------- #

def _linkedin(page, search_terms, location, label):
    url = (
        "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(search_terms or "")
        + "&location=" + quote_plus(location or "")
        + "&f_AL=true"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if _url_blocked(page.url):
        raise NeedsLogin(
            "LinkedIn redirected to its sign-in page, so this browser profile is not "
            "logged in. Press 'Sign in' on this source's row, log in, leave the window "
            "open, then Sync."
        )

    card_selector = ("div.job-card-container, li.jobs-search-results__list-item, "
                     "li[data-occludable-job-id], div[data-job-id]")
    try:
        page.wait_for_selector(card_selector, timeout=15000)
    except Exception:
        pass

    if _count_any(page, [card_selector]) == 0:
        if _visible_challenge(page):
            raise NeedsLogin(
                "LinkedIn is showing a verification or sign-in screen. Open the browser "
                "window, clear it yourself, then Sync again."
            )
        raise RuntimeError(
            "LinkedIn returned no results for this search. Check the search terms and "
            "location, or widen them."
        )

    jobs, seen_urls = [], set()
    cards = page.locator(card_selector).all()
    for card in cards:
        if len(jobs) >= MAX_JOBS_PER_SYNC:
            break
        try:
            link = card.locator("a.job-card-container__link, a.job-card-list__title").first
            if not link.count():
                continue
            href = link.get_attribute("href")
            title = link.inner_text().strip().split("\n")[0]
            if not href or not title:
                continue
            full = urljoin("https://www.linkedin.com", href.split("?")[0])
            if full in seen_urls:
                continue
            seen_urls.add(full)
            match = re.search(r"/jobs/view/(\d+)", full)
            jobs.append(_job(
                external_id=match.group(1) if match else None,
                source="linkedin",
                company=_text_of(card.locator(
                    ".job-card-container__primary-description, .artdeco-entity-lockup__subtitle"
                ).first, label) or "Unknown company",
                title=title,
                location=_text_of(card.locator(".job-card-container__metadata-item").first, location) or None,
                description="",
                application_url=full,
                posted_at=_card_posted_at(card, "linkedin"),
            ))
        except Exception:
            continue

    _describe_pages(page, jobs,
                    "div.jobs-description__content, div.show-more-less-html__markup, #job-details")
    return jobs


# --------------------------------------------------------------------------- #
# Indeed
# --------------------------------------------------------------------------- #

# Indeed's job key: 16 hex characters. Matched strictly, because the canonical
# URL is built from it and a wrong key silently reads a different job.
JOB_KEY = re.compile(r"^[0-9a-f]{10,20}$")
JOB_KEY_IN_URL = re.compile(r"[?&]jk=([0-9a-f]{10,20})(?:&|$)")
JOB_KEY_IN_ID = re.compile(r"^(?:job|sj)_([0-9a-f]{10,20})$")


def _indeed_job_key(card, link, href: str) -> str:
    """The posting's own identifier, wherever the card happens to carry it.

    Reading it only out of the href was the whole failure. A sponsored card links
    to `/pagead/clk?mo=r&ad=...`, which contains no `jk` at all, so every
    sponsored job got no identifier, no canonical URL, and was then "read" at its
    tracking redirect - an interstitial with no posting on it. The key is on the
    card markup regardless of sponsorship, in any of these places.
    """
    def clean(value: str | None, pattern=JOB_KEY) -> str:
        text = (value or "").strip()
        if pattern is JOB_KEY:
            return text if JOB_KEY.match(text) else ""
        found = pattern.search(text)
        return found.group(1) if found else ""

    for locator in (link, card):
        try:
            if locator is not None and locator.count():
                key = clean(locator.get_attribute("data-jk"))
                if key:
                    return key
                key = clean(locator.get_attribute("id"), JOB_KEY_IN_ID)
                if key:
                    return key
        except Exception:
            continue
    try:
        nested = card.locator("[data-jk]").first
        if nested.count():
            key = clean(nested.get_attribute("data-jk"))
            if key:
                return key
    except Exception:
        pass
    return clean(href, JOB_KEY_IN_URL)


def _indeed(page, search_terms, location, label):
    url = ("https://www.indeed.com/jobs?q=" + quote_plus(search_terms or "")
           + "&l=" + quote_plus(location or ""))
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if _url_blocked(page.url) or _visible_challenge(page):
        raise NeedsLogin(
            "Indeed is showing a verification challenge. Open the browser window, clear "
            "it yourself, then Sync again. Do not retry immediately."
        )

    indeed_cards = "div.job_seen_beacon, td.resultContent, div[data-jk]"
    try:
        page.wait_for_selector(indeed_cards, timeout=15000)
    except Exception:
        pass
    if _count_any(page, [indeed_cards]) == 0:
        raise RuntimeError(
            "Indeed returned no results for this search. Check the terms and location."
        )

    jobs, seen_urls, seen_keys = [], set(), set()
    for card in page.locator(indeed_cards).all():
        if len(jobs) >= MAX_JOBS_PER_SYNC:
            break
        try:
            link = card.locator("h2.jobTitle a, a.jcs-JobTitle").first
            if not link.count():
                continue
            href = link.get_attribute("href")
            title = link.inner_text().strip()
            if not href or not title:
                continue
            full_url = urljoin("https://www.indeed.com", href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            key = _indeed_job_key(card, link, href)
            # The canonical posting, not the click-tracking redirect the card
            # links to. `/pagead/clk?...` bounces through an interstitial that
            # carries no job description, which is why every sponsored Indeed job
            # came back with none. Derived only from a verified key - never
            # guessed - and the card's own link is kept as the source URL.
            canonical = f"https://www.indeed.com/viewjob?jk={key}" if key else full_url
            if key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            jobs.append(_job(
                external_id=key or None,
                source="indeed",
                company=_text_of(card.locator(
                    '[data-testid="company-name"], span.companyName'
                ).first, label) or "Unknown company",
                title=title,
                location=_text_of(card.locator(
                    '[data-testid="text-location"], div.companyLocation'
                ).first, location) or None,
                description="",
                # The canonical posting is the address worth keeping: the
                # tracking link is regenerated on every search, so storing it
                # made the same job arrive as a new row each sync.
                application_url=canonical,
                source_url=full_url,
                posted_at=_card_posted_at(card, "indeed"),
            ))
            jobs[-1]["read_url"] = canonical
        except Exception:
            continue

    described = _describe_pages(page, jobs, "#jobDescriptionText")
    if jobs and not described:
        raise RuntimeError(
            f"Read {len(jobs)} Indeed listings but could not open any of their "
            "descriptions - Indeed may be showing a verification challenge. Open "
            "the browser window, clear it, then sync again."
        )
    return jobs


# --------------------------------------------------------------------------- #
# Handshake (university SSO)
# --------------------------------------------------------------------------- #

def _handshake(page, search_terms, location, label):
    """Handshake renders job cards as wrapper links with no text of their own.

    Reading the card gives nothing - inner_text() on those anchors is empty, so
    an earlier version silently collected zero jobs. The hrefs are reliable
    though, so collect the URLs and read each job's own page for the details.
    """
    url = "https://app.joinhandshake.com/job-search?query=" + quote_plus(search_terms or "")
    if location:
        url += "&location=" + quote_plus(location)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)

    if _url_blocked(page.url) or "sso" in page.url.casefold() or _visible_challenge(page):
        raise NeedsLogin(
            "Handshake is not signed in. Press 'Sign in' on this source's row, complete "
            "your university single sign-on, leave the window open, then Sync."
        )

    links, seen = [], set()
    for anchor in page.locator('a[href*="/job-search/"], a[href*="/jobs/"]').all():
        try:
            href = anchor.get_attribute("href")
        except Exception:
            continue
        if not href:
            continue
        identifier = re.search(r"/(?:job-search|jobs)/(\d+)", href)
        if not identifier or identifier.group(1) in seen:
            continue
        seen.add(identifier.group(1))
        # /jobs/<id> is the canonical page: it opens directly, its first heading
        # is the role, and it carries the posted date. /job-search/<id> is the
        # results page with that job selected, which is far messier to read.
        links.append((identifier.group(1), f"https://app.joinhandshake.com/jobs/{identifier.group(1)}"))
        if len(links) >= MAX_JOBS_PER_SYNC:
            break

    if not links:
        raise RuntimeError(
            "Handshake returned no results for this search. Check the search terms, or "
            "widen them in the source's settings."
        )

    jobs = []
    for job_id, link in links:
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=45000)
            # Wait for something that is on the page in every state. The old
            # signal was the "Apply to <Company>" heading, which is not: once an
            # application has been started it is replaced by that application's
            # progress panel. So on exactly the jobs already begun, this waited
            # the full timeout, gave up, and read a page that had not finished
            # rendering - which is the second reason those rows came back empty.
            # The employer logo is present whatever state the application is in.
            for selector in ('img[alt$=" logo" i]', "main h1"):
                try:
                    if page.locator(selector).count():
                        break
                    page.wait_for_selector(selector, timeout=6000)
                    break
                except Exception:
                    continue
            browser_session.human_pause(0.7, 1.4)

            # Falling back to the source label would put "Handshake" in the
            # company column, which is simply wrong. Leave it unknown instead.
            company = _handshake_company(page)

            # Several h1s exist: the page heading, the role, and any promo banner.
            title = ""
            for text in page.locator("h1").all_inner_texts():
                cleaned = " ".join(text.split())
                if cleaned and cleaned.casefold() not in HANDSHAKE_CHROME_TITLES:
                    title = cleaned
                    break
            if not title:
                continue

            description = ""
            try:
                panel = page.locator(
                    f'xpath=//h1[normalize-space(text())="{title}"]/ancestor::*[self::section or self::div][3]'
                ).first
                if panel.count():
                    description = _trim_description(panel.inner_text(timeout=6000))
            except Exception:
                pass
            if len(description) < 200:
                description = _trim_description(page.locator("body").inner_text(timeout=6000))

            found_date = _page_date(page, {
                "application_url": link, "company": company, "title": title.lstrip(". "),
            })

            jobs.append(_job(
                external_id=job_id,
                source="handshake",
                company=company or "Unknown company",
                title=title.lstrip(". "),
                location=location or None,
                description=description[:40000],
                application_url=link,
                posted_at=found_date.value if found_date else None,
            ))
        except Exception:
            continue

    return jobs


READERS = {"linkedin": _linkedin, "indeed": _indeed, "handshake": _handshake}
ADAPTERS = READERS  # name kept for the sources API

SIGN_IN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "handshake": "https://app.joinhandshake.com/login",
}


async def fetch(platform: str, search_terms: str, location: str, label: str) -> list[dict]:
    reader = READERS.get(platform)
    if reader is None:
        raise ValueError(f"Unsupported browser source: {platform}")

    def command(worker):
        page = worker.context().new_page()
        try:
            found = reader(page, search_terms or "", location or "", label or platform.title())
            return [job for job in found if job["title"]]
        finally:
            try:
                page.close()
            except Exception:
                pass

    return await browser_session.run(command, timeout=600)
