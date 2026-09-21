"""How well one resume fits one job - and three other things, kept apart.

There is no universal "ATS score". An applicant tracking system stores and filters
applications; the percentage a resume checker shows you is that checker's own
estimate of resume-to-job-description match, not a number the employer sees and
not a threshold anyone publishes. So this module measures four different things
and refuses to average them into one figure:

  match        Does the candidate's real evidence line up with what the job asks?
  parsing      Can software extract the experience, education and skills at all?
  eligibility  Location, work authorisation, clearances, licences - pass/fail
               conditions that no amount of keyword coverage compensates for.
  quality      Would a recruiter see the relevant accomplishments quickly?

Only `match` is the headline number, and it is called a job-match score. Parsing
and quality are reported next to it as separate gates, and eligibility is reported
as a list of conditions rather than a score, precisely so a strong average cannot
conceal a disqualifying mismatch.

The match weights below are a design choice, not a validated model:

    required skills supported by evidence     40%
    relevant responsibilities                 30%
    experience level                          20%
    preferred qualifications                  10%

A component with nothing to measure - a posting that lists no required skills,
say - is dropped and the remaining weights are renormalised, so an absent signal
never silently scores as zero or as full marks.

Matching is deterministic term overlap, not semantic similarity: every match here
means the words are literally present. That is a floor on what a keyword screen
would find, and it is reproducible, which is what makes it usable as the tailoring
loop's objective.
"""
import re
from datetime import date

from app.services import ats_scorer

MATCH_WEIGHTS = {
    "required_skills": 40.0,
    "responsibilities": 30.0,
    "experience_level": 20.0,
    "preferred_qualifications": 10.0,
}

COMPONENT_LABELS = {
    "required_skills": "Required skills backed by evidence",
    "responsibilities": "Responsibilities you have done",
    "experience_level": "Experience level",
    "preferred_qualifications": "Preferred qualifications",
}

# Words that carry no signal when deciding whether a responsibility line is
# covered. These are on top of the scorer's own stopword list: they are the
# connective tissue of job-description prose specifically.
RESPONSIBILITY_NOISE = {
    "responsible", "responsibilities", "duties", "including", "ensure", "ensuring",
    "support", "supporting", "partner", "partnering", "drive", "driving", "own",
    "owning", "deliver", "delivering", "build", "building", "develop", "developing",
    "design", "designing", "create", "creating", "maintain", "maintaining",
    "collaborate", "collaborating", "contribute", "contributing", "participate",
    "stakeholders", "cross", "functional", "closely", "various", "day",
    "quality", "high", "best", "practices", "end", "scale", "scalable", "robust",
}

# A responsibility line is counted as covered when at least this share of its
# distinctive terms appear in the resume.
RESPONSIBILITY_THRESHOLD = 0.5
MIN_RESPONSIBILITY_TERMS = 2

# Seniority word -> years of experience the wording implies. Used only to judge
# level; "scope" (team size, blast radius, ownership) is not measured here because
# no honest deterministic proxy for it exists in resume text.
SENIORITY_YEARS = [
    ("principal", 10), ("distinguished", 12), ("director", 10), ("head of", 10),
    ("staff", 8), ("lead", 7), ("manager", 7), ("senior", 5), ("sr.", 5), ("sr ", 5),
    ("mid", 3), ("ii", 2), ("associate", 1), ("junior", 1), ("jr.", 1),
    ("entry", 0), ("new grad", 0), ("graduate", 0), ("intern", 0), ("early career", 0),
]

YEARS_REQUIRED = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?year", re.I)


# --------------------------------------------------------------------------- #
# Component 1 & 4 - skills supported by evidence
# --------------------------------------------------------------------------- #

