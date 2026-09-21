"""Pull jobs from configured sources into the local job table."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.source import JobSource, SourceKind
from app.services.job_matcher import rescore_all
from app.sources import boards

MAX_DESCRIPTION = 60000

# Readers attach working notes to their rows - why a description could not be
# read, which URL was followed. Those belong in the sync report, not in a column,
# and passing them straight to the model raises TypeError.
JOB_FIELDS = {column.key for column in Job.__table__.columns} - {"id"}


def _store(database: Session, rows: list[dict]) -> tuple[int, int]:
    """Insert new jobs, refresh descriptions on ones already known."""
    added = updated = 0
    for row in rows:
        url = (row.get("application_url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        row["description"] = (row.get("description") or "").strip()[:MAX_DESCRIPTION]
        if not row["description"]:
            # Left genuinely empty. It used to be filled with
            # "<Title> at <Company>. See the original posting." - a sentence that
            # reads like a description, counts as one everywhere that checks for
            # emptiness, and says nothing. Tailoring then ran against the job
            # title alone and produced a confident resume built on nothing.
            #
            # Empty is honest and actionable: the job still lists and opens, and
            # the UI can offer to fetch or paste the real description.
            row["description"] = ""

        # Identity first, address second. A sponsored Indeed card carries a fresh
        # tracking URL on every search, so matching on the address alone filed the
        # same posting as a new job each sync - which is where the duplicate
        # Capital One and Konica Minolta rows came from.
        existing = None
        external_id = (row.get("external_id") or "").strip()
        if external_id:
            existing = database.scalar(select(Job).where(
                Job.source == row.get("source", ""), Job.external_id == external_id
            ))
        if existing is None:
            existing = database.scalar(select(Job).where(Job.application_url == url))

        if existing is None:
            database.add(Job(**{k: v for k, v in row.items() if k in JOB_FIELDS}))
            added += 1
        else:
            # Refresh volatile fields; never touch status or match_score.
            existing.title = title
            # A description already captured is not given up because a later read
            # failed. Re-syncs routinely come back empty - the posting is behind a
            # wall that day, or the reader was rate-limited - and overwriting made
            # the app lose work it had already done, silently.
            if row["description"]:
                existing.description = row["description"]
            if not existing.external_id and external_id:
                existing.external_id = external_id
            if row.get("source_url"):
                existing.source_url = row["source_url"]
            if existing.application_url != url and not database.scalar(
                select(Job).where(Job.application_url == url, Job.id != existing.id)
            ):
                # Upgrading a tracking redirect to the canonical posting, but only
                # when nothing else already holds that address - application_url
                # is unique, and a collision here would abort the whole sync.
                existing.application_url = url
            existing.location = row.get("location") or existing.location
            existing.salary_min = row.get("salary_min") or existing.salary_min
            existing.salary_max = row.get("salary_max") or existing.salary_max
            existing.salary_currency = row.get("salary_currency") or existing.salary_currency
            # A rounded "3 days ago" must not move the original date on every sync.
            if existing.posted_at is None and row.get("posted_at") is not None:
                existing.posted_at = row["posted_at"]
            updated += 1
    database.commit()
    return added, updated


async def sync_source(database: Session, source: JobSource) -> dict:
    """Fetch one source. Never raises: failures are recorded on the row."""
    result = {
        "source_id": source.id,
        "label": source.label or source.slug,
        "platform": source.platform,
        "found": 0,
        "added": 0,
        "updated": 0,
        "without_description": 0,
        "with_posted_date": 0,
        "without_posted_date": 0,
        "error": "",
    }
    try:
        if source.kind == SourceKind.BOARD:
            rows = await boards.fetch(source.platform, source.slug, source.label or source.slug)
        else:
            from app.sources import browser_boards

            rows = await browser_boards.fetch(
                source.platform,
                search_terms=source.search_terms,
                location=source.location_filter,
                label=source.label,
            )
        result["found"] = len(rows)
        result["with_posted_date"] = sum(r.get("posted_at") is not None for r in rows)
        result["without_posted_date"] = len(rows) - result["with_posted_date"]
        # A sync that files jobs but reads none of their descriptions is a
        # partial failure, and used to report as a clean success. Saying so is
        # what tells the user the reader is blocked rather than the board empty.
        missing = [r for r in rows if not (r.get("description") or "").strip()]
        result["without_description"] = len(missing)
        result["added"], result["updated"] = _store(database, rows)
        source.last_error = ""
        if rows and len(missing) == len(rows):
            reasons = {r["description_error"] for r in missing if r.get("description_error")}
            detail = f" ({sorted(reasons)[0]})" if reasons else ""
            source.last_error = (
                f"Listed {len(rows)} jobs but could not read any descriptions{detail}"
            )[:400]
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        result["error"] = message[:400]
        source.last_error = message[:400]

    source.last_synced_at = datetime.now(timezone.utc)
    source.last_sync_found = result["found"]
    source.last_sync_added = result["added"]
    database.commit()

    # Score the new rows here as well as in sync_all - syncing one source on its
    # own otherwise leaves its jobs with an empty Match column.
    if result["added"] or result["updated"]:
        try:
            rescore_all(database)
        except Exception:
            pass
    return result


async def sync_all(database: Session, only_kind: SourceKind | None = None) -> dict:
    statement = select(JobSource).where(JobSource.enabled.is_(True))
    if only_kind is not None:
        statement = statement.where(JobSource.kind == only_kind)
    sources = list(database.scalars(statement).all())

    results = []
    for source in sources:
        results.append(await sync_source(database, source))

    # Newly pulled jobs have no match score until they are ranked, which would
    # otherwise leave every fresh job showing a blank Match column.
    scored = rescore_all(database)

    return {
        "jobs_scored": scored.get("jobs_scored", 0),
        "sources_synced": len(results),
        "jobs_found": sum(r["found"] for r in results),
        "jobs_added": sum(r["added"] for r in results),
        "jobs_updated": sum(r["updated"] for r in results),
        "failures": [r for r in results if r["error"]],
        "results": results,
    }
