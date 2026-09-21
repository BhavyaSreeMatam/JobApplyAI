"""Structured-output schemas for the Claude-powered analysis and tailoring calls.

Kept deliberately flat and fully-required. Structured outputs close the schema
(every object gets additionalProperties:false), and each field carrying a default
becomes an optional union in that closed form. With a nested model referenced from
several array fields those unions multiply, and the API rejects the request with
"Schema is too complex".

So: no defaults, no Optional, one nested model per schema, and a `section`
discriminator instead of four parallel entry lists. Field guidance lives in the
system prompts rather than in `description=`, which keeps the schema small too.
"""
from typing import Literal

from pydantic import BaseModel

Importance = Literal["required", "preferred", "nice_to_have"]
Section = Literal["experience", "projects", "education", "publications"]


class Keyword(BaseModel):
    term: str
    importance: Importance


class JobAnalysis(BaseModel):
    """A posting broken into the parts that are scored separately.

    `keywords` are screenable terms; `responsibilities` are what the job actually
    involves doing; `eligibility` are pass/fail conditions (authorisation,
    location, clearance, licence) that are reported rather than scored. Keeping
    them apart is what lets a strong keyword match stop hiding a disqualifier.
    """

    normalized_title: str
    seniority: str
    keywords: list[Keyword]
    responsibilities: list[str]
    eligibility: list[str]
    hard_requirements: list[str]
    # What the employer says good looks like, and what the team actually works
    # on. Neither is screened for; both are what makes a cover letter specific
    # rather than flattering.
    outcomes: list[str]
    company_context: str
    ats_notes: str


class LineEdit(BaseModel):
    """One line of the candidate's own resume, re-worded."""

    index: int
    text: str
    reason: str


Action = Literal["keep", "compress", "remove"]


class LineDecision(BaseModel):
    """What becomes of one line of the candidate's resume for this job.

    `supports` is the private source reference: the requirement this line is
    being kept for. It is used for internal checking and never rendered.
    """

    index: int
    action: Action
    text: str
    priority: int
    supports: str


class TailoringPlan(BaseModel):
    """A complete selection: what to keep, shorten, and drop, and what is missing."""

    decisions: list[LineDecision]
    skills_added: list[str]
    unsupported_requirements: list[str]
    notes: str


class TailoredEdits(BaseModel):
    """The complete result of tailoring: wording changes and nothing else.

    Returning edits rather than a whole resume is what keeps this cheap and what
    makes the no-layout-changes guarantee mechanical: lines not named here are
    untouched, byte for byte.
    """

    edits: list[LineEdit]
    skills_added: list[str]
    gaps: list[str]
    notes: str


class ResumeEntry(BaseModel):
    section: Section
    heading: str
    organization: str
    dates: str
    location: str
    bullets: list[str]

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.section}: {self.heading} @ {self.organization}"


class TailoredResume(BaseModel):
    headline: str
    summary: str
    skills: list[str]
    entries: list[ResumeEntry]
    keywords_incorporated: list[str]
    keywords_unsupported: list[str]

    def section(self, name: str) -> list[ResumeEntry]:
        return [entry for entry in self.entries if entry.section == name]


class CoverLetter(BaseModel):
    body: str
    # What the posting seemed to want that the profile could not support. Left
    # out of the letter and reported instead, so the omission is a decision the
    # candidate can see rather than a gap they discover in an interview.
    unsupported_claims_avoided: list[str]


class ParsedProfile(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str
    website: str
    linkedin: str
    publications_url: str
    summary: str
    skills: list[str]
    entries: list[ResumeEntry]

    def section(self, name: str) -> list[ResumeEntry]:
        return [entry for entry in self.entries if entry.section == name]


class DraftedAnswer(BaseModel):
    """One employer-specific question and a grounded draft reply."""

    field_name: str
    question: str
    answer: str
    needs_you: bool
    reason: str


class DraftedAnswers(BaseModel):
    answers: list[DraftedAnswer]


class JobPosting(BaseModel):
    """A job extracted from an arbitrary careers page."""

    title: str
    company: str
    location: str
    employment_type: str
    description: str
    is_job_posting: bool
