"""Resume builder and library."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import BACKEND_DIR
from app.db.dependencies import get_db
from app.models.resume import ResumeDocument, ResumeKind
from app.services import apply_pipeline, file_storage, resume_library
from app.services.apply_pipeline import ProfileIncomplete
from app.services.claude_client import ClaudeUnavailable

router = APIRouter(prefix="/resumes", tags=["Resumes"])


class BuildRequest(BaseModel):
    role: str = Field(min_length=2, max_length=160)
    job_description: str = ""
    company: str = ""
    label: str = ""


class ScoreRequest(BaseModel):
    role: str = Field(min_length=2, max_length=160)
    job_description: str = ""


class UpdateRequest(BaseModel):
    label: str | None = None
    role_target: str | None = None
    is_favourite: bool | None = None


def _get(database: Session, resume_id: str) -> ResumeDocument:
    document = database.get(ResumeDocument, resume_id)
    if document is None:
        raise HTTPException(404, "Resume not found")
    return document


@router.get("")
def list_resumes(database: Session = Depends(get_db)):
    rows = database.scalars(
        select(ResumeDocument).order_by(
            ResumeDocument.is_favourite.desc(), ResumeDocument.created_at.desc()
        )
    ).all()
    return {"resumes": [resume_library.serialize(r) for r in rows]}


@router.post("/build")
async def build_resume(body: BuildRequest, database: Session = Depends(get_db)):
    """Generate a tailored, ATS-scored resume and save it to the library."""
    try:
        profile = apply_pipeline.load_profile(database)
    except ProfileIncomplete as error:
        raise HTTPException(409, str(error)) from error

    try:
        result = await asyncio.to_thread(
            resume_library.build, profile, body.role, body.job_description,
            body.company, database
        )
    except apply_pipeline.GenerationFailed as error:
        raise HTTPException(422, str(error)) from error
    except ClaudeUnavailable as error:
        raise HTTPException(503, str(error)) from error

    document = ResumeDocument(
        label=(body.label or body.role).strip()[:160],
        role_target=body.role.strip()[:160],
        kind=ResumeKind.GENERATED,
        file_path=result["file_path"],
        original_filename=Path(result["file_path"]).name,
        ats_score=result["ats_score"],
        keyword_coverage=result["keyword_coverage"],
        score_report=result["score_report"],
        analysis=result["analysis"],
        job_description=(body.job_description or "").strip()[:40000],
        company=body.company.strip()[:160],
    )
    database.add(document)
    database.commit()
    database.refresh(document)
    return {
        "resume": resume_library.serialize(document),
        "passes": result["passes"],
        "headline": result["headline"],
    }


@router.post("/upload")
async def upload_resume(
    resume: UploadFile = File(...),
    label: str = Form(""),
    role_target: str = Form(""),
    database: Session = Depends(get_db),
):
    """Add one of your own resumes to the library."""
    content = await resume.read(resume_library.MAX_UPLOAD + 1)
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        stored = resume_library.store_upload(content, resume.filename or "resume.pdf")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    document = ResumeDocument(
        label=(label or Path(resume.filename or "Resume").stem).strip()[:160],
        role_target=role_target.strip()[:160],
        kind=ResumeKind.UPLOADED,
        file_path=stored,
        original_filename=resume.filename or "",
    )
    database.add(document)
    database.commit()
    database.refresh(document)
    return {"resume": resume_library.serialize(document)}


@router.post("/{resume_id}/master")
def mark_master(resume_id: str, database: Session = Depends(get_db)):
    """Make this the resume every tailoring pass starts from."""
    try:
        document = resume_library.set_master(database, resume_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "resume": resume_library.serialize(document),
        "message": (
            f'"{document.label}" is now your master resume. Applying to a job re-words '
            "its sentences for that posting and changes nothing else."
        ),
    }


@router.get("/master/preview")
def preview_master(database: Session = Depends(get_db)):
    """What the parser sees in the master resume, line by line.

    Worth looking at once after marking a master: it shows exactly which lines
    tailoring may re-word and which are held as facts of record.
    """
    master = resume_library.master_document(database)
    if master is None:
        raise HTTPException(404, "No master resume is marked yet.")
    from app.services import master_resume

    try:
        document = master_resume.load(
            (BACKEND_DIR / master.file_path).resolve(), master.original_filename
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "label": master.label,
        "format": document.source,
        "lines": [
            {
                "index": line.index,
                "kind": line.kind,
                "editable": line.editable,
                "section": line.section,
                "text": line.text,
                "right_text": line.right_text,
                "bullet": bool(line.bullet),
            }
            for line in document.lines
        ],
    }


@router.post("/{resume_id}/score")
async def score_resume(resume_id: str, body: ScoreRequest, database: Session = Depends(get_db)):
    """ATS-check a stored resume against a posting or a role."""
    document = _get(database, resume_id)
    # The profile is what the experience-level and eligibility checks measure
    # against. An incomplete one just means those two are reported as unknown.
    try:
        profile = apply_pipeline.load_profile(database)
    except ProfileIncomplete:
        profile = {}
    try:
        result = await asyncio.to_thread(
            resume_library.score_document,
            document.file_path, document.original_filename, body.role,
            body.job_description, profile,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except ClaudeUnavailable as error:
        raise HTTPException(503, str(error)) from error

    document.ats_score = result["ats_score"]
    document.keyword_coverage = result["keyword_coverage"]
    document.score_report = result["score_report"]
    document.analysis = result["analysis"]
    document.job_description = (body.job_description or "").strip()[:40000]
    if not document.role_target:
        document.role_target = body.role.strip()[:160]
    database.commit()
    database.refresh(document)
    return {"resume": resume_library.serialize(document)}


@router.patch("/{resume_id}")
def update_resume(resume_id: str, body: UpdateRequest, database: Session = Depends(get_db)):
    document = _get(database, resume_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(document, field, value)
    database.commit()
    database.refresh(document)
    return {"resume": resume_library.serialize(document)}


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: str, database: Session = Depends(get_db)):
    document = _get(database, resume_id)
    path = (BACKEND_DIR / document.file_path).resolve()
    allowed = (BACKEND_DIR / "data" / "resumes").resolve()
    database.delete(document)
    database.commit()
    if path.is_relative_to(allowed) and path.is_file():
        path.unlink(missing_ok=True)


@router.get("/{resume_id}/download")
def download_resume(resume_id: str, database: Session = Depends(get_db)):
    document = _get(database, resume_id)
    path = (BACKEND_DIR / document.file_path).resolve()
    allowed = (BACKEND_DIR / "data" / "resumes").resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        raise HTTPException(404, "Stored file not found.")
    return FileResponse(path, media_type=file_storage.media_type(path), filename=path.name)
