from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus


class JobCreate(BaseModel):
    external_id: str | None = None
    source: str = Field(min_length=1, max_length=100)
    company: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    location: str | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    description: str = Field(min_length=1)
    application_url: str = Field(min_length=1)
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None


class JobRead(JobCreate):
    model_config = ConfigDict(from_attributes=True)

    # Reading is not creating. A job added by hand must come with a description,
    # but a discovered one may legitimately have none: some sites link through a
    # redirect that carries no posting text, and storing an empty description is
    # how "we never captured it" is distinguished from "here is the posting".
    #
    # Inheriting the create-time `min_length=1` made every one of those rows fail
    # response validation, which took down the whole jobs list rather than the one
    # field - the list is returned as a single model, so one short string is a
    # 500 for all of it.
    description: str = ""

    # Empty means "never captured", which the UI shows as a recoverable state
    # rather than a blank panel. Kept distinct from a description that exists but
    # is not being displayed - those are different bugs with different fixes.
    source_url: str = ""

    id: str
    posted_at: datetime | None
    match_score: float | None
    status: JobStatus
    discovered_at: datetime
    updated_at: datetime