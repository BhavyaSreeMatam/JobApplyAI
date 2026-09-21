"""The Apply flow: generate the tailored package, then fill the employer form."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import BACKEND_DIR
from app.db.dependencies import get_db
from app.models.application import Application, ApplicationStatus
from app.models.job import Job, JobStatus
from app.models.package import ApplicationPackage
from app.models.resume import ResumeDocument
from app.core import settings
from app.services import (
    apply_pipeline,
    autofill,
    browser_session,
    file_storage,
    resume_library,
    tailoring,
)
from app.services.apply_pipeline import (
    GenerationFailed,
    ProfileIncomplete,
    package_response,
)
from app.services.claude_client import ClaudeUnavailable

router = APIRouter(prefix="/apply", tags=["Apply"])

EDITABLE = {ApplicationStatus.PREPARING, ApplicationStatus.READY_FOR_REVIEW}


class ApplyRequest(BaseModel):
    job_id: str
    regenerate: bool = False


class MarkAppliedRequest(BaseModel):
    confirmation_id: str = ""
    notes: str = ""


class SaveAnswersRequest(BaseModel):
    answers: dict[str, str]


class UseResumeRequest(BaseModel):
    job_id: str
    resume_id: str


def _load(database: Session, application_id: str):
    application = database.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    job = database.get(Job, application.job_id)
    if job is None:
        raise HTTPException(404, "The job for this application no longer exists")
    return application, job


@router.post("/prepare")
async def prepare(body: ApplyRequest, database: Session = Depends(get_db)):
    """Analyse the job, tailor an ATS resume and cover letter. Submits nothing."""
    job = database.get(Job, body.job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not (job.description or "").strip():
        raise HTTPException(422, "This job has no description to tailor against.")

    application = database.scalar(select(Application).where(Application.job_id == job.id))
    if application is None:
        application = Application(job_id=job.id)
        database.add(application)
        job.status = JobStatus.PREPARING
        database.commit()
        database.refresh(application)
    elif application.status not in EDITABLE:
        raise HTTPException(
            409,
            f"This application is '{application.status.value}'. Reset it to draft before regenerating.",
        )

    existing = database.get(ApplicationPackage, application.id)
    if existing and not body.regenerate:
        if apply_pipeline.package_is_current(database, existing, job):
            return package_response(existing, application, job)
        # The profile, the master resume, the posting or a document setting has
        # changed since this was built, so the stored PDFs no longer reflect their
        # inputs. Rebuild rather than hand back a document that looks current.

    try:
        package = await apply_pipeline.build_package(database, application, job)
    except ProfileIncomplete as error:
        raise HTTPException(409, str(error)) from error
    except GenerationFailed as error:
        # Nothing is stored and nothing half-made is returned: an unverified
        # document must never reach an employer.
        raise HTTPException(422, str(error)) from error
    except ClaudeUnavailable as error:
        raise HTTPException(503, str(error)) from error

    database.refresh(application)
    return package_response(package, application, job)


@router.get("/{application_id}")
def get_package(application_id: str, database: Session = Depends(get_db)):
    application, job = _load(database, application_id)
    package = database.get(ApplicationPackage, application_id)
    if package is None:
        raise HTTPException(404, "No generated package yet - run Apply on this job first.")
    payload = package_response(package, application, job)
    payload["stale"] = not apply_pipeline.package_is_current(database, package, job)
    if payload["stale"]:
        payload["stale_reason"] = (
            "Your profile, master resume, this posting or a document setting has "
            "changed since these were generated. Press Regenerate for documents "
            "that match."
        )
    return payload


@router.post("/{application_id}/autofill")
async def run_autofill(application_id: str, database: Session = Depends(get_db)):
    """Open the employer form in your browser and fill it. Never submits."""
    application, job = _load(database, application_id)
    package = database.get(ApplicationPackage, application_id)
    if package is None:
        raise HTTPException(409, "Generate the application package first.")
    if not application.resume_path:
        raise HTTPException(409, "No tailored resume on this application.")

    try:
        profile = apply_pipeline.load_profile(database)
    except ProfileIncomplete as error:
        raise HTTPException(409, str(error)) from error

    try:
        report = await autofill.open_and_fill(
            job.application_url,
            profile,
            application.answers_json or {},
            application.resume_path,
            application.cover_letter_path,
            job.location or "",
            settings.load()["auto_accept_agreements"],
            application_id=application.id,
        )
    except browser_session.Busy as error:
        raise HTTPException(409, str(error)) from error
    except browser_session.Cancelled as error:
        raise HTTPException(499, str(error)) from error
    except autofill.AmbiguousPage as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(502, f"Could not open or fill the form: {type(error).__name__}: {error}") from error

    package.autofilled_at = datetime.now(timezone.utc)
    package.autofill_report = report
    database.commit()
    return report


@router.post("/{application_id}/stop")
def stop_browser_work(application_id: str):
    """Ask a running fill to stop at its next action.

    Whatever it had already entered stays on the page - stopping is not undoing.
    Without this, a fill that hit a slow site ran on unattended after the request
    that started it had already timed out.
    """
    stopped = browser_session.cancel(application_id)
    return {
        "stopped": stopped,
        "message": (
            "Stopping at the next field. Anything already filled is still there."
            if stopped else "Nothing is running for this application."
        ),
    }


@router.post("/{application_id}/mark-applied")
def mark_applied(application_id: str, body: MarkAppliedRequest, database: Session = Depends(get_db)):
    """Record that YOU submitted it. Nothing sets this automatically."""
    application, _ = _load(database, application_id)
    application.status = ApplicationStatus.APPLIED
    application.applied_at = datetime.now(timezone.utc)
    if body.confirmation_id:
        application.confirmation_id = body.confirmation_id.strip()[:255]
    database.commit()
    return {"application_id": application.id, "status": application.status.value,
            "applied_at": application.applied_at}


@router.post("/{application_id}/reset")
def reset(application_id: str, database: Session = Depends(get_db)):
    application, _ = _load(database, application_id)
    application.status = ApplicationStatus.PREPARING
    application.applied_at = None
    application.confirmation_id = None
    database.commit()
    return {"application_id": application.id, "status": application.status.value}


@router.get("/{application_id}/resume")
def download_resume(application_id: str, database: Session = Depends(get_db)):
    return _document(database, application_id, "resume")


@router.get("/{application_id}/cover-letter")
def download_cover_letter(application_id: str, database: Session = Depends(get_db)):
    return _document(database, application_id, "cover_letter")


def _document(database: Session, application_id: str, kind: str):
    application = database.get(Application, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    relative = application.resume_path if kind == "resume" else application.cover_letter_path
    if not relative:
        raise HTTPException(404, f"No {kind.replace('_', ' ')} generated for this application.")
    path = (BACKEND_DIR / relative).resolve()
    allowed = (BACKEND_DIR / "data").resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        raise HTTPException(404, "Stored document not found.")
    # Taken from the file rather than assumed. A master resume kept as a .docx is
    # tailored and returned as a .docx, and serving that as application/pdf hands
    # the employer a file that will not open.
    return FileResponse(
        path,
        media_type=file_storage.media_type(path),
        filename=path.name,
    )


@router.post("/{application_id}/draft-answers")
async def draft_answers(application_id: str, database: Session = Depends(get_db)):
    """Find this employer's own questions and draft grounded replies for review."""
    application, job = _load(database, application_id)
    package = database.get(ApplicationPackage, application_id)
    if package is None:
        raise HTTPException(409, "Generate the application package first.")

    try:
        profile = apply_pipeline.load_profile(database)
    except ProfileIncomplete as error:
        raise HTTPException(409, str(error)) from error

    saved = application.answers_json or {}
    try:
        questions = await autofill.collect_open_questions(
            job.application_url, profile, saved
        )
    except Exception as error:
        raise HTTPException(
            502, f"Could not read the form: {type(error).__name__}: {error}"
        ) from error

    if not questions:
        # An aggregator link often lands on the listing rather than the employer's
        # form, in which case there is nothing to read.
        via_aggregator = any(
            host in (job.application_url or "")
            for host in ("indeed.com", "linkedin.com", "joinhandshake.com", "ziprecruiter")
        )
        return {
            "application_id": application.id,
            "questions": [],
            "drafted": [],
            "message": (
                "No application form found at this link - it points at the "
                f"{job.source} listing, not the employer's form. Open the job, follow "
                "it through to the real application, then paste that URL into "
                "\"Apply to one job\" on the Sources tab."
                if via_aggregator else
                "No employer-specific questions found beyond what your profile already answers."
            ),
        }

    # Claude drafts prose answers. Dropdown questions ("Have you interviewed here
    # before?") are facts only the applicant knows, so they are surfaced with
    # their real options to pick from rather than guessed at.
    text_questions = [q for q in questions if not q.get("options")]
    choice_questions = [q for q in questions if q.get("options")]

    try:
        drafted = await asyncio.to_thread(
            tailoring.draft_answers, profile, job, package.analysis or {}, text_questions
        ) if text_questions else []
    except ClaudeUnavailable as error:
        raise HTTPException(503, str(error)) from error

    by_name = {q["field_name"]: q for q in questions}
    for item in drafted:
        item["options"] = []
        item["legal"] = by_name.get(item["field_name"], {}).get("legal", False)
    for question in choice_questions:
        drafted.append({
            "field_name": question["field_name"],
            "question": question["question"],
            "answer": "",
            "needs_you": True,
            "reason": ("Read this and decide yourself - it is a legal agreement."
                       if question.get("legal")
                       else "Only you know this; pick an option and it will be saved."),
            "options": question["options"],
            "legal": question.get("legal", False),
        })

    # Carry any answer already saved for a field into the draft, so edits stick.
    for item in drafted:
        existing = saved.get(item["field_name"])
        if isinstance(existing, str) and existing.strip():
            item["answer"] = existing
            item["reason"] = "Your saved answer for this field."
            item["needs_you"] = False

    return {
        "application_id": application.id,
        "questions": questions,
        "drafted": drafted,
        "message": f"Drafted {sum(1 for d in drafted if not d['needs_you'])} of "
                   f"{len(drafted)} questions. Review, edit, then save.",
    }


