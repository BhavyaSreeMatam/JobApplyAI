import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationStatus(str, enum.Enum):
    PREPARING = "preparing"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED_QUEUED = "approved_queued"
    APPLYING = "applying"
    NEEDS_HUMAN_INPUT = "needs_human_input"
    APPLIED = "applied"
    SUBMISSION_FAILED = "submission_failed"
    REJECTED_BY_USER = "rejected_by_user"
    INTERVIEW = "interview"
    OFFER = "offer"
    EMPLOYER_REJECTED = "employer_rejected"
    WITHDRAWN = "withdrawn"
    POSITION_CLOSED = "position_closed"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id"),
        nullable=False,
        unique=True,
        index=True,
    )

    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, native_enum=False),
        nullable=False,
        default=ApplicationStatus.PREPARING,
        index=True,
    )

    resume_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    cover_letter_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    answers_json: Mapped[dict[str, object]] = mapped_column(
    JSON,
    nullable=False,
    default=dict,
    )

    confirmation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )