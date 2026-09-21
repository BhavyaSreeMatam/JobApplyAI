from pydantic import BaseModel, Field


class MatchingRequest(BaseModel):
    target_roles: list[str] = Field(min_length=1)
    skills: list[str] = Field(min_length=1)
    preferred_locations: list[str] = Field(default_factory=list)
    excluded_title_terms: list[str] = Field(default_factory=list)
    minimum_score: float = Field(default=60, ge=0, le=100)


class MatchResult(BaseModel):
    job_id: str
    company: str
    title: str
    location: str | None
    score: float
    application_url: str


class MatchingResponse(BaseModel):
    jobs_scored: int
    recommended_jobs: int
    minimum_score: float
    top_matches: list[MatchResult]