@router.post("/{application_id}/answers")
def save_answers(application_id: str, body: SaveAnswersRequest,
                 database: Session = Depends(get_db)):
    """Store reviewed answers so the next autofill run uses them."""
    application, _ = _load(database, application_id)
    if application.status not in EDITABLE:
        raise HTTPException(409, "Reset this application to draft before editing answers.")
    merged = dict(application.answers_json or {})
    merged.update({k: v for k, v in body.answers.items() if isinstance(v, str)})
    application.answers_json = merged
    database.commit()
    return {"application_id": application.id, "answers": merged, "saved": len(body.answers)}


@router.post("/use-resume")
def use_library_resume(body: UseResumeRequest, database: Session = Depends(get_db)):
    """Attach a resume from the library, creating the application if needed.

    Keyed by job rather than application so it works on a job you have not
    started - which is exactly when skipping generation is most useful.
    """
    job = database.get(Job, body.job_id)
    if job is None:
        raise HTTPException(404, "Job not found")

    document = database.get(ResumeDocument, body.resume_id)
    if document is None:
        raise HTTPException(404, "That resume is not in your library.")

    application = database.scalar(select(Application).where(Application.job_id == job.id))
    if application is None:
        application = Application(job_id=job.id)
        database.add(application)
        database.commit()
        database.refresh(application)
    elif application.status not in EDITABLE:
        raise HTTPException(409, "Reset this application to draft before changing its resume.")

    try:
        # The application keeps its own copy, so editing or deleting the library
        # entry later cannot change what was attached to a sent application.
        application.resume_path = resume_library.copy_for_application(document.file_path)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error

    document.times_used += 1
    application.status = ApplicationStatus.READY_FOR_REVIEW
    job.status = JobStatus.READY_FOR_REVIEW

    package = database.get(ApplicationPackage, application.id) or ApplicationPackage(
        application_id=application.id
    )
    # The library resume's stored score belongs to whatever job it was last
    # measured against, which is usually a different one. Copying it here put a
    # confident number from another posting next to this application. It is
    # recomputed against this job instead, from the file that was just attached.
    from app.services import job_match

    profile_for_score = {}
    try:
        profile_for_score = apply_pipeline.load_profile(database)
    except ProfileIncomplete:
        pass

    attached = (BACKEND_DIR / application.resume_path).resolve()
    analysis = document.analysis or {}
    if analysis and attached.is_file():
        score = job_match.evaluate(
            resume_library.extract_text(attached, document.original_filename),
            analysis,
            attached if attached.suffix.casefold() == ".pdf" else None,
            profile_for_score,
            job.location or "",
        )
        package.ats_score = score["overall"]
        package.keyword_coverage = score["keyword"]["coverage_percent"]
        package.score_report = score
    else:
        # No analysis of THIS posting, so there is no honest number to show.
        package.ats_score = 0.0
        package.keyword_coverage = 0.0
        package.score_report = {}
    package.analysis = analysis

    # A cover letter written for the previous resume no longer matches this one.
    # Keeping it paired an argument with a document that does not support it.
    replaced_letter = bool(application.cover_letter_path)
    application.cover_letter_path = None
    package.cover_letter_body = ""

    package.tailored_resume = {}
    package.source_fingerprint = ""   # attached by hand; regenerate to re-verify
    package.model_used = "library"
    database.add(package)
    database.commit()
    database.refresh(application)
    database.refresh(package)
    return {
        "application_id": application.id,
        "resume_path": application.resume_path,
        "used": resume_library.serialize(document),
        "note": (
            "Attached from your library and scored against this posting."
            + (" The previous cover letter was removed - it was written for a "
               "different resume." if replaced_letter else "")
        ),
    }


