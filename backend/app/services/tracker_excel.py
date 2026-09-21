"""The application tracker workbook.

The Applications sheet mirrors the column layout the user already tracks by hand,
so the export drops straight into their existing workflow:

    Company Name | Job Title | Job posting URL | Location | Date Applied |
    Application status | Resume version Used | Email Used | Cover Letter

Everything this app knows that a manual sheet cannot (job-match score, keyword
coverage, which source found the job) follows in extra columns after those nine,
so the familiar ones stay leftmost and the extras can be hidden or deleted.

Rebuilt from the database on every download.
"""
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.candidate import CandidateProfile
from app.models.job import Job
from app.models.package import ApplicationPackage
from app.services.spreadsheet_safety import is_web_link, protect_text_cells

BACKEND_DIR = Path(__file__).resolve().parents[2]
EXPORT_DIRECTORY = BACKEND_DIR / "exports"
EXPORT_PATH = EXPORT_DIRECTORY / "job_applications.xlsx"

HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
LINK_FONT = Font(color="0563C1", underline="single")

STATUS_COLORS = {
    "applied": "C6EFCE",
    "interview": "BDD7EE",
    "offer": "A9D08E",
    "ready_for_review": "FFF2CC",
    "preparing": "FFF2CC",
    "approved_queued": "FCE4D6",
    "employer_rejected": "F8CBAD",
    "submission_failed": "FFC7CE",
    "rejected_by_user": "E7E6E6",
    "withdrawn": "E7E6E6",
    "position_closed": "E7E6E6",
}

# The user's own columns first, then what this app adds.
TRACKER_HEADERS = [
    "Company Name", "Job Title", "Job posting URL", "Location", "Date Applied",
    "Application status", "Resume version Used", "Email Used", "Cover Letter",
]
# "Match Score" rather than "ATS Score": it is this app's measure of how well
# the resume fits the posting, not a number any employer produces or sees.
EXTRA_HEADERS = ["Match Score", "Date Posted"]

# The states the tracker uses, offered as a dropdown on every row so a status can
# be changed in Excel without typing. Colours match the ones used in the app.
STATUS_CHOICES = [
    "", "Applied", "Online Assessment", "Interview", "Offer", "Rejected", "Archived",
]
CHOICE_COLORS = {
    "Applied": "C6EFCE",
    "Online Assessment": "BDD7EE",
    "Interview": "FFF2CC",
    "Offer": "A9D08E",
    "Rejected": "F8CBAD",
    "Archived": "E4DFEC",
}

JOB_HEADERS = [
    "Company Name", "Job Title", "Job posting URL", "Location", "Date Posted",
]

# Internal state -> one of STATUS_CHOICES. Anything still in progress maps to a
# blank cell, the same way an untouched row is left blank by hand.
STATUS_LABELS = {
    "applied": "Applied",
    "interview": "Interview",
    "offer": "Offer",
    "employer_rejected": "Rejected",
    "rejected_by_user": "Archived",
    "withdrawn": "Archived",
    "position_closed": "Archived",
    "preparing": "",
    "ready_for_review": "",
    "approved_queued": "",
    "needs_human_input": "",
    "applying": "",
    "submission_failed": "",
}


def _date(value) -> str:
    return value.strftime("%m/%d/%Y") if isinstance(value, datetime) else ""


def _datetime(value) -> str:
    return value.strftime("%m/%d/%Y %H:%M") if isinstance(value, datetime) else ""


def _status(value) -> str:
    return getattr(value, "value", str(value or ""))


def _filename(path: str | None) -> str:
    return Path(path).name if path else ""


def _style(worksheet, widths: dict[int, int], freeze: str = "A2") -> None:
    for cell in worksheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    worksheet.row_dimensions[1].height = 30
    worksheet.freeze_panes = freeze
    if worksheet.max_row > 1:
        worksheet.auto_filter.ref = worksheet.dimensions
    for index, width in widths.items():
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _hyperlink(worksheet, row: int, column: int, url: str) -> None:
    if not url or not is_web_link(url):
        return
    cell = worksheet.cell(row=row, column=column)
    cell.hyperlink = url
    cell.font = LINK_FONT


