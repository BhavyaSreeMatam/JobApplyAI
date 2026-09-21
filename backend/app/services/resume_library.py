"""Building, scoring and storing reusable resumes.

Two ways a resume lands in the library:
  - generated here from the candidate profile against a job description or a role
  - uploaded by the user, then scored against whatever target they name

Both are scored identically: the PDF is parsed back out and measured the way an
ATS would read it, so an uploaded resume and a generated one are comparable.
"""
import re
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.db.database import BACKEND_DIR, DATA_DIR
from app.schemas.tailoring import JobAnalysis
from app.services import ats_scorer, claude_client, job_match, resume_pdf, tailoring

UPLOAD_DIR = DATA_DIR / "resumes"
MAX_UPLOAD = 8 * 1024 * 1024

ROLE_ANALYST_SYSTEM = """You describe what an applicant tracking system screens for in a
given job title, with no specific posting to work from.

Produce the terms a typical resume screen for this role matches on: languages,
frameworks, platforms, tools, methods and domain nouns. Mark importance by how
commonly the role demands each one - `required` for the near-universal core,
`preferred` for the common, `nice_to_have` for the rest.

Never emit soft skills or generic filler. Prefer specific technologies over
categories. Write hard_requirements only where they are near-universal for the
role (for example a specific degree for a research scientist).

`responsibilities` should be the work this role typically does, one short line
each, 5-10 lines - what the person builds and owns, not qualities they have.

`eligibility` must be EMPTY unless the role itself carries a legal or licensing
condition that holds everywhere (a clearance for a defence role, a licence for a
clinical one). With no posting in front of you there is nothing else to know, and
inventing a location or authorisation requirement would be a guess."""


def job_like(title: str, description: str = "", company: str = "") -> SimpleNamespace:
    """A minimal stand-in for a Job row, for the tailoring pipeline."""
    return SimpleNamespace(
        title=title or "Target role",
        company=company or "",
        location="",
        description=description or "",
    )


def analyze_target(title: str, description: str) -> dict:
    """Keywords for a real posting, or for a bare role title."""
    if (description or "").strip():
        return tailoring.analyze_job(job_like(title, description))

    analysis = claude_client.parse(
        system=ROLE_ANALYST_SYSTEM,
        user=f"Role title: {title}\n\nDescribe what this role's ATS screen matches on.",
        output_format=JobAnalysis,
        effort="medium",
        max_tokens=8000,
    )
    return analysis.model_dump()


def build(profile: dict, title: str, description: str = "", company: str = "",
          database=None) -> dict:
    """Generate a tailored resume PDF. Returns everything needed to store it.

    The same pipeline the Apply button uses. This used to call the older
    preserve-everything path, so the standalone builder produced a resume under
    different rules from the one an application would send - and it did so even
    for candidates who had marked a master resume, which it ignored entirely.
    """
    from app.services import apply_pipeline, master_resume

    job = job_like(title, description, company)
    analysis = analyze_target(title, description)

    master = master_document(database) if database is not None else None
    source = (
        master_resume.SourceDocument.from_library(master)
        if master is not None
        else master_resume.SourceDocument.from_profile_record(profile)
    )
    result = apply_pipeline.tailor_document(source, profile, job, analysis)
    return {
        "file_path": result["resume_path"],
        "analysis": analysis,
        "score_report": result["score"],
        "ats_score": result["score"]["overall"],
        "keyword_coverage": result["score"]["keyword"]["coverage_percent"],
        "passes": 1,
        "headline": result["plan"]["notes"][:120],
        "from_master": source.label,
        "unsupported_requirements": result["plan"]["unsupported_requirements"],
    }


def extract_text(path: Path, original_name: str = "") -> str:
    """Read an uploaded document the way a parser would."""
    suffix = (Path(original_name or path).suffix or "").casefold()
    if suffix == ".docx":
        from app.services import docx_text

        return docx_text.extract(path)
    if suffix in {".txt", ".md"}:
        return path.read_text("utf-8", errors="replace")
    return ats_scorer.extract_pdf_text(path)


