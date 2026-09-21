from datetime import datetime

from pydantic import BaseModel

from app.models.application import ApplicationStatus


class ApprovalResponse(BaseModel):
    approval_id: str
    application_id: str
    approval_token: str
    packet_hash: str
    expires_at: datetime
    application_status: ApplicationStatus