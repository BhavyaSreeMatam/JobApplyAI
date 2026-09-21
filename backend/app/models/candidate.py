"""Additive tables: existing jobs, approvals and applications are preserved."""
from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default="local")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class AnswerMemory(Base):
    __tablename__ = "answer_memories"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    company: Mapped[str] = mapped_column(String)
    labels: Mapped[list] = mapped_column(JSON)
    answer: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PreparedPacket(Base):
    __tablename__ = "prepared_packets"
    application_id: Mapped[str] = mapped_column(String, primary_key=True)
    profile_revision: Mapped[int] = mapped_column(Integer)
    questions: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    reviewed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
