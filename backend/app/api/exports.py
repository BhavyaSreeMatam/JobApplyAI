from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.services.tracker_excel import build_workbook


router = APIRouter(
    prefix="/exports",
    tags=["Exports"],
)


@router.get("/applications/excel")
def download_applications_excel(
    database: Session = Depends(get_db),
) -> FileResponse:
    export_path = build_workbook(database)

    return FileResponse(
        path=export_path,
        filename="job_applications.xlsx",
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )