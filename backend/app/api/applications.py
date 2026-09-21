from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.models.application import Application, ApplicationStatus
from app.models.job import Job, JobStatus
from app.schemas.application import (
    ApplicationAnswersUpdate,
    ApplicationCreate,
    ApplicationRead,
)
from app.services.file_storage import store_resume
from app.models.candidate import PreparedPacket


router = APIRouter(
    prefix="/applications",
    tags=["Applications"],
)


@router.post(
    "",
    response_model=ApplicationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    application_data: ApplicationCreate,
    database: Session = Depends(get_db),
) -> Application:
    job = database.get(Job, application_data.job_id)

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        )

    application = Application(**application_data.model_dump())
    job.status = JobStatus.PREPARING
    database.add(application)

    try:
        database.commit()
        database.refresh(application)
    except IntegrityError as error:
        database.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An application already exists for this job.",
        ) from error

    return application


@router.get(
    "",
    response_model=list[ApplicationRead],
)
def list_applications(
    database: Session = Depends(get_db),
) -> list[Application]:
    statement = select(Application).order_by(
        Application.created_at.desc()
    )

    return list(database.scalars(statement).all())


@router.get(
    "/{application_id}",
    response_model=ApplicationRead,
)
def get_application(
    application_id: str,
    database: Session = Depends(get_db),
) -> Application:
    application = database.get(Application, application_id)

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    return application


@router.post(
    "/{application_id}/resume",
    response_model=ApplicationRead,
)
async def upload_application_resume(
    application_id: str,
    resume: UploadFile = File(...),
    database: Session = Depends(get_db),
) -> Application:
    application = database.get(Application, application_id)

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    if application.status != ApplicationStatus.PREPARING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A resume can only be changed while the "
                "application is preparing."
            ),
        )

    try:
        resume_path = await store_resume(resume)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    application.resume_path = resume_path
    database.commit()
    database.refresh(application)

    return application


@router.patch(
    "/{application_id}/ready",
    response_model=ApplicationRead,
)
def mark_application_ready(
    application_id: str,
    database: Session = Depends(get_db),
) -> Application:
    application = database.get(Application, application_id)

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    if not application.resume_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Upload a resume before sending this "
                "application for review."
            ),
        )

    if application.status != ApplicationStatus.PREPARING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only an application with preparing status "
                "can be marked ready."
            ),
        )

    application.status = ApplicationStatus.READY_FOR_REVIEW
    database.commit()
    database.refresh(application)

    return application


@router.patch(
    "/{application_id}/answers",
    response_model=ApplicationRead,
)
def update_application_answers(
    application_id: str,
    answer_data: ApplicationAnswersUpdate,
    database: Session = Depends(get_db),
) -> Application:
    application = database.get(Application, application_id)

    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found.",
        )

    editable_statuses = {
        ApplicationStatus.PREPARING,
        ApplicationStatus.READY_FOR_REVIEW,
    }

    if application.status not in editable_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Answers cannot be changed after the "
                "application has been approved."
            ),
        )

    application.answers_json = answer_data.answers_json
    packet = database.get(PreparedPacket, application_id)
    if packet:
        packet.reviewed = False
    database.commit()
    database.refresh(application)

    return application


@router.post("/clear")
def clear_application_history(
    delete_files: bool = True,
    database: Session = Depends(get_db),
) -> dict:
    """Wipe every application so the tracker starts empty.

    Removes applications and everything hanging off them - generated packages,
    approvals, prepared packets - and by default the per-application document
    copies too. Your candidate profile, resume library, jobs and configured
    sources are all left alone.
    """
    from app.db.database import BACKEND_DIR
    from app.models.approval import Approval
    from app.models.candidate import PreparedPacket
    from app.models.package import ApplicationPackage
    from app.models.resume import ResumeDocument

    applications = list(database.scalars(select(Application)).all())
    # Files still owned by the resume library must survive.
    library = {
        r.file_path for r in database.scalars(select(ResumeDocument)).all()
    }
    removed_files = 0

    for application in applications:
        for relative in (application.resume_path, application.cover_letter_path):
            if not delete_files or not relative or relative in library:
                continue
            path = (BACKEND_DIR / relative).resolve()
            allowed = (BACKEND_DIR / "data").resolve()
            if path.is_relative_to(allowed) and path.is_file():
                path.unlink(missing_ok=True)
                removed_files += 1

        for model in (ApplicationPackage, PreparedPacket):
            row = database.get(model, application.id)
            if row is not None:
                database.delete(row)
        for approval in database.scalars(
            select(Approval).where(Approval.application_id == application.id)
        ).all():
            database.delete(approval)
        database.delete(application)

    # Jobs go back to being un-started.
    for job in database.scalars(select(Job)).all():
        if job.status != JobStatus.DISCOVERED:
            job.status = JobStatus.RANKED if job.match_score is not None else JobStatus.DISCOVERED

    database.commit()
    return {
        "applications_deleted": len(applications),
        "files_deleted": removed_files,
        "message": (
            f"Cleared {len(applications)} applications. Your profile, resume library, "
            "jobs and sources were not touched."
        ),
    }
