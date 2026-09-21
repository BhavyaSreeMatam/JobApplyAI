from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.application import ApplicationStatus


class ApplicationCreate(BaseModel):
    job_id: str
    resume_path: str | None = None
    cover_letter_path: str | None = None
    answers_json: dict[str, Any] = Field(default_factory=dict)


class ApplicationAnswersUpdate(BaseModel):
    answers_json: dict[str, Any] = Field(default_factory=dict)


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    status: ApplicationStatus
    resume_path: str | None
    cover_letter_path: str | None
    answers_json: dict[str, Any]
    confirmation_id: str | None
    failure_reason: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime