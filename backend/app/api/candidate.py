from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from app.db.dependencies import get_db
from app.models.application import Application, ApplicationStatus
from app.models.candidate import CandidateProfile
from app.schemas.candidate import ProfileData, ProfileUpdate
from app.services.candidate_engine import AUTH_KEYS
from app.services import profile_merge

router = APIRouter(prefix="/agent", tags=["Candidate Agent"])


def profile_row(database):
    row = database.get(CandidateProfile, "local")
    if not row:
        raise HTTPException(409, "Save your candidate profile first")
    return row




@router.get("/profile")
def get_profile(database: Session = Depends(get_db)):
    row = database.get(CandidateProfile, "local")
    return {"revision": row.revision if row else 0,
            "data": row.data if row else ProfileData().model_dump(mode="json")}


@router.put("/profile")
def save_profile(body: ProfileUpdate, database: Session = Depends(get_db)):
    row = database.get(CandidateProfile, "local")
    if body.revision != (row.revision if row else 0):
        raise HTTPException(409, "Profile changed in another window; reload before saving")
    data = body.data.model_dump(mode="json")
    if row is None:
        row = CandidateProfile(id="local", data=data, revision=1)
        database.add(row)
    else:
        row.data, row.revision = data, row.revision + 1
    database.commit()
    return {"revision": row.revision, "data": row.data}


@router.post("/profile/confirm-authorization")
def confirm_authorization(database: Session = Depends(get_db)):
    """Record that the candidate has just re-checked their work-authorisation answers.

    The freshness rule needs a date to work from, and nothing could set one: the
    answer engine refused every authorisation question because
    `authorization_reviewed_on` was never written, and the only way it could have
    been written was the start/end date fields the candidate asked to have
    removed. This records the one fact the rule actually needs - that a person
    looked at the saved answers today - and adds no date fields back.
    """
    from datetime import date

    row = database.get(CandidateProfile, "local")
    if row is None or not row.data:
        raise HTTPException(409, "Save your profile first.")
    data = dict(row.data)
    if data.get("authorized_to_work") is None:
        raise HTTPException(
            422,
            "Set your work-authorisation answers in Profile before confirming them.",
        )
    data["authorization_reviewed_on"] = date.today().isoformat()
    row.data = data
    flag_modified(row, "data")
    row.revision += 1
    database.commit()
    return {
        "revision": row.revision,
        "reviewed_on": data["authorization_reviewed_on"],
        "message": (
            "Confirmed today. Your saved work-authorisation answers will be used on "
            "forms until they need re-checking."
        ),
    }


@router.post("/profile/confirm-skills")
def confirm_skills(body: dict, database: Session = Depends(get_db)):
    """Mark machine-added skills as ones the candidate stands behind.

    Skills added from job descriptions were being treated as evidence the moment
    they were written. They are kept as pending until confirmed here, and only
    confirmed skills are usable in a document or on a form.
    """
    row = database.get(CandidateProfile, "local")
    if row is None or not row.data:
        raise HTTPException(409, "Save your profile first.")
    wanted = [str(s) for s in (body or {}).get("skills") or []]
    if not wanted:
        raise HTTPException(400, "Name the skills you are confirming.")
    row.data = profile_merge.confirm_skills(dict(row.data), wanted)
    flag_modified(row, "data")
    row.revision += 1
    database.commit()
    return {
        "revision": row.revision,
        "confirmed": wanted,
        "pending": profile_merge.unconfirmed_skills(row.data),
    }


@router.get("/profile/pending-skills")
def pending_skills(database: Session = Depends(get_db)):
    """Skills no document and no person has backed, awaiting confirmation."""
    row = database.get(CandidateProfile, "local")
    data = dict(row.data) if row and row.data else {}
    return {
        "pending": profile_merge.unconfirmed_skills(data),
        "evidenced": len(profile_merge.evidenced_skills(data)),
    }