def build_workbook(database: Session) -> Path:
    EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()

    profile_row = database.get(CandidateProfile, "local")
    default_email = (profile_row.data or {}).get("email", "") if profile_row else ""

    # ---------------------------- Applications ----------------------------
    sheet = workbook.active
    sheet.title = "Applications"
    sheet.append(TRACKER_HEADERS + EXTRA_HEADERS)

    records = database.execute(
        select(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .order_by(Application.created_at.desc())
    ).all()

    status_counts: dict[str, int] = {}
    for application, job in records:
        package = database.get(ApplicationPackage, application.id)
        raw_status = _status(application.status)
        status_counts[raw_status] = status_counts.get(raw_status, 0) + 1
        shown_status = STATUS_LABELS.get(raw_status, "")

        answers = application.answers_json or {}
        email_used = answers.get("email") if isinstance(answers.get("email"), str) else ""

        sheet.append([
            job.company,
            job.title,
            job.application_url,
            job.location or "",
            _date(application.applied_at),
            shown_status,
            _filename(application.resume_path),
            email_used or default_email,
            _filename(application.cover_letter_path),
            round(package.ats_score, 1) if package and package.ats_score is not None else "",
            _date(job.posted_at),
        ])
        _hyperlink(sheet, sheet.max_row, 3, job.application_url)
        colour = CHOICE_COLORS.get(shown_status)
        if colour:
            sheet.cell(row=sheet.max_row, column=6).fill = PatternFill(
                fill_type="solid", fgColor=colour
            )

    _style(sheet, {
        1: 26, 2: 42, 3: 52, 4: 26, 5: 13, 6: 19, 7: 38, 8: 26, 9: 32, 10: 10, 11: 13,
    }, freeze="C2")

    # Dropdown on the status column for every row, plus room to add new ones.
    validation = DataValidation(
        type="list",
        formula1='"' + ",".join(c for c in STATUS_CHOICES if c) + '"',
        allow_blank=True,
        showDropDown=False,   # openpyxl inverts this: False means "show the arrow"
    )
    validation.prompt = "Pick the current stage"
    validation.promptTitle = "Application status"
    validation.error = "Choose one of the listed statuses."
    validation.errorTitle = "Not a known status"
    sheet.add_data_validation(validation)
    validation.add(f"F2:F{max(sheet.max_row, 1) + 300}")

    # ------------------------------- Summary ------------------------------
    summary = workbook.create_sheet("Summary")
    summary.append(["Metric", "Value"])
    scored = [
        package.ats_score
        for application, _ in records
        if (package := database.get(ApplicationPackage, application.id)) and package.ats_score is not None
    ]
    total_jobs = len(database.scalars(select(Job.id)).all())
    summary.append(["Total applications", len(records)])
    summary.append(["Applications submitted", status_counts.get("applied", 0)])
    summary.append(["Jobs discovered", total_jobs])
    summary.append(["Average match score", round(sum(scored) / len(scored), 1) if scored else ""])
    summary.append(["Highest match score", max(scored) if scored else ""])
    summary.append(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")])
    summary.append([])

    summary.append(["Application status", "Count"])
    header_row = summary.max_row
    for column in (1, 2):
        cell = summary.cell(row=header_row, column=column)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT

    for status, count in sorted(status_counts.items(), key=lambda kv: -kv[1]):
        summary.append([STATUS_LABELS.get(status, status) or "In progress", count])
        colour = STATUS_COLORS.get(status)
        if colour:
            summary.cell(row=summary.max_row, column=1).fill = PatternFill(
                fill_type="solid", fgColor=colour
            )
    _style(summary, {1: 28, 2: 16}, freeze="A2")

    # -------------------------- Jobs not applied to ------------------------
    jobs_sheet = workbook.create_sheet("Jobs Discovered")
    jobs_sheet.append(JOB_HEADERS)
    applied_ids = {job.id for _, job in records}
    for job in database.scalars(
        select(Job).order_by(Job.discovered_at.desc())
    ).all():
        if job.id in applied_ids:
            continue
        jobs_sheet.append([
            job.company, job.title, job.application_url, job.location or "",
            _date(job.posted_at),
        ])
        _hyperlink(jobs_sheet, jobs_sheet.max_row, 3, job.application_url)
    _style(jobs_sheet, {1: 26, 2: 44, 3: 54, 4: 26, 5: 13})

    protect_text_cells(workbook)
    temporary = EXPORT_DIRECTORY / "job_applications.tmp.xlsx"
    workbook.save(temporary)
    temporary.replace(EXPORT_PATH)
    return EXPORT_PATH
