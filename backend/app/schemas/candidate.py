from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class HistoryEntry(BaseModel):
    """One job, project, degree or publication.

    `location` was missing here, so every location typed into the profile was
    silently dropped on save - pydantic ignores unknown keys by default.

    Education uses `degree` and `major` separately because application forms ask
    for them as separate fields (Workday has distinct pickers for each), and
    splitting a combined heading after the fact is guesswork.
    """

    heading: str = ""
    organization: str = ""
    location: str = ""
    bullets: list[str] = Field(default_factory=list)

    # Dates are held as parts rather than one string. Application forms ask for
    # month and year separately (Workday has distinct inputs), so storing them
    # apart removes a parsing step that could only ever be a guess.
    start_month: str = ""
    start_year: str = ""
    end_month: str = ""
    end_year: str = ""
    current: bool = False

    # Kept so entries saved before the split still render, and so a resume can
    # show a single line. Recomputed from the parts whenever they are set.
    dates: str = ""

    # Education only.
    degree: str = ""
    major: str = ""
    gpa: str = ""


class ProfileProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")
    skills: dict[str, Literal["user", "document", "machine"]] = Field(default_factory=dict)


class ProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    # Workday and several other trackers ask what kind of number this is, as a
    # required Mobile / Home / Work picker beside the number itself.
    phone_device_type: str = "Mobile"
    location: str = ""
    # Structured address - many forms (Workday especially) want the parts
    # separately rather than one free-text line.
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    website: str = ""
    github: str = ""
    linkedin: str = ""
    publications_url: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    # Skills that arrived from a job description rather than being typed in here.
    # These stay pending until the candidate confirms them or a resume import
    # supplies evidence. Provenance survives ordinary profile saves.
    adopted_skills: list[str] = Field(default_factory=list)
    provenance: ProfileProvenance = Field(default_factory=ProfileProvenance)
    experience: list[HistoryEntry] = Field(default_factory=list)
    education: list[HistoryEntry] = Field(default_factory=list)
    projects: list[HistoryEntry] = Field(default_factory=list)
    publications: list[HistoryEntry] = Field(default_factory=list)
    earliest_start: str = ""
    relocation: bool | None = None
    office_25_percent: bool | None = None
    work_country: str = ""
    authorized_to_work: bool | None = None
    requires_sponsorship_now: bool | None = None
    requires_sponsorship_future: bool | None = None
    authorization_start: date | None = None
    authorization_end: date | None = None
    authorization_reviewed_on: date | None = None

    # Voluntary self-identification. These are stored exactly as you select them
    # and are only ever copied onto a form verbatim - never inferred from a name,
    # a photo, or anything else. Leave any of them blank to keep answering by hand;
    # "Decline to self-identify" is a real answer and is filled like any other.
    gender: str = ""
    hispanic_latino: str = ""
    race_ethnicity: str = ""
    veteran_status: str = ""
    disability_status: str = ""


class ProfileUpdate(BaseModel):
    revision: int
    data: ProfileData


class MemoryChoice(BaseModel):
    field_name: str
    aliases: list[str] = Field(default_factory=list, max_length=10)
    scope: str = "company"


class PacketAnswers(BaseModel):
    answers_json: dict[str, Any]
    remember: list[MemoryChoice] = Field(default_factory=list)
    reviewed: bool = False
