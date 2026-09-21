"""Scoring how well a job fits the candidate.

The score answers "how much of this job do I already match", never "what
fraction of my skills does it use". Those are different questions, and the
second one punishes a full profile: dividing matched skills by total skills
meant listing 74 skills capped the score near zero, while listing five inflated
it. Skill points now saturate - matching enough of a job's stack earns full
marks however long the profile is.
"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.schemas.matching import MatchResult, MatchingRequest

SCORABLE_STATUSES = [
    JobStatus.DISCOVERED,
    JobStatus.ELIGIBILITY_CHECKED,
    JobStatus.RANKED,
]

# Matching this many distinct skills from a posting earns full skill marks.
SKILL_SATURATION = 8

TITLE_POINTS = 38
SKILL_POINTS = 42
LOCATION_POINTS = 10
LEVEL_POINTS = 10
EXCLUDED_PENALTY = 45

EARLY_CAREER_TERMS = (
    "entry level", "entry-level", "new grad", "new graduate", "graduate",
    "junior", "associate", "campus", "early career", "university",
)

# Words too generic to prove a title match on their own.
WEAK_TITLE_WORDS = {
    "engineer", "developer", "manager", "analyst", "scientist", "specialist",
    "senior", "junior", "staff", "lead", "principal", "i", "ii", "iii",
    "the", "of", "and", "for", "a", "an", "in", "at", "new", "grad",
}


def normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9+#.\s-]", " ", (value or "").casefold()).strip()


def _tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[\s\-/,]+", normalize(value)) if t}


def _present(term: str, haystack: str) -> bool:
    """Whole-word containment, so 'go' does not match 'google'."""
    parts = [re.escape(p) for p in re.split(r"\s+", normalize(term)) if p]
    if not parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]{0,3}".join(parts) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def title_score(title: str, target_roles: list[str]) -> float:
    """How closely the job title matches any role the candidate is targeting."""
    if not target_roles:
        return 0.0
    title_tokens = _tokens(title)
    best = 0.0
    for role in target_roles:
        role_tokens = _tokens(role)
        if not role_tokens:
            continue
        overlap = role_tokens & title_tokens
        if not overlap:
            continue
        # A shared "engineer" is not a match; a shared "machine learning" is.
        strong = overlap - WEAK_TITLE_WORDS
        strong_needed = role_tokens - WEAK_TITLE_WORDS
        ratio = (
            len(strong) / len(strong_needed) if strong_needed
            else len(overlap) / len(role_tokens)
        )
        best = max(best, ratio)
    return best * TITLE_POINTS


def calculate_match_score(job: Job, request: MatchingRequest) -> float:
    title = normalize(job.title)
    haystack = normalize(f"{job.title} {job.description}")
    location = normalize(job.location)

    score = title_score(job.title, request.target_roles)
    # With no target roles set, skills carry the weight title would have.
    skill_weight = SKILL_POINTS + (TITLE_POINTS if not request.target_roles else 0)

    matched = {skill for skill in request.skills if skill.strip() and _present(skill, haystack)}
    score += min(len(matched) / SKILL_SATURATION, 1.0) * skill_weight

    if request.preferred_locations:
        if any(_present(place, location) for place in request.preferred_locations):
            score += LOCATION_POINTS
        elif "remote" in location:
            score += LOCATION_POINTS * 0.6
    else:
        score += LOCATION_POINTS

    if any(term in haystack for term in EARLY_CAREER_TERMS):
        score += LEVEL_POINTS

    if any(_present(term, title) for term in request.excluded_title_terms if term.strip()):
        score -= EXCLUDED_PENALTY

    return round(max(0.0, min(score, 100.0)), 1)


def score_available_jobs(database: Session, request: MatchingRequest) -> dict:
    statement = select(Job).where(Job.status.in_(SCORABLE_STATUSES))
    jobs = list(database.scalars(statement).all())
    matches: list[MatchResult] = []

    for job in jobs:
        score = calculate_match_score(job, request)
        job.match_score = score
        job.status = JobStatus.RANKED

        if score >= request.minimum_score:
            matches.append(
                MatchResult(
                    job_id=job.id,
                    company=job.company,
                    title=job.title,
                    location=job.location,
                    score=score,
                    application_url=job.application_url,
                )
            )

    database.commit()
    matches.sort(key=lambda match: match.score, reverse=True)

    return {
        "jobs_scored": len(jobs),
        "recommended_jobs": len(matches),
        "minimum_score": request.minimum_score,
        "top_matches": matches[:25],
    }


def request_from_profile(profile: dict, settings_data: dict) -> MatchingRequest | None:
    """Build a scoring request from the saved profile and settings."""
    skills = [s.strip() for s in (profile.get("skills") or []) if s and s.strip()]
    roles = [r.strip() for r in (settings_data.get("target_roles") or []) if r and r.strip()]
    if not skills and not roles:
        return None

    locations = [
        location.strip()
        for location in (settings_data.get("preferred_locations") or [])
        if location and location.strip()
    ]
    if not locations and profile.get("location"):
        locations = [str(profile["location"]).strip()]

    return MatchingRequest(
        target_roles=roles or ["engineer"],
        skills=skills or ["python"],
        preferred_locations=locations,
        excluded_title_terms=[
            t.strip() for t in (settings_data.get("excluded_terms") or []) if t and t.strip()
        ],
        minimum_score=0,
    )


def rescore_all(database: Session) -> dict:
    """Re-score every job that has not been applied to, from the saved profile."""
    from app.core import settings as app_settings
    from app.models.candidate import CandidateProfile

    row = database.get(CandidateProfile, "local")
    request = request_from_profile(row.data if row else {}, app_settings.load())
    if request is None:
        return {"jobs_scored": 0, "skipped": "Add skills to your profile, or target roles in Settings."}

    # Re-score everything, including jobs already ranked by an earlier run.
    for job in database.scalars(select(Job)).all():
        job.match_score = calculate_match_score(job, request)
        if job.status in SCORABLE_STATUSES:
            job.status = JobStatus.RANKED
    database.commit()
    return {"jobs_scored": len(database.scalars(select(Job.id)).all())}
