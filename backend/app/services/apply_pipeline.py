"""One-click apply: analyse the job, tailor an ATS resume, write a cover letter.

This is the orchestration behind the Apply button. It never submits anything -
it produces the documents and the filled-answer set, then hands off to the
browser for the user's own review and Submit click.
"""
import asyncio
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import settings
from app.models.application import Application, ApplicationStatus
from app.models.candidate import CandidateProfile
from app.models.job import Job, JobStatus
from app.models.package import ApplicationPackage
from app.services import job_description, profile_merge, resume_pdf, tailoring
from app.services.claude_client import ClaudeUnavailable

REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email")


class ProfileIncomplete(RuntimeError):
    pass


def load_profile(database: Session) -> dict:
    row = database.get(CandidateProfile, "local")
    if row is None or not row.data:
        raise ProfileIncomplete(
            "Save your candidate profile first - open Profile and either upload an "
            "existing resume to auto-fill it, or enter your details."
        )
    data = row.data
    missing = [f for f in REQUIRED_PROFILE_FIELDS if not data.get(f)]
    if missing:
        raise ProfileIncomplete(
            "Profile is missing: " + ", ".join(f.replace("_", " ") for f in missing)
        )
    if not (data.get("experience") or data.get("projects")):
        raise ProfileIncomplete(
            "Add at least one experience or project to your profile - the resume is "
            "built only from facts you have verified there."
        )
    return profile_merge.evidenced_profile(data)


def record_adopted_skills(database: Session, adopted: list[str]) -> None:
    """Write skills picked up from a job description back into the profile.

    They remain pending suggestions until the candidate confirms them or imports
    a resume that contains them. They cannot support a generated claim yet.
    """
    row = database.get(CandidateProfile, "local")
    if row is None or not row.data:
        return
    data = dict(row.data)
    have = {str(s).casefold().strip() for s in (data.get("skills") or [])}
    fresh = [s for s in adopted if s.casefold().strip() not in have]
    if not fresh:
        return
    data["skills"] = list(data.get("skills") or []) + fresh
    data["adopted_skills"] = list(data.get("adopted_skills") or []) + fresh
    row.data = data
    row.revision += 1
    # `data` is a JSON column; SQLAlchemy cannot see an in-place mutation, and
    # reassigning a plain dict to it is not enough on its own for every backend.
    flag_modified(row, "data")
    database.add(row)
    database.commit()


