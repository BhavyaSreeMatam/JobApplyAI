"""Configure and sync the sites jobs are pulled from."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.source import JobSource, SourceKind
from app.services import browser_session, source_sync
from app.models.job import Job
from app.sources import boards, browser_boards, single_job

router = APIRouter(prefix="/sources", tags=["Sources"])

BROWSER_PLATFORMS = set(browser_boards.ADAPTERS)


class SingleJobRequest(BaseModel):
    url: str = Field(min_length=6)


class CareersPageRequest(BaseModel):
    url: str = Field(min_length=4)
    label: str = ""


class BrowserSourceRequest(BaseModel):
    platform: str
    search_terms: str = ""
    location_filter: str = ""
    label: str = ""


class SourceUpdate(BaseModel):
    enabled: bool | None = None
    search_terms: str | None = None
    location_filter: str | None = None
    label: str | None = None


def serialize(source: JobSource) -> dict:
    return {
        "id": source.id,
        "platform": source.platform,
        "kind": source.kind.value,
        "slug": source.slug,
        "label": source.label,
        "source_url": source.source_url,
        "search_terms": source.search_terms,
        "location_filter": source.location_filter,
        "enabled": source.enabled,
        "last_synced_at": source.last_synced_at,
        "last_sync_added": source.last_sync_added,
        "last_sync_found": source.last_sync_found,
        "last_error": source.last_error,
    }


@router.get("")
def list_sources(database: Session = Depends(get_db)):
    rows = database.scalars(select(JobSource).order_by(JobSource.created_at)).all()
    return {
        "sources": [serialize(s) for s in rows],
        "browser_platforms": sorted(BROWSER_PLATFORMS),
        "board_platforms": sorted(boards.ADAPTERS),
        "browser_open": browser_session.is_open(),
        **browser_session.channel_status(),
    }


@router.post("/careers-page")
async def add_careers_page(body: CareersPageRequest, database: Session = Depends(get_db)):
    """Detect the ATS behind a company careers URL and register it."""
    try:
        detected = await boards.detect_from_url(body.url.strip())
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    platform, slug = detected["platform"], detected["slug"]
    existing = database.scalar(
        select(JobSource).where(JobSource.platform == platform, JobSource.slug == slug)
    )
    if existing:
        raise HTTPException(409, f"{existing.label or slug} is already added ({platform}).")

    # Confirm the feed actually returns jobs before saving a dead source.
    try:
        jobs = await boards.fetch(platform, slug, body.label or slug)
    except Exception as error:
        raise HTTPException(
            502,
            f"Found a {platform} board ({slug}) but could not read it: {type(error).__name__}. "
            "Check the URL, or the company may have no open roles.",
        ) from error

    source = JobSource(
        platform=platform,
        kind=SourceKind.BOARD,
        slug=slug,
        label=(body.label or slug).strip(),
        source_url=detected.get("source_url", body.url),
    )
    database.add(source)
    database.commit()
    database.refresh(source)
    return {
        "source": serialize(source),
        "detected_via": detected["method"],
        "jobs_available": len(jobs),
    }


@router.post("/browser")
def add_browser_source(body: BrowserSourceRequest, database: Session = Depends(get_db)):
    platform = body.platform.strip().casefold()
    if platform not in BROWSER_PLATFORMS:
        raise HTTPException(400, f"Unsupported browser source. Choose one of: {', '.join(sorted(BROWSER_PLATFORMS))}")
    source = JobSource(
        platform=platform,
        kind=SourceKind.BROWSER,
        slug="",
        label=(body.label or platform.title()).strip(),
        source_url=browser_boards.SIGN_IN_URLS.get(platform, ""),
        search_terms=body.search_terms.strip(),
        location_filter=body.location_filter.strip(),
    )
    database.add(source)
    database.commit()
    database.refresh(source)
    return {"source": serialize(source)}


@router.patch("/{source_id}")
def update_source(source_id: str, body: SourceUpdate, database: Session = Depends(get_db)):
    source = database.get(JobSource, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(source, field, value)
    database.commit()
    database.refresh(source)
    return {"source": serialize(source)}


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str, database: Session = Depends(get_db)):
    source = database.get(JobSource, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    database.delete(source)
    database.commit()


@router.post("/{source_id}/sync")
async def sync_one(source_id: str, database: Session = Depends(get_db)):
    source = database.get(JobSource, source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    result = await source_sync.sync_source(database, source)
    if result["error"]:
        raise HTTPException(502, result["error"])
    return result


@router.post("/sync")
async def sync_everything(boards_only: bool = False, database: Session = Depends(get_db)):
    return await source_sync.sync_all(
        database, only_kind=SourceKind.BOARD if boards_only else None
    )


@router.post("/browser/open")
async def open_browser(platform: str = ""):
    """Open the persistent browser so the user can sign in to a site."""
    url = browser_boards.SIGN_IN_URLS.get(platform.casefold(), "https://www.linkedin.com/login")
    try:
        await browser_session.open_at(url)
    except Exception as error:
        raise HTTPException(502, f"Could not open the browser: {type(error).__name__}: {error}") from error
    status = browser_session.channel_status()
    message = ("Sign in in the browser window that just opened. Your login is stored "
               "by the browser itself and persists between runs. Close this dialog "
               "when done, then sync the source.")
    if status["browser_substituted"]:
        # The window on screen is not the browser the setting names. Say which one
        # opened, in the same breath as asking the user to sign in to it.
        message = (f"A {status['browser_in_use']} window opened, not "
                   f"{status['browser_wanted']} - {status['browser_note']} "
                   "Sign in there; the login is stored by the browser and persists.")
    return {"opened": True, "url": url, "message": message, **status}


@router.post("/browser/close")
async def close_browser():
    """Close the browser, including one orphaned by an earlier backend run."""
    await browser_session.stop()
    result = browser_session.release_profile()
    return {
        "closed": True,
        "orphans_released": result.get("released", 0),
        "message": (
            f"Closed the browser and released {result['released']} leftover Chromium "
            "process(es). Your logins are stored in the profile folder and survive this."
            if result.get("released") else
            "Browser closed. Your logins are kept in the profile folder."
        ),
    }


@router.post("/single-job")
async def add_single_job(body: SingleJobRequest, database: Session = Depends(get_db)):
    """Import one job from a pasted link so it can be applied to immediately."""
    try:
        listing = await single_job.import_job(body.url)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise HTTPException(502, f"Could not import that job: {type(error).__name__}: {error}") from error

    method = listing.pop("import_method", "")
    url = listing["application_url"]
    existing = database.scalar(select(Job).where(Job.application_url == url))
    if existing is not None:
        if existing.posted_at is None and listing.get("posted_at") is not None:
            existing.posted_at = listing["posted_at"]
            database.commit()
        return {"job_id": existing.id, "title": existing.title, "company": existing.company,
                "already_known": True, "import_method": method}

    job = Job(**listing)
    database.add(job)
    database.commit()
    database.refresh(job)
    return {
        "job_id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description_length": len(job.description or ""),
        "already_known": False,
        "import_method": method,
    }
