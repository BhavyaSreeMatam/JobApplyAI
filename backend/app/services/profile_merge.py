"""Folding an imported resume into a profile without losing what is already there.

Importing used to replace whole sections. Every experience, education, project and
publication array was overwritten by whatever the parse produced, so anything the
candidate had typed themselves - a location the resume never stated, a corrected
job title, an entry from a different resume - disappeared the moment they imported
anything. The candidate's own corrections were the least durable data in the file.

Entries are matched by identity instead: an employer and a role, an institution
and a degree, a project name. A matched pair is merged field by field, and the
rule at every field is the same - a value the candidate entered outranks a value a
parser produced. Blanks are filled, non-blanks are kept, and a genuine
disagreement between two stated values is reported rather than resolved silently.

Provenance is recorded because these three things are not equivalent and were
being treated as though they were:

  document   read out of a resume the candidate uploaded
  user       typed or confirmed by the candidate
  machine    added by this application from a job description

Only `user` and `document` are evidence. `machine` is a suggestion that has not
been confirmed by anybody, and it must never authorise a claim in a document or
on a form.
"""
import re

SECTIONS = ("experience", "education", "projects", "publications")

# Fields that identify an entry rather than describe it.
IDENTITY_FIELDS = {
    "experience": ("organization", "heading"),
    "education": ("organization", "degree"),
    "projects": ("heading",),
    "publications": ("heading",),
}

# Values that disagreeing is worth telling the candidate about, rather than
# quietly preferring one. Dates and titles change the meaning of an application.
FACTUAL_FIELDS = (
    "organization", "heading", "degree", "major", "start_year", "end_year",
    "start_month", "end_month", "gpa", "location",
)

DOCUMENT = "document"
USER = "user"
MACHINE = "machine"


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def skill_key(text: str) -> str:
    """Keep meaningful punctuation: C, C++ and C# are different skills."""
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _skill_source(provenance: dict, skill: str):
    source = provenance.get(skill_key(skill))
    # Old keys discarded punctuation. An ambiguous machine mark must remain
    # pending, but an old approval for C must not also approve C++ or C#.
    if source is None and provenance.get(_key(skill)) == MACHINE:
        return MACHINE
    return source


def identity(section: str, entry: dict) -> str:
    """A stable handle for one entry, so the same job matches itself next time."""
    fields = IDENTITY_FIELDS.get(section, ("heading",))
    parts = [_key(entry.get(field)) for field in fields]
    if not any(parts):
        parts = [_key(entry.get("heading")) or _key(entry.get("organization"))]
    return "|".join(parts)


def _merge_entry(section: str, existing: dict, incoming: dict) -> tuple[dict, list[dict]]:
    """One entry merged, plus any disagreements worth surfacing."""
    merged = dict(existing)
    conflicts = []
    for field, value in (incoming or {}).items():
        if field == "bullets":
            merged[field] = _merge_bullets(existing.get("bullets") or [], value or [])
            continue
        if value in (None, "", [], {}):
            continue
        current = existing.get(field)
        if current in (None, "", [], {}):
            merged[field] = value           # a blank is a gap, not a decision
        elif current == value:
            continue
        elif field in FACTUAL_FIELDS:
            # Both sides state something and they differ. The candidate's value
            # stands; the disagreement is reported so they can settle it.
            conflicts.append({
                "section": section,
                "entry": existing.get("heading") or existing.get("organization") or "",
                "field": field,
                "yours": current,
                "from_document": value,
            })
    return merged, conflicts


def _merge_bullets(existing: list, incoming: list) -> list:
    """Keep every bullet from both sides, in order, without duplicating."""
    seen = {_key(b) for b in existing if str(b).strip()}
    merged = list(existing)
    for bullet in incoming:
        if str(bullet).strip() and _key(bullet) not in seen:
            seen.add(_key(bullet))
            merged.append(bullet)
    return merged


def merge_section(section: str, existing: list, incoming: list) -> tuple[list, list[dict]]:
    """Merge one section. Returns (entries, conflicts).

    Existing entries keep their order and their place; a parsed entry that matches
    one is folded into it; a parsed entry that matches nothing is appended. No
    entry is ever dropped, because a resume that omits a job is not evidence that
    the job did not happen - it is evidence about that resume.
    """
    existing = [e for e in (existing or []) if isinstance(e, dict)]
    incoming = [e for e in (incoming or []) if isinstance(e, dict)]
    by_identity = {identity(section, entry): position
                   for position, entry in enumerate(existing)}

    merged = [dict(entry) for entry in existing]
    conflicts: list[dict] = []
    for entry in incoming:
        handle = identity(section, entry)
        position = by_identity.get(handle)
        if position is None:
            merged.append(dict(entry))
            by_identity[handle] = len(merged) - 1
            continue
        updated, found = _merge_entry(section, merged[position], entry)
        merged[position] = updated
        conflicts.extend(found)
    return merged, conflicts


