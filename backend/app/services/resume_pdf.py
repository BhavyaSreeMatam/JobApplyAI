"""ATS-safe PDF rendering.

Every choice here exists to survive resume parsers:
  - one single column, no tables, no text boxes, no images, no headers/footers
  - core PostScript fonts (Helvetica) so glyphs map to real characters
  - standard section headings on their own line, in reading order
  - contact details as plain text lines, not glyph-separated
  - a real text layer (never vector-outlined text)
"""
import re
import uuid
from html import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from app.db.database import DATA_DIR

NAME = ParagraphStyle(
    "AtsName", fontName="Helvetica-Bold", fontSize=17, leading=20,
    spaceAfter=2, alignment=TA_LEFT,
)
CONTACT = ParagraphStyle(
    "AtsContact", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=1,
)
HEADING = ParagraphStyle(
    "AtsHeading", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
    spaceBefore=11, spaceAfter=3,
)
ENTRY = ParagraphStyle(
    "AtsEntry", fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=5, spaceAfter=1,
)
META = ParagraphStyle(
    "AtsMeta", fontName="Helvetica-Oblique", fontSize=9.5, leading=12, spaceAfter=2,
)
BODY = ParagraphStyle(
    "AtsBody", fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=3,
)
# Bullets are drawn as ordinary Helvetica text, NOT via ListFlowable, and the
# marker is an ASCII hyphen. Verified by round-trip extraction: U+2022 and U+25AA
# both corrupt to "(cid:127)" / "n" in the core fonts, which would hand an ATS a
# garbage character at the start of every achievement. Hyphen extracts cleanly.
BULLET = ParagraphStyle(
    "AtsBullet", fontName="Helvetica", fontSize=10, leading=13.5, spaceAfter=2,
    leftIndent=13, firstLineIndent=-9,
)


def document_name(profile: dict, job, kind: str) -> str:
    """A human-readable filename - this is what the employer sees attached."""
    parts = [
        f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
        getattr(job, "company", "") or "",
        getattr(job, "title", "") or "",
        kind,
    ]
    slug = "_".join(
        re.sub(r"[^A-Za-z0-9]+", "-", part).strip("-") for part in parts if part
    )
    slug = re.sub(r"-{2,}", "-", slug)[:110].strip("_-")
    # A short suffix keeps two applications to the same role from colliding.
    return f"{slug or kind}_{uuid.uuid4().hex[:6]}.pdf"


def _clean(text) -> str:
    """Strip characters that corrupt ATS extraction, keep real punctuation."""
    value = str(text or "")
    replacements = {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", " ": " ", "•": "",
    }
    for bad, good in replacements.items():
        value = value.replace(bad, good)
    value = "".join(ch for ch in value if ch.isprintable())
    return re.sub(r"\s+", " ", value).strip()


def _para(text, style):
    return Paragraph(escape(_clean(text)), style)


def _entry_flowables(entry) -> list:
    heading = _clean(getattr(entry, "heading", "") or "")
    # Education is stored as separate degree/major fields; recombine for display.
    degree = _clean(getattr(entry, "degree", "") or "")
    major = _clean(getattr(entry, "major", "") or "")
    if not heading and (degree or major):
        heading = ", ".join(x for x in (degree, major) if x)
    org = _clean(getattr(entry, "organization", "") or "")
    dates = _clean(getattr(entry, "dates", "") or "")
    if not dates and isinstance(entry, dict):
        from app.services.workday import format_dates

        dates = _clean(format_dates(entry))
    location = _clean(getattr(entry, "location", "") or "")
    bullets = [b for b in (getattr(entry, "bullets", []) or []) if _clean(b)]

    out = []
    title_line = " - ".join(x for x in (heading, org) if x)
    if title_line:
        out.append(_para(title_line, ENTRY))
    meta_line = " | ".join(x for x in (location, dates) if x)
    if meta_line:
        out.append(_para(meta_line, META))
    for bullet in bullets:
        out.append(Paragraph("- " + escape(_clean(bullet)), BULLET))
    return out


def render(resume, profile: dict, job=None) -> str:
    """Write the tailored resume to an ATS-safe PDF. Returns a repo-relative path."""
    destination = DATA_DIR / "resumes"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / document_name(profile, job, "Resume")

    story = []
    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    story.append(_para(full_name or "Candidate", NAME))

    primary = " | ".join(
        _clean(profile[key]) for key in ("email", "phone", "location") if profile.get(key)
    )
    if primary:
        story.append(_para(primary, CONTACT))
    links = [_clean(profile[k]) for k in ("linkedin", "website", "publications_url") if profile.get(k)]
    if links:
        story.append(_para(" | ".join(links), CONTACT))

    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=0.6, spaceAfter=2, color="#333333"))

    if getattr(resume, "summary", ""):
        story.append(_para("Summary", HEADING))
        story.append(_para(resume.summary, BODY))

    if getattr(resume, "skills", None):
        story.append(_para("Skills", HEADING))
        story.append(_para(", ".join(_clean(s) for s in resume.skills if _clean(s)), BODY))

    for title in ("Experience", "Projects", "Education", "Publications"):
        entries = [
            e for e in resume.section(title.casefold())
            if _clean(getattr(e, "heading", "")) or _clean(getattr(e, "organization", ""))
        ]
        if not entries:
            continue
        story.append(_para(title, HEADING))
        for entry in entries:
            story.extend(_entry_flowables(entry))

    SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"{full_name} - Resume",
        author=full_name,
        subject="Resume",
    ).build(story)

    return "data/resumes/" + path.name


COVER_BODY = ParagraphStyle(
    "CoverBody", fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=9,
)


def render_cover_letter(body: str, profile: dict, job) -> str:
    destination = DATA_DIR / "cover_letters"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / document_name(profile, job, "Cover-Letter")

    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    story = [_para(full_name or "Candidate", NAME)]
    contact = " | ".join(
        _clean(profile[key]) for key in ("email", "phone", "location") if profile.get(key)
    )
    if contact:
        story.append(_para(contact, CONTACT))
    story.append(Spacer(1, 14))
    # The document's own title, from the verified company and role. Two lines
    # rather than one: a long role title plus a company wraps, and what wraps is
    # the company, leaving "Inc." alone on a line of its own. An unknown company
    # is left out entirely rather than printed as "Unknown company".
    company = _clean(getattr(job, "company", ""))
    if company.casefold().startswith("unknown"):
        company = ""
    story.append(_para(f"Cover Letter - {_clean(job.title)}", ENTRY))
    if company:
        story.append(_para(company, META))
    story.append(Spacer(1, 8))
    story.append(_para("Dear Hiring Manager,", COVER_BODY))
    for paragraph in [p for p in (body or "").split("\n") if p.strip()]:
        story.append(_para(paragraph, COVER_BODY))
    story.append(Spacer(1, 6))
    story.append(_para("Sincerely,", COVER_BODY))
    story.append(_para(full_name, COVER_BODY))

    SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
        title=f"{full_name} - Cover Letter"
              + (f" - {company}" if company else ""),
        author=full_name,
    ).build(story)

    return "data/cover_letters/" + path.name
