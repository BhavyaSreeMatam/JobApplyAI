"""AI-generated application artifacts.

Additive table: the existing jobs / applications / approvals rows are untouched,
so an existing database keeps working and `create_all` can add this cleanly.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationPackage(Base):
    __tablename__ = "application_packages"

    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), primary_key=True
    )

    ats_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    keyword_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Full structured records so the UI can explain the score.
    analysis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    score_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    score_history: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tailored_resume: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Skills this job asked for that the candidate had used but never listed.
    # Kept per application so the review screen can show what was added and when.
    adopted_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # What this package was built from. When the profile, the master resume, the
    # job description or the settings that shape a document change, the stored
    # PDFs no longer reflect the inputs and must not be offered as current.
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    cover_letter_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_used: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    passes_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set when the browser handoff actually filled the employer's form.
    autofilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    autofill_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