def _lines_over(path, page_target: int) -> int:
    """How many logical lines to drop to lose the overflow, measured not guessed.

    Counts the text rows that spilled past the target and halves them, because a
    dropped line usually takes a wrapped row with it. Converging in small steps
    costs a few renders and keeps the evidence that was going to be thrown away.
    """
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            rows = sum(
                len({round(char["bottom"]) for char in page.chars if char["text"].strip()})
                for page in pdf.pages[page_target:]
            )
    except Exception:
        return 1
    return max(1, -(-rows // 2))


def _pdf_pages(path) -> int:
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


class GenerationFailed(RuntimeError):
    """The documents could not be produced to standard within the retry budget."""


PLACEHOLDER_DESCRIPTION = re.compile(
    r"see (the )?(company )?(website|posting)|description (not available|unavailable|"
    r"coming soon)|no description|^\s*n/?a\s*$|lorem ipsum|click (here|apply) to",
    re.I,
)


def source_identity(master) -> str:
    """A stable name for whichever document a resume was built from."""
    if master is None:
        return "profile"
    return (
        getattr(master, "file_path", "")
        or getattr(master, "label", "")
        or "profile"
    )


def source_fingerprint(profile: dict, job, master, config: dict) -> str:
    """A short hash of everything a generated document depends on.

    Regenerating is the only way to make a stored resume reflect an edited
    profile, and there was nothing recording that it needed to happen. An
    application prepared before the candidate fixed a job title kept its old PDF,
    and the interface said "ready for review" about a document built from data
    that no longer existed.
    """
    import hashlib
    import json

    material = json.dumps(
        {
            "profile": {k: v for k, v in sorted((profile or {}).items())
                        if k not in {"provenance"}},
            "job": {
                "title": getattr(job, "title", ""),
                "company": getattr(job, "company", ""),
                "description": getattr(job, "description", "") or "",
            },
            # The identity of the source document, derived the same way on both
            # sides. Fingerprinting the object itself did not work: generation
            # passes a SourceDocument and the staleness check passes the library
            # record, so the two never matched and every package read as stale.
            "master": source_identity(master),
            "settings": {
                key: config.get(key) for key in
                ("model", "effort", "resume_page_target", "generate_cover_letter",
                 "adopt_missing_skills")
            },
        },
        sort_keys=True, default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def package_is_current(database: Session, package, job) -> bool:
    """Whether a stored package still matches the inputs it was built from."""
    if package is None:
        return False
    stored = getattr(package, "source_fingerprint", "") or ""
    if not stored:
        return True  # built before fingerprints existed; do not churn silently
    from app.services import resume_library

    try:
        profile = load_profile(database)
    except ProfileIncomplete:
        return False
    master = resume_library.master_document(database)
    return stored == source_fingerprint(profile, job, master, settings.load())


def reject_thin_description(job) -> None:
    """Refuse to tailor against a posting that does not say anything.

    A listing that was never captured properly - a redirect stub, "see our
    website", a title and nothing else - still produced a confident resume, a
    cover letter about the company, and a match score. All three were built on
    the job title and whatever the model filled in around it. Saying so is more
    use than producing a document nobody should send.
    """
    text = " ".join(str(getattr(job, "description", "") or "").split())
    if PLACEHOLDER_DESCRIPTION.search(text):
        raise GenerationFailed(
            "The stored description is a placeholder, not the posting. Open the job "
            "and use \"Read it in my browser\", or paste the description in."
        )
    try:
        # One judgement, shared with the capture and paste paths, so the same
        # text is not accepted at one door and refused at another. A concise but
        # real posting passes; a sign-in wall of any length does not.
        job_description.check(
            text,
            str(getattr(job, "company", "") or ""),
            str(getattr(job, "title", "") or ""),
        )
    except job_description.NotAPosting as rejected:
        raise GenerationFailed(
            f"{rejected.reason} Open the job and use \"Read it in my browser\", "
            "or paste the description in."
        ) from rejected


# How many times a failed check may be corrected before giving up. Each round is
# deterministic - reverting a line, trimming a bullet - so this costs no calls.
# Rendering and trimming are free, so the budget is set by how many small steps
# it can take to converge rather than by cost.
MAX_REVISIONS = 10


def tailor_document(source, profile: dict, job, analysis: dict | None = None) -> dict:
    """The one public way to produce a tailored resume, for any caller.

    Apply and the standalone Resumes builder both come through here, so a resume
    built in one place is built under the same rules as one built in the other.
    """
    return _tailor_from_master(source, profile, job, settings.load(), analysis)


def _tailor_from_master(master, profile: dict, job: Job, config: dict,
                        analysis: dict | None = None) -> dict:
    """Select, shorten and re-word the candidate's own resume for this job.

    The master resume is a superset written for every role; this produces the
    subset that argues for one. Selection, grounding checks and layout checks all
    happen here - nothing is surfaced for approval, and the master file on disk is
    never touched.

    The shape of the loop matters. One model call decides what stays, and every
    correction after that is deterministic: a line whose new wording outran its
    source is reverted to the source, and a resume over its page target loses its
    lowest-priority bullets. So a document that needs four rounds of fixing costs
    exactly the same as one that needs none.
    """
    from app.db.database import DATA_DIR
    from app.services import (
        document_check,
        job_match,
        master_resume,
        resume_library,
        tailoring,
    )

    document = master.open()
    analysis = analysis or tailoring.analyze_job(job)
    page_target = max(1, int(config.get("resume_page_target") or 1))

    adopted = []
    if config["adopt_missing_skills"]:
        before = job_match.evaluate(document.plain_text(), analysis, profile=profile)
        adopted = _confirmed_gap_skills(before["keyword"]["missing"], profile, document)

    # Captured before anything is edited: what the candidate can actually
    # evidence, which is the only thing a re-worded line may claim. The adopted
    # skills belong in here too - the candidate has confirmed those, and without
    # them the grounding check reverts the very additions it was asked to make.
    verified = document_check.verified_terms(document, profile)
    verified |= document_check.vocabulary_of(adopted)
    master_text = document.plain_text()

    # Coverage and protection are measured against the resume as it arrived,
    # before the model has removed anything. Computing them afterwards meant the
    # baseline already had the publication missing, so nothing could notice it
    # had gone - protection only ever applied to what selection had spared.
    from app.services import evidence

    baseline_coverage = evidence.coverage(document, analysis)
    baseline_protected = evidence.protected(document, analysis)

    plan = tailoring.select_for_job(document, job, analysis, page_target, adopted)
    document.apply_plan(plan["decisions"])

    # Anything the plan dropped that was the only support for a stated
    # requirement, or that is evidence of a kind too expensive to lose, comes
    # straight back. The model proposes; the requirement coverage decides.
    reinstated = [
        index for index in evidence.lost_requirements(document, analysis, baseline_coverage)
        if index in baseline_protected
    ]
    for index in reinstated:
        document.restore(index)

    stem = DATA_DIR / "resumes" / resume_pdf.document_name(profile, job, "Resume")
    written, history = _render_verified(
        document, stem, profile, page_target, verified, document_check, analysis,
        baseline_coverage, baseline_protected,
    )
    if reinstated:
        history.insert(0, {"attempt": 0, "reinstated": reinstated})
    relative = "data/resumes/" + written.name

    resume_text = resume_library.extract_text(written, written.name)
    score = job_match.evaluate(
        resume_text, analysis, written if written.suffix == ".pdf" else None,
        profile, job.location or "",
    )
    kept = [line for line in document.live if line.kind in master_resume.SELECTABLE_KINDS]
    return {
        "analysis": analysis,
        "resume_path": relative,
        "resume_text": resume_text,
        # The master as parsed, so the cover letter can be checked against every
        # fact the candidate has rather than only the ones that survived trimming.
        "master_text": master_text,
        "score": score,
        "plan": plan,
        "history": history,
        "lines_kept": len(kept),
        "lines_total": len(document.evidence()),
        "pages": _pdf_pages(written),
        # Only what actually survived into the document is claimed as adopted.
        "adopted": [
            skill for skill in (plan["skills_added"] or [])
            if skill.casefold() in resume_text.casefold()
        ],
    }


def _confirmed_gap_skills(missing, profile: dict, document) -> list[str]:
    """Skills the job wants, the master resume omits, and the candidate has confirmed.

    This is narrower than it used to be, and deliberately. Adopting every missing
    keyword meant a posting could put "A/B testing" and "multimodal
    representations" onto the resume of someone who had done neither - the job
    description describing what the employer wants, silently becoming a claim
    about the candidate. The job description is never evidence.

    What remains is the case the candidate actually asked for: a skill already in
    their own profile that their master resume happens not to list. That is
    verified information filling a gap in a document, not a new claim.
    """
    from app.services import document_check, tailoring

    confirmed = {
        str(skill).casefold().strip(): str(skill).strip()
        for skill in profile_merge.evidenced_skills(profile)
        if str(skill).strip()
    }
    already = document_check._vocabulary(document.plain_text())
    wanted = tailoring.adoptable_skills(missing, [])
    return [
        confirmed[term.casefold()]
        for term in wanted
        if term.casefold() in confirmed
        and document_check._normalise(term) not in already
    ]


def _cover_letter(profile: dict, job, analysis: dict, resume_text: str,
                  master_text: str = ""):
    from app.db.database import BACKEND_DIR

    """Write and render the cover letter, checking it against the finished resume.

    Consistency is the point of doing this after the resume rather than beside it:
    the letter has to agree with the document that was actually produced, not with
    the master resume it was cut from. A letter that fails its checks is rewritten
    once with those problems stated, and only then given up on.
    """
    problems: list[str] = []
    body, path = "", ""
    for attempt in range(2):
        feedback = ""
        if problems:
            feedback = (
                "\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS. Fix every one:\n"
                + "\n".join(f"- {problem}" for problem in problems)
            )
        letter = tailoring.write_cover_letter(profile, job, analysis, resume_text, feedback)
        body = letter.body
        path = resume_pdf.render_cover_letter(body, profile, job)

        from app.services import ats_scorer, document_check

        rendered = (BACKEND_DIR / path).resolve()
        problems = document_check.check_cover_letter(
            body, ats_scorer.extract_pdf_text(rendered), _pdf_pages(rendered),
            job, resume_text, profile, master_text,
        )
        if not problems:
            return path, body, []

    # Two attempts, still failing its own checks. The file is deleted rather than
    # returned: it was reachable from the download link and from autofill, so
    # "kept with a note" meant a document that had failed verification could be
    # attached to a real application - and the note was never shown anywhere.
    try:
        (BACKEND_DIR / path).resolve().unlink(missing_ok=True)
    except Exception:
        pass
    return "", body, problems


def _render_verified(document, stem, profile, page_target, verified, checker,
                     analysis=None, baseline_coverage=None, baseline_protected=None):
    """Render, check, correct, repeat - until the resume passes or the budget ends.

    Corrections are ordered by how little they cost the candidate:

      1. an ungrounded line goes back to its own wording
      2. skills the posting never mentions are dropped from the skills lists
      3. only then do bullets start to go, weakest first, and never one that is
         protected

    Two and three are in that order for a reason. A skills block is a list of
    nouns carrying no evidence, and on a long master resume it can occupy a third
    of the page; deleting a publication or a teaching role to make room for it is
    the wrong trade every time. After each trim, requirement coverage is checked
    again, and anything that lost its last supporting line comes back.
    """
    from app.services import evidence

    analysis = analysis or {}
    # Both measured against the unmodified resume, and passed in rather than
    # recomputed here: recomputing after selection makes whatever the model
    # removed invisible to the restoration check.
    protect = set(baseline_protected or evidence.protected(document, analysis))
    covered_before = baseline_coverage or evidence.coverage(document, analysis)
    # Skills are compressed in two passes before any evidence is touched, the
    # second one harder than the first.
    squeezes = [8, 4, 2]

    # The length that may be accepted when the only route to the target runs
    # through protected evidence. One page is the goal, not the point: a page of
    # the right length that has deleted the publication and the teaching role has
    # made the candidate look weaker, which is a worse outcome than a second page.
    ceiling = min(page_target + 1, max(page_target, document.page_count))

    history = []
    for attempt in range(MAX_REVISIONS):
        ungrounded = checker.ground_edits(document, verified)
        if ungrounded:
            for failure in ungrounded:
                document.revert(failure["index"])
            history.append({"attempt": attempt + 1, "reverted": ungrounded})
            continue

        # Always a PDF for verification and for delivery. A .docx master used to
        # be written as .docx and then measured with a PDF page counter, which
        # returned zero pages and empty text - so every DOCX master failed its own
        # validation and no amount of trimming could fix it. The source document
        # keeps its format; what the employer receives is a PDF.
        written = document.write(stem.with_suffix(".pdf"), as_pdf=True)
        pages = _pdf_pages(written)
        text = checker.ats_scorer.extract_pdf_text(written)
        problems = checker.check_resume(document, text, pages, page_target, profile)
        if not problems:
            history.append({"attempt": attempt + 1, "problems": []})
            return written, history

        history.append({"attempt": attempt + 1, "problems": problems, "pages": pages})
        if pages <= page_target:
            break

        if squeezes:
            # Buy the page from the skills list before touching any evidence.
            dropped_terms = document.compress_skills(
                evidence.job_vocabulary(analysis), squeezes.pop(0)
            )
            if dropped_terms:
                history[-1]["skills_terms_dropped"] = dropped_terms
                continue

        # Drop only what actually overflowed, then look again. Estimating from the
        # page ratio instead threw away half the resume to recover four lines.
        removed = document.trim_to_fit(_lines_over(written, page_target), protect)
        if not removed:
            if pages <= ceiling:
                history[-1]["accepted_longer"] = (
                    f"kept {pages} pages: reaching {page_target} would have meant "
                    "deleting evidence the posting explicitly asks for"
                )
                return written, history
            break
        history[-1]["trimmed"] = removed

        restored = evidence.lost_requirements(document, analysis, covered_before)
        for index in restored:
            document.restore(index)
            protect.add(index)
        if restored:
            history[-1]["restored"] = restored

    raise GenerationFailed(
        "The tailored resume could not be brought within "
        f"{page_target} page{'s' if page_target != 1 else ''} while keeping every "
        "claim supported. Raise the page target in Settings, or trim the master "
        "resume, then try again."
    )


async def build_package(database: Session, application: Application, job: Job) -> ApplicationPackage:
    """Analyse, select, verify, render. One pipeline, whatever the source document.

    There used to be two. A candidate with a master resume got selection,
    grounding checks, page-fit checks and honest gap reporting; a candidate
    without one got a wholesale regeneration that kept everything by default and
    adopted whatever keywords the posting asked for. Same button, same promises
    in the interface, different rules underneath - and only one set of rules was
    being maintained.

    A profile with no master is now turned into the same kind of document and run
    through the same path, so every check applies to every application.
    """
    from app.services import master_resume, resume_library

    profile = load_profile(database)
    config = settings.load()
    reject_thin_description(job)

    master = resume_library.master_document(database)
    source = (
        master_resume.SourceDocument.from_library(master)
        if master is not None
        else master_resume.SourceDocument.from_profile_record(profile)
    )
    return await _build_from_master(database, application, job, profile, config, source)


async def _build_from_master(database, application, job, profile, config, master):
    """Select from the master resume, verify, render. Two model calls, no review step."""
    def work():
        result = _tailor_from_master(master, profile, job, config)
        cover_path, cover_body, cover_problems = "", "", []
        if config["generate_cover_letter"]:
            cover_path, cover_body, cover_problems = _cover_letter(
                profile, job, result["analysis"], result["resume_text"],
                result["master_text"],
            )
        result["cover_problems"] = cover_problems
        return result, cover_path, cover_body

    result, cover_path, cover_body = await asyncio.to_thread(work)
    plan = result["plan"]
    if result["adopted"]:
        record_adopted_skills(database, result["adopted"])

    application.resume_path = result["resume_path"]
    application.cover_letter_path = cover_path or None
    application.status = ApplicationStatus.READY_FOR_REVIEW
    job.status = JobStatus.READY_FOR_REVIEW

    score = result["score"]
    package = database.get(ApplicationPackage, application.id) or ApplicationPackage(
        application_id=application.id
    )
    package.ats_score = score["overall"]
    package.keyword_coverage = score["keyword"]["coverage_percent"]
    package.analysis = result["analysis"]
    package.score_report = score
    package.score_history = [{"pass": 1, "overall": score["overall"],
                              "coverage": score["keyword"]["coverage_percent"]}]
    package.tailored_resume = {
        "from_master": master.label,
        "lines_kept": result["lines_kept"],
        "lines_total": result["lines_total"],
        "pages": result["pages"],
        "notes": plan["notes"],
        # Internal only: the requirements this candidate cannot support. Recorded
        # so the gap is knowable, never written into either document.
        "unsupported_requirements": plan["unsupported_requirements"],
        "keywords_unsupported": plan["unsupported_requirements"],
        "skills": result["adopted"],
        "verification": result["history"],
        "cover_problems": result.get("cover_problems", []),
    }
    package.adopted_skills = result["adopted"]
    package.source_fingerprint = source_fingerprint(profile, job, master, config)
    package.cover_letter_body = cover_body
    package.model_used = config["model"]
    package.passes_used = 1
    package.generated_at = datetime.now(timezone.utc)

    database.add(package)
    database.commit()
    database.refresh(package)
    return package


def package_response(package: ApplicationPackage, application: Application, job: Job) -> dict:
    resume = package.tailored_resume or {}
    return {
        "application_id": application.id,
        "job": {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "source": job.source,
            "application_url": job.application_url,
        },
        "status": application.status.value,
        "ats_score": package.ats_score,
        "keyword_coverage": package.keyword_coverage,
        "passes_used": package.passes_used,
        "score_history": package.score_history,
        "score_report": package.score_report,
        "analysis": package.analysis,
        "headline": resume.get("headline", ""),
        "summary": resume.get("summary", ""),
        "skills": resume.get("skills", []),
        "keywords_incorporated": resume.get("keywords_incorporated", []),
        "keywords_unsupported": resume.get("keywords_unsupported", []),
        "adopted_skills": package.adopted_skills or [],
        # Only populated on the master-resume path. `from_master` being empty is
        # how the UI knows a document was generated rather than re-worded.
        "tailored_resume": {
            "from_master": resume.get("from_master", ""),
            "lines_kept": resume.get("lines_kept", 0),
            "lines_total": resume.get("lines_total", 0),
            "pages": resume.get("pages", 0),
            "notes": resume.get("notes", ""),
            # Named `gaps` for the UI, which has always called them that. These
            # are the requirements nothing in the candidate's material supports;
            # they are shown to the candidate and never written into a document.
            "gaps": resume.get("unsupported_requirements", resume.get("gaps", [])),
            "cover_problems": resume.get("cover_problems", []),
        },
        "resume_path": application.resume_path,
        "cover_letter_path": application.cover_letter_path,
        "cover_letter_body": package.cover_letter_body,
        "autofilled_at": package.autofilled_at,
        "autofill_report": package.autofill_report,
        "generated_at": package.generated_at,
        "model_used": package.model_used,
    }


__all__ = [
    "build_package",
    "GenerationFailed",
    "package_response",
    "load_profile",
    "ProfileIncomplete",
    "ClaudeUnavailable",
]
