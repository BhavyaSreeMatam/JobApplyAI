from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.schemas.matching import MatchingRequest, MatchingResponse
from app.services.job_matcher import rescore_all, score_available_jobs


router = APIRouter(
    prefix="/matching",
    tags=["Matching"],
)


@router.post(
    "/score",
    response_model=MatchingResponse,
)
def run_job_matching(
    request: MatchingRequest,
    database: Session = Depends(get_db),
) -> MatchingResponse:
    result = score_available_jobs(database, request)
    return MatchingResponse(**result)

@router.post("/rescore")
def rescore(database: Session = Depends(get_db)):
    """Re-score every job from the saved profile and target roles."""
    return rescore_all(database)
