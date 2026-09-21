"""The resume library: documents you uploaded plus ones the builder generated."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResumeKind(str, enum.Enum):
    UPLOADED = "uploaded"      # a PDF/DOCX the user supplied
    GENERATED = "generated"    # built here from the profile


class ResumeDocument(Base):
    __tablename__ = "resume_documents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    role_target: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    kind: Mapped[ResumeKind] = mapped_column(
        Enum(ResumeKind, native_enum=False), nullable=False, default=ResumeKind.GENERATED
    )

    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Scored against whatever description or role it was built/checked for.
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    keyword_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    analysis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # The description this was tailored against, kept so it can be re-scored.
    job_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    company: Mapped[str] = mapped_column(String(160), nullable=False, default="")

    times_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The resume tailoring starts from. Exactly one document carries this.
    is_master: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_favourite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
