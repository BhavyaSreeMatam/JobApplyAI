"""Recover dates for explicitly selected saved jobs, in small browser batches."""
import re
from urllib.parse import quote, urlparse

from sqlalchemy import func, select, update

from app.models.job import Job
from app.services import browser_session
from app.sources import boards, browser_boards
from app.sources.posting_dates import _evidence

MAX_BATCH = 10


def _target(job):
    url = job.get("application_url") or job.get("source_url") or ""
    host = (urlparse(url).hostname or "").casefold()
    key = job.get("external_id") or ""
    if (host == "indeed.com" or host.endswith(".indeed.com")) and re.fullmatch(r"[0-9a-f]{10,20}", key):
        return f"https://www.indeed.com/viewjob?jk={key}"
    return url


def _browser_read(worker, jobs):
    results = []
    page = worker.context().new_page()
    try:
        for job in jobs:
            result = {"id": job["id"], "title": job["title"], "status": "not_found",
                      "message": "This page did not expose a matching posting date."}
            try:
                target = _target(job)
                if urlparse(target).scheme not in {"https", "http"}:
                    result.update(status="no_url", message="No usable job link is saved.")
                    results.append(result)
                    continue
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
                if browser_boards._url_blocked(page.url) or browser_boards._visible_challenge(page):
                    result.update(status="blocked", message="Sign in or complete verification in the browser, then retry.")
                    results.append(result)
                    break
                try:
                    page.wait_for_selector('h1, #jobDescriptionText, #job-details', timeout=8000, state="visible")
                except Exception:
                    pass
                found = browser_boards._page_date(page, {**job, "application_url": target})
                # A late challenge should stop the batch, too.
                if browser_boards._url_blocked(page.url) or browser_boards._visible_challenge(page):
                    result.update(status="blocked", message="Sign in or complete verification in the browser, then retry.")
                    results.append(result)
                    break
                if found:
                    result.update(status="found", date=found.value, evidence=found.raw,
                                  evidence_source=found.source, estimated=found.estimated,
                                  message="Date read from this job posting.")
            except Exception as error:
                result.update(status="error", message=f"Could not read this page ({type(error).__name__}).")
            results.append(result)
    finally:
        try:
            page.close()
        except Exception:
            pass
    return results


async def read_dates(jobs):
    """Use Greenhouse's original-publication field, otherwise the saved browser."""
    results, remaining = [], []
    for job in jobs:
        platform, slug = boards.detect_in_text(job.get("application_url", ""))
        key = job.get("external_id") or ""
        if platform == "greenhouse":
            if not key.isdigit():
                match = re.search(r"/jobs/(\d+)", job.get("application_url", ""))
                key = match.group(1) if match else ""
            if key.isdigit():
                try:
                    item = await boards._get_json(
                        f"https://boards-api.greenhouse.io/v1/boards/{quote(slug, safe='')}/jobs/{key}"
                    )
                    evidence = _evidence(item.get("first_published"), "Greenhouse.first_published")
                    if str(item.get("id")) == key and evidence:
                        results.append({"id": job["id"], "title": job["title"], "status": "found",
                                        "date": evidence.value, "evidence": evidence.raw,
                                        "evidence_source": evidence.source, "estimated": False,
                                        "message": "Original publication date read from Greenhouse."})
                        continue
                except Exception:
                    pass
        remaining.append(job)
    if remaining:
        try:
            results.extend(await browser_session.run(lambda worker: _browser_read(worker, remaining), timeout=450))
        except Exception as error:
            results.extend({"id": j["id"], "title": j["title"], "status": "error",
                            "message": f"Browser could not finish ({type(error).__name__})."} for j in remaining)
    return results


async def refresh_missing(database, job_ids):
    """Keep IDs, descriptions, applications and any existing date unchanged."""
    results, jobs = [], []
    for job_id in dict.fromkeys(job_ids):
        job = database.get(Job, job_id)
        if job is None:
            results.append({"id": job_id, "title": "", "status": "missing_job", "message": "Job no longer exists."})
        elif job.posted_at is not None:
            results.append({"id": job.id, "title": job.title, "status": "already_dated", "message": "Existing date kept."})
        else:
            jobs.append({k: getattr(job, k) for k in ("id", "title", "company", "application_url", "source_url", "external_id", "source")})
    # Do not hold a database transaction open during browser navigation.
    database.rollback()
    if jobs:
        results.extend(await read_dates(jobs))
    for result in results:
        if result["status"] == "found":
            changed = database.execute(update(Job).where(
                Job.id == result["id"], Job.posted_at.is_(None),
            ).values(posted_at=result.pop("date")))
            if changed.rowcount != 1:
                result.update(status="unchanged", message="Job was removed or given a date while reading; kept current data.")
    database.commit()
    total = database.scalar(select(func.count(Job.id))) or 0
    dated = database.scalar(select(func.count(Job.id)).where(Job.posted_at.is_not(None))) or 0
    return {"results": results, "recovered": sum(r["status"] == "found" for r in results),
            "checked": len(results), "jobs_total": total, "jobs_with_date": dated,
            "jobs_without_date": total - dated}
