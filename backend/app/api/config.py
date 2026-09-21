"""Settings and resume-import endpoints."""
import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import settings
from app.db.dependencies import get_db
from app.models.candidate import CandidateProfile
from app.schemas.candidate import ProfileData
from app.services import claude_client, profile_merge, tailoring
from app.services.claude_client import ClaudeUnavailable

router = APIRouter(prefix="/config", tags=["Configuration"])

MAX_UPLOAD = 8 * 1024 * 1024


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = None
    model: str | None = None
    effort: str | None = None
    ats_target_score: int | None = None
    max_tailor_passes: int | None = None
    generate_cover_letter: bool | None = None
    auto_accept_agreements: bool | None = None
    adopt_missing_skills: bool | None = None
    resume_page_target: int | None = None
    browser_channel: str | None = None
    target_roles: list[str] | None = None
    search_terms: list[str] | None = None
    excluded_terms: list[str] | None = None
    preferred_locations: list[str] | None = None


@router.get("")
def get_settings():
    return settings.redacted()


@router.put("")
def update_settings(body: SettingsUpdate):
    changes = body.model_dump(exclude_none=True)
    if "effort" in changes and changes["effort"] not in {"low", "medium", "high", "xhigh", "max"}:
        raise HTTPException(400, "effort must be low, medium, high, xhigh or max")
    if "ats_target_score" in changes and not 50 <= changes["ats_target_score"] <= 100:
        raise HTTPException(400, "ats_target_score must be between 50 and 100")
    if "max_tailor_passes" in changes and not 1 <= changes["max_tailor_passes"] <= 4:
        raise HTTPException(400, "max_tailor_passes must be between 1 and 4")
    if "resume_page_target" in changes and not 1 <= changes["resume_page_target"] <= 3:
        raise HTTPException(400, "resume_page_target must be 1, 2 or 3")
    if "browser_channel" in changes and changes["browser_channel"] not in {"chrome", "msedge", "chromium"}:
        raise HTTPException(400, "browser_channel must be chrome, msedge or chromium")
    if "anthropic_api_key" in changes:
        changes["anthropic_api_key"] = changes["anthropic_api_key"].strip()
    settings.save(changes)
    return settings.redacted()


@router.post("/test-key")
def test_key():
    """Confirm the configured key actually works, with a minimal call."""
    try:
        response = claude_client.client().messages.create(
            model=settings.load()["model"],
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except ClaudeUnavailable as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise HTTPException(400, f"{type(error).__name__}: {error}") from error
    text = next((b.text for b in response.content if b.type == "text"), "")
    return {
        "ok": True,
        "model": response.model,
        "reply": text.strip()[:40],
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def _read_document(upload: UploadFile, content: bytes) -> str:
    suffix = Path(upload.filename or "").suffix.casefold()
    if suffix == ".pdf":
        import pdfplumber

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(content)
            temporary = handle.name
        try:
            with pdfplumber.open(temporary) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if suffix == ".docx":
        from app.services import docx_text

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
            handle.write(content)
            temporary = handle.name
        try:
            # Tables as well as paragraphs: a resume laid out in a two-column
            # table returns almost nothing from `.paragraphs` alone.
            return docx_text.extract(temporary)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if suffix in {".txt", ".md"}:
        return content.decode("utf-8", errors="replace")
    raise HTTPException(400, "Upload a PDF, DOCX, TXT or MD file.")


@router.post("/import-resume")
async def import_resume(
    resume: UploadFile = File(...),
    profile_json: str | None = Form(None),
    revision: int | None = Form(None),
    database: Session = Depends(get_db),
):
    """Preview a merge into the on-screen draft; only Save profile persists it."""
    if (profile_json is None) != (revision is None):
        raise HTTPException(422, "Send both the profile draft and its revision.")
    profile_row = database.get(CandidateProfile, "local")
    current_revision = profile_row.revision if profile_row else 0
    current = dict(profile_row.data) if profile_row and profile_row.data else {}
    if revision is not None:
        if revision != current_revision:
            raise HTTPException(
                409, "Profile changed in another window. Keep a copy of your unsaved "
                "edits, then reload the profile before importing.",
            )
        try:
            current = ProfileData.model_validate_json(profile_json).model_dump(
                mode="json", exclude_unset=True,
            )
        except ValidationError as error:
            raise HTTPException(422, "The profile draft is invalid. Check its fields before importing.") from error
    # Do not hold a database transaction open during document parsing or an AI call.
    database.rollback()
    content = await resume.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(400, "File cannot exceed 8 MB.")
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")

    text = await asyncio.to_thread(_read_document, resume, content)
    if len(text.strip()) < 120:
        raise HTTPException(
            422,
            "Could not read enough text from that file. If it is a scanned image, "
            "export a text-based PDF or paste the content into a .txt file.",
        )

    try:
        parsed = await asyncio.to_thread(tailoring.parse_resume_text, text)
    except ClaudeUnavailable as error:
        raise HTTPException(503, str(error)) from error

    profile_row = database.get(CandidateProfile, "local")
    if (profile_row.revision if profile_row else 0) != current_revision:
        raise HTTPException(
            409, "Profile changed while the resume was being read. Keep a copy of "
            "your unsaved edits, then reload the profile before importing again.",
        )

    # The model returns one flat `entries` list; the stored profile keeps a list
    # per section, so fan it back out here.
    from app.services.workday import split_degree

    incoming = parsed.model_dump(exclude={"entries"})
    for section in ("experience", "projects", "education", "publications"):
        rows = []
        # `entry_row`, not `row`. Naming this `row` shadowed the profile record
        # fetched above, so any resume that contained a single experience,
        # education, project or publication ended the request by calling
        # `.revision` on a dict - an unconditional crash on the common case.
        for entry in parsed.section(section):
            entry_row = {k: v for k, v in entry.model_dump().items() if k != "section"}
            if section == "education":
                # Applications ask for degree and major separately, so split the
                # parsed heading once here rather than guessing at fill time.
                degree, major = split_degree(entry_row.get("heading", ""))
                entry_row["degree"], entry_row["major"] = degree, major
            rows.append(entry_row)
        incoming[section] = rows

    # Entries are matched by identity and merged, never replaced wholesale: a
    # resume that omits a job is evidence about the resume, not about the job.
    merged, conflicts = profile_merge.merge_profile(current, incoming)

    counts = {section: len(incoming.get(section) or []) for section in profile_merge.SECTIONS}
    empty = [section for section, total in counts.items() if not total]
    coverage = (
        "Nothing was read for: " + ", ".join(empty) + ". Check the document has those "
        "sections before saving." if empty else ""
    )

    return {
        "parsed": incoming,
        "merged_preview": merged,
        "conflicts": conflicts,
        "counts": counts,
        "coverage_warning": coverage,
        "current_revision": current_revision,
        "characters_read": len(text),
        "message": (
            "Review the imported details, then press Save profile to keep them."
            + (f" {len(conflicts)} value(s) differ from what you already had - yours "
               "were kept." if conflicts else "")
        ),
    }
