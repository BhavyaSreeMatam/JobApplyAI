from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "jobapplyai.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

# Columns added after a database already existed. create_all() only creates whole
# tables, so an existing install needs these applied explicitly.
ADDED_COLUMNS = {
    "jobs": {"posted_at": "DATETIME", "source_url": "TEXT DEFAULT ''"},
    "application_packages": {"adopted_skills": "JSON", "source_fingerprint": "TEXT DEFAULT ''"},
    "resume_documents": {"is_master": "BOOLEAN DEFAULT 0"},
}


def apply_pending_columns() -> list[str]:
    """Add any missing columns to existing tables. Safe to run on every start."""
    from sqlalchemy import text

    applied = []
    with engine.begin() as connection:
        for table, columns in ADDED_COLUMNS.items():
            existing = {
                row[1]
                for row in connection.execute(text(f"PRAGMA table_info({table})"))
            }
            if not existing:
                continue  # table not created yet; create_all will include the column
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    )
                    applied.append(f"{table}.{name}")
    return applied