def merge_profile(current: dict, incoming: dict) -> tuple[dict, list[dict]]:
    """The whole profile after an import. Returns (profile, conflicts)."""
    merged = dict(current or {})
    conflicts: list[dict] = []

    for section in SECTIONS:
        entries, found = merge_section(
            section, current.get(section), incoming.get(section)
        )
        if entries:
            merged[section] = entries
        conflicts.extend(found)

    merged["skills"], skill_provenance = merge_skills(current, incoming)

    for key, value in (incoming or {}).items():
        if key in SECTIONS or key == "skills" or not value:
            continue
        if not merged.get(key):
            merged[key] = value          # fills a gap
        elif merged[key] != value and key in {"email", "phone", "location"}:
            conflicts.append({
                "section": "contact", "entry": "", "field": key,
                "yours": merged[key], "from_document": value,
            })

    provenance = dict(merged.get("provenance") or {})
    provenance["skills"] = skill_provenance
    merged["provenance"] = provenance
    return merged, conflicts


def merge_skills(current: dict, incoming: dict) -> tuple[list, dict]:
    """Skills after an import, with where each one came from.

    A skill named in an imported resume is evidence and is promoted to `document`
    even if it arrived earlier as a machine addition - the resume settles it. One
    that is still only a machine addition stays unconfirmed.
    """
    existing = [str(s).strip() for s in (current.get("skills") or []) if str(s).strip()]
    parsed = [str(s).strip() for s in (incoming.get("skills") or []) if str(s).strip()]
    machine = {skill_key(s) for s in (current.get("adopted_skills") or [])}
    provenance = dict((current.get("provenance") or {}).get("skills") or {})
    previous_marks = dict(provenance)
    parsed_keys = {skill_key(s) for s in parsed}

    merged, seen = [], set()
    for skill in existing + parsed:
        handle = skill_key(skill)
        if handle in seen:
            continue
        seen.add(handle)
        merged.append(skill)
        source = _skill_source(previous_marks, skill)
        if handle in parsed_keys:
            provenance[handle] = DOCUMENT
        elif source in (USER, DOCUMENT, MACHINE):
            provenance[handle] = source
        elif handle in machine:
            provenance[handle] = MACHINE
        else:
            provenance[handle] = USER
    return merged, provenance


def unconfirmed_skills(profile: dict) -> list[str]:
    """Skills no human and no document has ever backed.

    These are what a previous version of this app added from job descriptions and
    then treated as though the candidate had confirmed them. They are kept, and
    shown, and never used as evidence until the candidate says otherwise.
    """
    provenance = (profile.get("provenance") or {}).get("skills") or {}
    machine = {skill_key(s) for s in (profile.get("adopted_skills") or [])}
    pending = []
    for skill in profile.get("skills") or []:
        handle = skill_key(skill)
        source = _skill_source(provenance, skill)
        if source == MACHINE or (source is None and handle in machine):
            pending.append(str(skill))
    return pending


def evidenced_skills(profile: dict) -> list[str]:
    """Skills the candidate or a document stands behind. The only ones usable."""
    pending = {skill_key(s) for s in unconfirmed_skills(profile)}
    return [s for s in (profile.get("skills") or []) if skill_key(s) and skill_key(s) not in pending]


def evidenced_profile(profile: dict) -> dict:
    """A copy for generation/autofill that cannot leak pending skill metadata."""
    data = dict(profile)
    data["skills"] = evidenced_skills(profile)
    data.pop("adopted_skills", None)
    data.pop("provenance", None)
    return data


def confirm_skills(profile: dict, skills) -> dict:
    """Record that the candidate has confirmed these. Returns the updated profile."""
    wanted = {skill_key(s) for s in (skills or [])}
    data = dict(profile)
    provenance = dict(data.get("provenance") or {})
    marks = dict(provenance.get("skills") or {})
    previous_marks = dict(marks)
    for skill in data.get("skills") or []:
        source = _skill_source(previous_marks, skill)
        if source is not None:
            marks[skill_key(skill)] = source
        if skill_key(skill) in wanted:
            marks[skill_key(skill)] = USER
    provenance["skills"] = marks
    data["provenance"] = provenance
    data["adopted_skills"] = [
        s for s in (data.get("adopted_skills") or []) if skill_key(s) not in wanted
    ]
    return data
