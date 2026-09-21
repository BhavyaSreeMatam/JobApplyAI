import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.dependencies import get_db
from app.schemas.discovery import (
    DiscoveryResponse,
    GreenhouseDiscoveryRequest,
)
from app.services.greenhouse import discover_greenhouse_jobs


router = APIRouter(
    prefix="/discovery",
    tags=["Discovery"],
)


@router.post(
    "/greenhouse",
    response_model=DiscoveryResponse,
)
async def run_greenhouse_discovery(
    request: GreenhouseDiscoveryRequest,
    database: Session = Depends(get_db),
) -> DiscoveryResponse:
    try:
        result = await discover_greenhouse_jobs(
            board_token=request.board_token,
            company_name=request.company_name,
            database=database,
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Greenhouse board not found.",
            )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Greenhouse returned an error.",
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not connect to Greenhouse.",
        )

    return DiscoveryResponse(**result)