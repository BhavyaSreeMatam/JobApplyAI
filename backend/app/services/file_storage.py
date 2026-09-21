import uuid
from pathlib import Path

from fastapi import UploadFile


BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]
RESUME_DIRECTORY = BACKEND_DIRECTORY / "data" / "resumes"
MAXIMUM_FILE_SIZE = 5 * 1024 * 1024

# What to serve a stored document as. A tailored resume keeps the format of the
# master it came from, so this cannot be assumed to be a PDF.
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def media_type(path) -> str:
    from pathlib import Path as _Path

    return DOCUMENT_TYPES.get(_Path(path).suffix.casefold(), "application/octet-stream")


async def store_resume(file: UploadFile) -> str:
    original_name = file.filename or ""

    if Path(original_name).suffix.lower() != ".pdf":
        raise ValueError("Resume must be a PDF file.")

    content = await file.read(MAXIMUM_FILE_SIZE + 1)

    if len(content) > MAXIMUM_FILE_SIZE:
        raise ValueError("Resume cannot exceed 5 MB.")

    if not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a valid PDF.")

    RESUME_DIRECTORY.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4()}.pdf"
    stored_path = RESUME_DIRECTORY / stored_name
    stored_path.write_bytes(content)

    return f"data/resumes/{stored_name}"