def _skills_component(resume_text: str, keywords: list[dict], wanted: set[str],
                      evidence_text: str | None = None) -> dict:
    """How many of the job's terms the resume supports, and how it supports them.

    A term listed in a skills section and a term demonstrated in a bullet are not
    the same claim, and counting them identically is what let this component -
    labelled "required skills backed by evidence" - be satisfied by a word in a
    list. Both are still counted, because a skills line is legitimate evidence of
    familiarity, but they are reported apart so the difference is visible.
    """
    demonstrated_in = evidence_text if evidence_text is not None else resume_text
    matched, listed_only, missing, seen = [], [], [], set()
    for entry in keywords or []:
        term = (entry.get("term") or "").strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        if entry.get("importance", "preferred") not in wanted:
            continue
        seen.add(key)
        if ats_scorer.phrase_present(term, demonstrated_in):
            matched.append(term)
        elif ats_scorer.phrase_present(term, resume_text):
            listed_only.append(term)
        else:
            missing.append(term)
    total = len(matched) + len(listed_only) + len(missing)
    # A term only in the skills list counts for half: it is stated, not shown.
    earned = len(matched) + 0.5 * len(listed_only)
    return {
        "applicable": total > 0,
        "score": round(earned / total * 100, 1) if total else 0.0,
        "matched": matched,
        "listed_only": listed_only,
        "missing": missing,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Component 2 - responsibilities
# --------------------------------------------------------------------------- #

def _distinctive_terms(line: str) -> set[str]:
    return {
        term for term in ats_scorer.content_tokens(line)
        if term not in RESPONSIBILITY_NOISE and len(term) > 2
    }


def responsibility_coverage(resume_text: str, responsibilities: list[str]) -> dict:
    """Which of the job's stated responsibilities the resume shows evidence for.

    Overlap of distinctive terms, not meaning: "designed retrieval pipelines" and
    "built RAG systems" describe the same work and will not match here. That is a
    deliberate floor - it under-reports rather than crediting work the words do
    not actually show.
    """
    resume_terms = ats_scorer.content_tokens(resume_text)
    covered, uncovered = [], []
    for line in responsibilities or []:
        text = (line or "").strip()
        terms = _distinctive_terms(text)
        if len(terms) < MIN_RESPONSIBILITY_TERMS:
            continue
        overlap = len(terms & resume_terms) / len(terms)
        (covered if overlap >= RESPONSIBILITY_THRESHOLD else uncovered).append(
            {"responsibility": text[:160], "overlap": round(overlap * 100)}
        )
    total = len(covered) + len(uncovered)
    uncovered.sort(key=lambda item: item["overlap"], reverse=True)
    return {
        "applicable": total > 0,
        "score": round(len(covered) / total * 100, 1) if total else 0.0,
        "covered": covered,
        "uncovered": uncovered,
        "total": total,
    }


# --------------------------------------------------------------------------- #
# Component 3 - experience level
# --------------------------------------------------------------------------- #

def _entry_years(entry: dict) -> tuple[int, int]:
    """(start_year, end_year) for one experience entry, 0 where unknown."""
    def year(value) -> int:
        digits = re.search(r"(19|20)\d{2}", str(value or ""))
        return int(digits.group(0)) if digits else 0

    start = year(entry.get("start_year")) or year(entry.get("dates"))
    end = year(entry.get("end_year"))
    if not end and (entry.get("current") or re.search(
        r"present|current", str(entry.get("dates") or ""), re.I
    )):
        end = date.today().year
    if not end:
        # Entries saved before the month/year split keep one string, e.g.
        # "Jun 2021 - Aug 2023"; the second year in it is the end.
        years = re.findall(r"(?:19|20)\d{2}", str(entry.get("dates") or ""))
        end = int(years[-1]) if len(years) > 1 else 0
    return start, end


def _entry_months(entry: dict) -> tuple[int, int] | None:
    """(start, end) as months since year 0, or None when there is no usable range.

    Month precision where the profile has it, year precision where it does not.
    A year with no month is read as the middle of that year rather than January:
    assuming January makes every year-only role look up to eleven months longer
    than anything anyone stated.
    """
    def month_number(month, year) -> int | None:
        text_year = re.search(r"(19|20)\d{2}", str(year or ""))
        if not text_year:
            return None
        digits = re.sub(r"[^\d]", "", str(month or ""))
        index = int(digits) if digits and 1 <= int(digits) <= 12 else 6
        return int(text_year.group(0)) * 12 + index

    start = month_number(entry.get("start_month"), entry.get("start_year"))
    if start is None:
        parsed = re.findall(r"(?:19|20)\d{2}", str(entry.get("dates") or ""))
        if not parsed:
            return None
        start = int(parsed[0]) * 12 + 6
        end = int(parsed[-1]) * 12 + 6 if len(parsed) > 1 else None
    else:
        end = month_number(entry.get("end_month"), entry.get("end_year"))

    if entry.get("current") or re.search(
        r"present|current", str(entry.get("dates") or ""), re.I
    ):
        end = date.today().year * 12 + date.today().month
    if end is None or end < start:
        return None
    return start, end


def candidate_years(profile: dict) -> float:
    """How long the candidate has actually worked, in years.

    Union of the intervals, not the distance between the first and the last.
    Taking `latest end - earliest start` counted every gap between jobs as
    experience: somebody who worked in 2015 and again in 2024 was credited with
    nine years. Merging overlapping roles - a job alongside a degree, two
    part-time positions - stops the opposite error of double counting them.
    """
    spans = []
    for entry in profile.get("experience") or []:
        span = _entry_months(entry if isinstance(entry, dict) else {})
        if span:
            spans.append(span)
    if not spans:
        return 0.0

    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    months = sum(end - start for start, end in merged)
    return round(months / 12.0, 1)


def required_years(analysis: dict) -> int:
    """Years the posting asks for: a stated number wins over a seniority word."""
    text = " ".join(
        str(x) for x in (
            [analysis.get("seniority", ""), analysis.get("normalized_title", "")]
            + list(analysis.get("hard_requirements") or [])
        )
    )
    stated = [int(m) for m in YEARS_REQUIRED.findall(text) if int(m) <= 25]
    if stated:
        return min(stated)
    lowered = text.casefold()
    for word, years in SENIORITY_YEARS:
        if word in lowered:
            return years
    return -1


def experience_component(profile: dict, analysis: dict) -> dict:
    wanted = required_years(analysis)
    held = candidate_years(profile)
    if wanted < 0:
        return {"applicable": False, "score": 0.0, "required_years": None,
                "candidate_years": round(held, 1), "note": "the posting states no level"}
    if wanted == 0:
        return {"applicable": True, "score": 100.0, "required_years": 0,
                "candidate_years": round(held, 1),
                "note": "entry level - no minimum to meet"}
    if held <= 0:
        # Scoring zero here would be a measurement of the profile's date fields,
        # not of the candidate. Drop the component instead and say why.
        return {"applicable": False, "score": 0.0, "required_years": wanted,
                "candidate_years": 0.0,
                "note": "add start and end years to your experience to measure this"}
    ratio = min(1.0, held / wanted)
    # Being over the bar is not a defect, so the component tops out at 100 rather
    # than penalising seniority above what the posting asks for.
    return {
        "applicable": True,
        "score": round(ratio * 100, 1),
        "required_years": wanted,
        "candidate_years": round(held, 1),
        "note": (f"posting implies about {wanted} years; your profile spans "
                 f"{held:.0f}"),
    }


# --------------------------------------------------------------------------- #
# Eligibility - reported, never scored
# --------------------------------------------------------------------------- #

def _sponsorship(profile: dict) -> tuple[str, str]:
    needs = profile.get("requires_sponsorship_now") or profile.get(
        "requires_sponsorship_future"
    )
    if needs is True:
        return "unmet", "your profile says you need sponsorship"
    if profile.get("authorized_to_work") is True:
        return "met", "you are authorised and need no sponsorship"
    return "unknown", "set work authorisation in your profile"


def _authorisation(profile: dict) -> tuple[str, str]:
    if profile.get("authorized_to_work") is True:
        return "met", "your profile says you are authorised to work"
    if profile.get("authorized_to_work") is False:
        return "unmet", "your profile says you are not authorised"
    return "unknown", "set work authorisation in your profile"


def _degree(profile: dict, requirement: str) -> tuple[str, str]:
    held = " ".join(
        f"{entry.get('degree', '')} {entry.get('heading', '')} {entry.get('major', '')}"
        for entry in (profile.get("education") or [])
        if isinstance(entry, dict)
    ).casefold()
    if not held.strip():
        return "unknown", "no education entries in your profile"
    levels = [
        ("phd", r"ph\.?d|doctor"),
        ("master", r"master|m\.?s\.?\b|m\.?eng"),
        ("bachelor", r"bachelor|b\.?s\.?\b|b\.?tech|b\.?e\.?\b"),
    ]
    lowered = requirement.casefold()
    for name, pattern in levels:
        if re.search(pattern, lowered):
            # A higher degree satisfies a lower requirement, so anything at or
            # above the asked-for level counts.
            index = [n for n, _ in levels].index(name)
            for higher, higher_pattern in levels[: index + 1]:
                if re.search(higher_pattern, held):
                    return "met", f"your profile shows a {higher}'s-level degree"
            return "unmet", f"the posting asks for a {name}'s degree"
    return "unknown", ""


def _location(profile: dict, job_location: str, requirement: str) -> tuple[str, str]:
    if re.search(r"remote", (job_location or "") + " " + requirement, re.I):
        return "met", "remote"
    here = " ".join(
        str(profile.get(key) or "") for key in ("city", "state", "location")
    ).casefold()
    target = (job_location or "").casefold()
    if not target or not here.strip():
        return "unknown", "add your city and state to your profile"
    if any(part.strip() and part.strip() in here for part in re.split(r"[,/|]", target)):
        return "met", "the job is where you are"
    if profile.get("relocation") is True:
        return "met", "you are open to relocating"
    if profile.get("relocation") is False:
        return "unmet", f"the job is in {job_location} and you are not relocating"
    return "unknown", "set whether you will relocate in your profile"


ELIGIBILITY_RULES = [
    ("sponsorship", r"without (visa )?sponsorship|not .{0,20}sponsor|no sponsorship|"
                    r"sponsorship (is )?not", lambda p, r, j: _sponsorship(p)),
    ("work authorisation", r"authoriz(ed|ation) to work|legally authoriz|work permit|"
                           r"right to work", lambda p, r, j: _authorisation(p)),
    ("citizenship", r"u\.?s\.? citizen|citizenship (is )?required|permanent resident",
     lambda p, r, j: ("unknown", "only you can confirm citizenship status")),
    ("security clearance", r"security clearance|ts/sci|top secret|secret clearance|"
                           r"public trust",
     lambda p, r, j: ("unknown", "only you can confirm clearance status")),
    ("degree", r"bachelor|master'?s|ph\.?d|degree in|b\.?s\.?\b|m\.?s\.?\b",
     lambda p, r, j: _degree(p, r)),
    ("licence", r"licen[cs]e|certified [a-z]|certification (is )?required",
     lambda p, r, j: ("unknown", "only you can confirm licences held")),
    ("location", r"on-?site|in-?office|hybrid|relocat|must (be )?(located|based)|"
                 r"days? (a|per) week in", lambda p, r, j: _location(p, j, r)),
]


def eligibility_report(profile: dict, analysis: dict, job_location: str = "") -> dict:
    """Pass/fail conditions, listed rather than scored.

    Deliberately kept out of the match score. A resume can match a job perfectly
    and still be disqualified by one of these, and averaging it in would hide
    exactly the thing that matters most.
    """
    conditions, seen = [], set()
    lines = list(analysis.get("eligibility") or []) + list(
        analysis.get("hard_requirements") or []
    )
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        for name, pattern, check in ELIGIBILITY_RULES:
            if not re.search(pattern, text, re.I):
                continue
            if name in seen:
                break
            seen.add(name)
            status, note = check(profile, text, job_location)
            conditions.append({
                "kind": name,
                "requirement": text[:200],
                "status": status,
                "note": note,
            })
            break
    return {
        "conditions": conditions,
        "blocking": [c for c in conditions if c["status"] == "unmet"],
        "needs_you": [c for c in conditions if c["status"] == "unknown"],
    }


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #

# Headings whose content is a list of terms rather than a record of work.
SKILLS_HEADING_LINE = re.compile(
    r"^\s*(technical\s+)?(skills|technologies|competenc\w*|tools)\b.*$", re.I
)


def _demonstrated_text(resume_text: str) -> str:
    """The resume with its skills listings removed.

    Everything left is a sentence about something the candidate did. Matching a
    required technology against this, rather than against the whole document,
    is the difference between "they have used it" and "they typed it in a list".
    """
    kept, skipping = [], False
    for line in (resume_text or "").splitlines():
        if SKILLS_HEADING_LINE.match(line.strip()):
            skipping = True
            continue
        if skipping:
            stripped = line.strip()
            # A new section heading ends the skills block; short all-caps or
            # title-case lines with no sentence punctuation are headings.
            heading = (
                stripped
                and (stripped.isupper() or len(stripped.split()) <= 4)
                and not stripped.endswith((".", ",", ";"))
            )
            if heading:
                skipping = False
            else:
                continue
        kept.append(line)
    return "\n".join(kept)


def match_report(resume_text: str, analysis: dict, profile: dict | None = None) -> dict:
    keywords = analysis.get("keywords") or []
    # The parts of the resume that show work being done, as opposed to the parts
    # that list what the candidate knows.
    evidence_text = _demonstrated_text(resume_text)
    components = {
        "required_skills": _skills_component(resume_text, keywords, {"required"},
                                             evidence_text),
        "responsibilities": responsibility_coverage(
            resume_text, analysis.get("responsibilities") or []
        ),
        "experience_level": experience_component(profile or {}, analysis),
        "preferred_qualifications": _skills_component(
            resume_text, keywords, {"preferred", "nice_to_have"}, evidence_text
        ),
    }
    live = {k: v for k, v in components.items() if v["applicable"]}
    total_weight = sum(MATCH_WEIGHTS[k] for k in live) or 1.0
    score = sum(v["score"] * MATCH_WEIGHTS[k] for k, v in live.items()) / total_weight
    for key, value in components.items():
        value["weight"] = round(MATCH_WEIGHTS[key] / total_weight * 100) if key in live else 0
        value["label"] = COMPONENT_LABELS[key]
    return {"score": round(score, 1), "components": components}


def _as_analysis(value) -> dict:
    """Accept either a full analysis dict or the bare keyword list."""
    if isinstance(value, dict):
        return value
    return {"keywords": list(value or [])}


def evaluate(resume_text, analysis, pdf_path=None, profile=None, job_location=""):
    """The four measures, side by side, with the job-match score as the headline."""
    analysis = _as_analysis(analysis)
    match = match_report(resume_text, analysis, profile)
    keys = ats_scorer.keyword_report(resume_text, analysis.get("keywords") or [])
    parsing = ats_scorer.format_report(resume_text, pdf_path)
    quality = ats_scorer.content_report(resume_text)
    eligibility = eligibility_report(profile or {}, analysis, job_location)

    issues = list(parsing["issues"]) + list(quality["issues"])
    for condition in eligibility["blocking"]:
        issues.insert(0, f"Eligibility: {condition['note']}.")

    return {
        # The headline. Job match only - parsing and quality are gates beside it,
        # not ingredients, so neither can paper over a weak match.
        "overall": match["score"],
        "match": match,
        "keyword": keys,
        "format": parsing,
        "content": quality,
        "eligibility": eligibility,
        "issues": issues,
    }


def evaluate_pdf(pdf_path, analysis, profile=None, job_location=""):
    from pathlib import Path

    path = Path(pdf_path)
    return evaluate(
        ats_scorer.extract_pdf_text(path), analysis, path, profile, job_location
    )
