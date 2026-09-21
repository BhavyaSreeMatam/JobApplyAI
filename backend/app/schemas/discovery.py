from pydantic import BaseModel, Field


class GreenhouseDiscoveryRequest(BaseModel):
    board_token: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    company_name: str = Field(
        min_length=1,
        max_length=255,
    )


class DiscoveryResponse(BaseModel):
    source: str
    company: str
    jobs_found: int
    jobs_added: int
    duplicates_skipped: int