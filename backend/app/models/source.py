"""Job sources the user configures: which sites to pull jobs from."""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceKind(str, enum.Enum):
    """How jobs are obtained from this source."""

    BOARD = "board"        # public JSON feed - no browser, no credentials
    BROWSER = "browser"    # your logged-in browser session (LinkedIn/Indeed/Handshake)


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # "greenhouse" | "lever" | ... | "linkedin" | "indeed" | "handshake"
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    kind: Mapped[SourceKind] = mapped_column(
        Enum(SourceKind, native_enum=False), nullable=False, default=SourceKind.BOARD
    )

    # Board tenant slug, or the search query for a browser source.
    slug: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Browser sources only.
    search_terms: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location_filter: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sync_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
