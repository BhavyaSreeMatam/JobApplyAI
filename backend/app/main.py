from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.database import Base, apply_pending_columns, engine
from app.db.dependencies import get_db

# Models are imported for their side effect of registering tables.
from app.models.job import Job  # noqa: F401
from app.models.application import Application  # noqa: F401
from app.models.approval import Approval  # noqa: F401
from app.models.candidate import CandidateProfile, AnswerMemory, PreparedPacket  # noqa: F401
from app.models.source import JobSource  # noqa: F401
from app.models.package import ApplicationPackage  # noqa: F401
from app.models.resume import ResumeDocument  # noqa: F401

from app.api.jobs import router as jobs_router
from app.api.applications import router as applications_router
from app.api.exports import router as exports_router
from app.api.discovery import router as discovery_router
from app.api.matching import router as matching_router
from app.api.candidate import router as candidate_router
from app.api.sources import router as sources_router
from app.api.apply import router as apply_router
from app.api.config import router as config_router
from app.api.resumes import router as resumes_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    apply_pending_columns()
    yield
    from app.services import browser_session

    await browser_session.stop()


app = FastAPI(
    title="JobApplyAI API",
    description="Local job search assistant: multi-source discovery, ATS-tailored "
                "resumes, assisted application filling, and Excel tracking.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    jobs_router,
    applications_router,
    exports_router,
    discovery_router,
    matching_router,
    candidate_router,
    sources_router,
    apply_router,
    config_router,
    resumes_router,
):
    app.include_router(router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "JobApplyAI", "message": "JobApplyAI backend is running"}


@app.get("/health")
def health_check(database: Session = Depends(get_db)) -> dict[str, object]:
    from app.core import settings
    from app.services import browser_session

    try:
        database.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Database unavailable") from error
    config = settings.redacted()
    return {
        "status": "healthy",
        "database": "connected",
        "claude_key": config["anthropic_api_key_source"],
        "model": config["model"],
        "browser_open": browser_session.is_open(),
    }