def store_upload(content: bytes, original_name: str) -> str:
    """Save an uploaded resume next to the generated ones."""
    suffix = (Path(original_name).suffix or ".pdf").casefold()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise ValueError("Upload a PDF, DOCX, TXT or MD file.")
    if len(content) > MAX_UPLOAD:
        raise ValueError("File cannot exceed 8 MB.")
    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise ValueError("That file is not a valid PDF.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "-", Path(original_name).stem).strip("-")[:70] or "resume"
    name = f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
    (UPLOAD_DIR / name).write_bytes(content)
    return "data/resumes/" + name


def score_document(relative_path: str, original_name: str, title: str,
                   description: str, profile: dict | None = None) -> dict:
    """ATS-check any stored resume against a posting or a role."""
    path = (BACKEND_DIR / relative_path).resolve()
    if not path.is_file():
        raise ValueError("That resume file is missing from disk.")

    analysis = analyze_target(title, description)
    text = extract_text(path, original_name)
    if len(text.strip()) < 150:
        raise ValueError(
            "Could not read text from that file. If it is a scanned image, export a "
            "text-based PDF - an ATS cannot read it either, which is itself the problem."
        )
    report = job_match.evaluate(
        text, analysis, path if path.suffix.casefold() == ".pdf" else None, profile or {}
    )
    return {"analysis": analysis, "score_report": report,
            "ats_score": report["overall"],
            "keyword_coverage": report["keyword"]["coverage_percent"]}


def copy_for_application(relative_path: str) -> str:
    """Give an application its own copy, so later edits cannot change what was sent."""
    source = (BACKEND_DIR / relative_path).resolve()
    if not source.is_file():
        raise ValueError("That resume file is missing from disk.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{source.stem}_{uuid.uuid4().hex[:6]}{source.suffix}"
    shutil.copy2(source, target)
    return "data/resumes/" + target.name


def serialize(document) -> dict:
    report = document.score_report or {}
    keyword = report.get("keyword", {}) if isinstance(report, dict) else {}
    return {
        "id": document.id,
        "label": document.label,
        "role_target": document.role_target,
        "kind": document.kind.value,
        "filename": Path(document.file_path).name,
        "original_filename": document.original_filename,
        "ats_score": document.ats_score,
        "keyword_coverage": document.keyword_coverage,
        "matched": keyword.get("matched", [])[:40],
        "missing": [m.get("term", "") for m in (keyword.get("missing", []) or [])][:25],
        "issues": report.get("issues", []) if isinstance(report, dict) else [],
        "company": document.company,
        "has_description": bool((document.job_description or "").strip()),
        "times_used": document.times_used,
        "is_master": bool(document.is_master),
        "is_favourite": document.is_favourite,
        "created_at": document.created_at,
    }


def master_document(database):
    """The resume tailoring starts from, or None when none is marked."""
    from sqlalchemy import select

    from app.models.resume import ResumeDocument

    return database.scalars(
        select(ResumeDocument).where(ResumeDocument.is_master.is_(True))
    ).first()


def set_master(database, resume_id: str):
    """Mark one document as the master, clearing the flag from any other.

    Exactly one at a time: two masters would mean the tailored resume depended
    on row order, which is the kind of thing that works until it does not.
    """
    from sqlalchemy import select, update

    from app.models.resume import ResumeDocument

    document = database.get(ResumeDocument, resume_id)
    if document is None:
        raise ValueError("That resume is not in your library.")
    suffix = (Path(document.original_filename or document.file_path).suffix or "").casefold()
    if suffix not in {".pdf", ".docx"}:
        raise ValueError(
            "A master resume has to be a PDF or a DOCX - those are the only formats "
            "that carry the styling tailoring has to preserve."
        )
    database.execute(update(ResumeDocument).values(is_master=False))
    document.is_master = True
    database.commit()
    database.refresh(document)
    return document
