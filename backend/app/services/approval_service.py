import hashlib
import json
import secrets
from pathlib import Path
from app.db.database import BACKEND_DIR


def document_hash(relative_path):
    if not relative_path:
        return None
    path = (BACKEND_DIR / relative_path).resolve()
    if not path.is_relative_to((BACKEND_DIR / "data").resolve()) or not path.is_file():
        raise ValueError("Upload or generate a document under backend/data before approval")
    return hashlib.sha256(path.read_bytes()).hexdigest()

from app.models.application import Application
from app.models.job import Job


def create_packet_hash(
    application: Application,
    job: Job,
) -> str:
    packet = {
        "application_id": application.id,
        "job_id": job.id,
        "application_url": job.application_url,
        "resume_path": application.resume_path,
        "resume_sha256": document_hash(application.resume_path),
        "cover_letter_sha256": document_hash(application.cover_letter_path),
        "cover_letter_path": application.cover_letter_path,
        "answers_json": application.answers_json,
    }

    serialized_packet = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        serialized_packet.encode("utf-8")
    ).hexdigest()


def create_approval_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    return raw_token, token_hash
