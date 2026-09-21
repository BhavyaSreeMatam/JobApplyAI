from html import unescape

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job


GREENHOUSE_API = (
    "https://boards-api.greenhouse.io/v1/boards/"
    "{board_token}/jobs?content=true"
)


def clean_description(content: str) -> str:
    decoded_content = unescape(content)
    soup = BeautifulSoup(decoded_content, "html.parser")
    return soup.get_text(" ", strip=True)


async def discover_greenhouse_jobs(
    board_token: str,
    company_name: str,
    database: Session,
) -> dict[str, int | str]:
    url = GREENHOUSE_API.format(board_token=board_token)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()

    greenhouse_jobs = response.json().get("jobs", [])

    added = 0
    duplicates = 0

    for greenhouse_job in greenhouse_jobs:
        application_url = greenhouse_job.get("absolute_url")

        if not application_url:
            continue

        existing_job = database.scalar(
            select(Job).where(
                Job.application_url == application_url
            )
        )

        if existing_job is not None:
            duplicates += 1
            continue

        location_data = greenhouse_job.get("location") or {}

        job = Job(
            external_id=str(greenhouse_job["id"]),
            source="greenhouse",
            company=company_name,
            title=greenhouse_job.get("title", "Untitled position"),
            location=location_data.get("name"),
            description=clean_description(
                greenhouse_job.get("content", "")
            ),
            application_url=application_url,
        )

        database.add(job)
        added += 1

    database.commit()

    return {
        "source": "greenhouse",
        "company": company_name,
        "jobs_found": len(greenhouse_jobs),
        "jobs_added": added,
        "duplicates_skipped": duplicates,
    }