@router.post("/{application_id}/autofill-current")
async def autofill_current_page(application_id: str, database: Session = Depends(get_db)):
    """Fill the page already open in the browser, wherever you have navigated to.

    For applications behind a sign-in (Workday, Taleo) or split across steps, the
    real form is several clicks past where a single navigate-and-fill can reach.
    Sign in yourself, then run this on each step.
    """
    application, job = _load(database, application_id)
    package = database.get(ApplicationPackage, application_id)
    try:
        profile = apply_pipeline.load_profile(database)
    except ProfileIncomplete as error:
        raise HTTPException(409, str(error)) from error

    from urllib.parse import urlparse

    expect_host = urlparse(job.application_url or "").netloc
    try:
        report = await autofill.fill_open_page(
            profile,
            application.answers_json or {},
            application.resume_path,
            application.cover_letter_path,
            job.location or "",
            settings.load()["auto_accept_agreements"],
            application_id=application.id,
            expect_host=expect_host,
        )
    except browser_session.Busy as error:
        raise HTTPException(409, str(error)) from error
    except browser_session.Cancelled as error:
        raise HTTPException(499, str(error)) from error
    except autofill.AmbiguousPage as error:
        # The open tab is not this application's. Nothing was typed, and the
        # applicant is told which tab to bring forward rather than having one
        # application's answers put into another's form.
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        raise HTTPException(502, f"{type(error).__name__}: {error}") from error

    if package is not None:
        package.autofilled_at = datetime.now(timezone.utc)
        package.autofill_report = report
        database.commit()
    return report
