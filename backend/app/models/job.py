import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    ELIGIBILITY_CHECKED = "eligibility_checked"
    RANKED = "ranked"
    PREPARING = "preparing"
    READY_FOR_REVIEW = "ready_for_review"
    REJECTED_BY_USER = "rejected_by_user"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    external_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    company: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    location: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    workplace_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    employment_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    application_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        unique=True,
    )

    # Where the listing card linked, when that differs from the canonical
    # posting. Indeed regenerates a tracking redirect on every search, so it is
    # useless as identity but worth keeping: it is the link the user actually saw.
    source_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )

    salary_min: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    salary_max: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    salary_currency: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )

    match_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False),
        nullable=False,
        default=JobStatus.DISCOVERED,
        index=True,
    )

    # When the employer published it, where the source tells us. Distinct from
    # discovered_at, which is only when this app first saw it.
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    discovered_at: Mapped[datetime] = mapped_column(
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