from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.job import Job
from app.schemas.job import JobCreate, JobRead
from app.services import job_description


router = APIRouter(
    prefix="/jobs",
    tags=["Jobs"],
)


class DateRefreshRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=10)


@router.post("/dates/refresh")
async def refresh_dates(body: DateRefreshRequest, database: Session = Depends(get_db)):
    from app.services.date_refresh import refresh_missing

    return await refresh_missing(database, body.job_ids)


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    job_data: JobCreate,
    database: Session = Depends(get_db),
) -> Job:
    job = Job(**job_data.model_dump())
    database.add(job)

    try:
        database.commit()
        database.refresh(job)
    except IntegrityError:
        database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A job with this application URL already exists.",
        )

    return job


@router.get(
    "/recommended",
    response_model=list[JobRead],
)
def list_recommended_jobs(
    database: Session = Depends(get_db),
    minimum_score: float = Query(default=55, ge=0, le=100),
    limit: int = Query(default=25, ge=1, le=100),
) -> list[Job]:
    statement = (
        select(Job)
        .where(
            Job.match_score.is_not(None),
            Job.match_score >= minimum_score,
        )
        .order_by(Job.match_score.desc())
        .limit(limit)
    )

    return list(database.scalars(statement).all())


@router.get("", response_model=list[JobRead])
def list_jobs(
    database: Session = Depends(get_db),
) -> list[Job]:
    statement = select(Job).order_by(Job.discovered_at.desc())
    return list(database.scalars(statement).all())


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: str,
    database: Session = Depends(get_db),
) -> Job:
    job = database.get(Job, job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    return job

@router.get("/sources/summary")
def job_source_summary(database: Session = Depends(get_db)):
    """Counts per source/company, so the dashboard can filter and prune."""
    from sqlalchemy import func

    from app.models.application import Application

    rows = database.execute(
        select(
            Job.source,
            Job.company,
            func.count(Job.id),
            func.count(Application.id),
        )
        .outerjoin(Application, Application.job_id == Job.id)
        .group_by(Job.source, Job.company)
        .order_by(func.count(Job.id).desc())
    ).all()
    return {
        "groups": [
            {"source": r[0], "company": r[1], "jobs": r[2], "applications": r[3]}
            for r in rows
        ]
    }


@router.post("/bulk-delete")
def bulk_delete_jobs(
    source: str = Body(default=""),
    company: str = Body(default=""),
    database: Session = Depends(get_db),
):
    """Remove jobs you did not ask for.

    Only jobs with no application attached are deleted, so anything you have
    started or already sent is never touched.
    """
    from app.models.application import Application

    if not source and not company:
        raise HTTPException(400, "Name a source or a company to remove.")

    statement = select(Job.id).outerjoin(
        Application, Application.job_id == Job.id
    ).where(Application.id.is_(None))
    if source:
        statement = statement.where(Job.source == source)
    if company:
        statement = statement.where(Job.company == company)

    ids = list(database.scalars(statement).all())
    for job_id in ids:
        job = database.get(Job, job_id)
        if job is not None:
            database.delete(job)
    database.commit()
    return {
        "deleted": len(ids),
        "kept_with_applications": True,
        "message": f"Removed {len(ids)} jobs. Anything with an application was kept.",
    }

@router.post("/{job_id}/description")
def set_description(
    job_id: str,
    description: str = Body(default="", embed=True),
    database: Session = Depends(get_db),
):
    """Paste in a description for a posting whose text was never captured.

    Indeed's sponsored listings link through a click-tracking redirect rather than
    the posting, so their descriptions did not come across and the job could not
    be tailored against. Rather than tailor against a job title, the app says so -
    and this is the way to give it the real text.
    """
    job = database.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    text = " ".join((description or "").split())
    try:
        # Judged on what it is, not how long it is. A terse posting that states
        # its responsibilities is worth more than four hundred words of sign-in
        # page, and the old sixty-word floor accepted the second and refused the
        # first. Identity is not required here: the user can see what they copied.
        text = job_description.check(text, job.company, job.title)
    except job_description.NotAPosting as rejected:
        raise HTTPException(422, rejected.reason) from rejected
    job.description = text[:40000]
    database.commit()
    return {"id": job.id, "words": job_description.words(text),
            "description": job.description,
            "message": "Description saved. Press Apply to tailor against it."}


@router.post("/{job_id}/fetch-description")
async def fetch_description(job_id: str, database: Session = Depends(get_db)):
    """Read this posting's description from the page open in your browser.

    Uses the browser you are already signed into, on the page you are already
    entitled to read. Nothing is submitted and no form is touched.
    """
    from app.services import browser_session
    from app.sources.browser_boards import _trim_description

    job = database.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    target = job.application_url
    body_selectors = ("#jobDescriptionText", "div.jobs-description__content",
                      "div.show-more-less-html__markup", "#job-details")

    def command(worker):
        page = worker.context().new_page()
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            # Wait for a posting body to exist rather than for a flat three
            # seconds. The fixed wait was why a slow page returned its own
            # loading shell, which then read as "only 10 words".
            try:
                page.wait_for_selector(", ".join(body_selectors),
                                       timeout=15000, state="attached")
            except Exception:
                page.wait_for_timeout(2000)
            for selector in body_selectors + ("main",):
                found = page.locator(selector).first
                if found.count():
                    text = _trim_description(found.inner_text(timeout=8000))
                    if job_description.usable(text, job.company, job.title):
                        return text
            return _trim_description(page.locator("body").inner_text(timeout=8000))
        finally:
            try:
                page.close()
            except Exception:
                pass

    try:
        text = await browser_session.run(command, timeout=120)
    except Exception as error:
        raise HTTPException(
            502,
            f"Could not read that page ({type(error).__name__}). Open it yourself and "
            "paste the description instead.",
        ) from error

    try:
        # Identity is required here: unlike a paste, this followed a link, and a
        # redirect landing on a different posting is a real failure mode.
        text = job_description.check(text, job.company, job.title, require_identity=True)
    except job_description.NotAPosting as rejected:
        # A failed read never replaces a description already captured.
        raise HTTPException(422, rejected.reason) from rejected
    job.description = text[:40000]
    database.commit()
    words = job_description.words(text)
    return {"id": job.id, "words": words, "description": job.description,
            "message": f"Read {words} words. Press Apply to tailor against it."}
