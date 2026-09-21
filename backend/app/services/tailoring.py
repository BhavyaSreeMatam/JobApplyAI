"""Claude-powered job analysis, resume tailoring, and cover letters.

Grounding rule enforced throughout: the model may re-word, re-order, re-emphasize
and re-frame the candidate's own verified facts using the employer's vocabulary.
It may never invent an employer, a date, a degree, a metric, or a skill. Anything
it cannot support is returned in `keywords_unsupported` instead of being written.
"""
import json
import re
from difflib import SequenceMatcher

from app.core import settings
from app.schemas.tailoring import (
    CoverLetter,
    DraftedAnswers,
    JobAnalysis,
    ParsedProfile,
    ResumeEntry,
    TailoredEdits,
    TailoringPlan,
    TailoredResume,
)
from app.services import claude_client, job_match
from app.services.workday import format_dates

ANALYST_SYSTEM = """You break a job description into the four parts that get judged
separately. Do not blur them together - each is used for a different purpose.

1. `keywords` - terms a resume screen would literally match on: programming
   languages, frameworks, libraries, platforms, tools, databases, methods,
   certifications, domain nouns. Use the employer's own spelling.
   - Never emit soft skills ("communication", "team player") or generic filler
     ("fast-paced", "self-starter"). They are not screenable.
   - Mark importance honestly: `required` only for stated must-haves, `preferred`
     for clearly desired items, `nice_to_have` for the rest.
   - Prefer the specific over the generic: "PyTorch" over "ML frameworks".
   - Include obvious multi-word phrases as single terms ("distributed systems").

2. `responsibilities` - what the person will actually DO, one short line each,
   close to the posting's own wording. Typically 5-12 lines. These are matched
   against the candidate's accomplishments, not against their skills list, so
   write them as work ("build retrieval pipelines over internal documents"), not
   as qualities.

3. `eligibility` - pass/fail conditions that no amount of keyword matching can
   make up for: work authorisation or sponsorship, citizenship, security
   clearance, a required licence or certification, a required degree level, and
   location or onsite/hybrid expectations. One line each, quoting the posting.
   Return an empty list when the posting states none. Never invent one.

4. `hard_requirements` - the stated must-haves in plain language, including the
   years of experience if a number is given.

5. `outcomes` - what the posting says good work looks like here: reliability,
   accuracy, latency, delivery speed, customer experience, scale. One line each.
   Empty when the posting does not say.

6. `company_context` - two or three sentences on the product, its customers, and
   the problem this team owns, taken only from the posting. This is what makes a
   cover letter specific instead of flattering. Write nothing you cannot point
   to in the text.

`seniority` should carry the level the posting implies, with the number of years
if one is stated."""


SELECTOR_SYSTEM = """You decide what belongs on one candidate's resume for one job,
and re-word what stays. You are a selector first and an editor second.

The candidate's master resume is a superset written for every role they might
apply to. Most of it is not relevant here. Keeping all of it is the failure mode
you exist to prevent: a long resume buries the evidence that matters under
evidence that does not.

You return one decision per line. Lines you do not name are kept unchanged, so
name every line you want shortened or dropped.

ACTIONS
- keep      the line earns its space; return its text unchanged
- compress  the line is worth keeping but not at this length; return it shorter
- remove    the line does not earn its space for THIS job

PRIORITY: 1 for evidence the employer's main requirements rest on, 5 for a line
kept only for context. This decides what goes first if the resume runs long, so
be honest - marking everything 1 means the trimming happens arbitrarily.

SUPPORTS: name the requirement this line is being kept for, in a few words. For
a removal, say why it goes. This is internal and never printed.

WHAT TO KEEP
- Direct relevance to the stated responsibilities and required qualifications.
- Demonstrated impact, technical depth, and ownership over generic duties.
- Distinct evidence. Two bullets making the same point are one bullet.
- Transferable work that supports the role even when the posting's exact words
  are absent. Do not drop it for lacking the right vocabulary.
- Uniquely relevant older work. Age alone is not a reason to cut.
- Essential education. Coursework, GPA, awards and certifications only where they
  materially strengthen this application or the posting asks for them.
- Publications, concisely, with their status exactly as written.

EVIDENCE THAT OUTRANKS A SKILLS LIST. These take years to earn, usually occupy
one or two lines, and are the strongest reasons to interview anyone. Keep them
whenever the posting touches them at all, and take the space from the skills
section instead:
- a publication, with its venue and authorship position
- mentoring, teaching, or bringing other engineers up to speed
- shipping something to production, and operating it
- evaluating a model or a system against real measurements
- implementing or training models rather than only using them
A one-page resume that dropped the publication to keep a fourth line of
technology names has made the candidate look weaker, not shorter.

WHAT TO REMOVE
- Repeated claims, unrelated technologies, generic soft-skill lists, weak bullets
  with no result or method, and redundant projects.
- Skill-list entries with nothing to do with this role. Trim the list; do not
  delete a whole skills line that still carries relevant terms. Skills are the
  cheapest content on the page - cut here first and hardest.
- A project heading whose bullets you removed. A title with nothing under it is
  not evidence; remove the entry as well.

EMPLOYMENT: prefer compressing a less relevant job to its entry line with one
bullet, or none, over removing it - that keeps the career history readable.
Removing a job entirely is allowed when it adds nothing, but never re-word the
remaining entries to suggest the history is complete.

BUDGETS, as ceilings and not quotas:
- 3-4 bullets for the roles closest to this job, 0-2 for the rest
- 2-3 projects, 1-2 bullets each, chosen to add evidence the jobs do not already
- one summary line, and only if it says something specific and supported about
  fitting THIS role. A generic objective is worse than no summary; remove it.

RE-WORDING what stays:
- Lead with a strong past-tense verb. Say what was built or changed, by what
  method, at what scope, to what end - where the source line supports it.
- Use the employer's vocabulary only where it is factually the same thing.
- Keep every real metric and every meaningful technical detail.
- Stay within each line's max_chars.
- Never keyword-stuff. A term appearing once where it belongs beats five times.

ENTRY LINES (an employer, a job title, a degree, a project name with its dates)
take only keep or remove. Never re-word one: those are facts of record.

GROUNDING - the whole point:
- Every word you write must be supported by the line you are editing or by the
  candidate's verified profile. The job description tells you what the employer
  wants; it is never evidence that the candidate has it.
- Never invent a skill, tool, metric, number, scale, credential, date, title,
  publication, or a length of experience.
- Never infer one technology from another. PyTorch experience is not TensorFlow
  experience.
- Anything the posting requires that this candidate genuinely cannot support goes
  in `unsupported_requirements`. That list is internal. Never write around a gap.

TAGS: lines arrive with <b>, <i>, <u> around bold, italic and underlined parts.
Keep them around the same words. Never add one, never leave one unclosed."""


WORD_EDITOR_SYSTEM = """You tailor a resume by editing sentences. You do not write
resumes, you do not restructure them, and you never see or change their layout.

You are given the candidate's own resume as numbered lines, and one job posting.
You return only the lines whose WORDING should change. Every line you do not
return stays exactly as it is.

WHAT YOU MAY DO TO A LINE:
- Add a term from the posting where it accurately names work the line already
  describes. "Built REST services in Python" for a job asking for microservices
  can become "Built Python microservices (REST)".
- Remove wording irrelevant to this job to make room for what matters.
- Use the employer's vocabulary for the same thing: "Postgres" -> "PostgreSQL",
  "document search" -> "retrieval-augmented generation (RAG)" ONLY if that is
  genuinely what was built.
- Lead with a strong past-tense verb, and follow the shape:
  action + what was built or changed + method or technology + result or purpose.
- Keep the line close to its original length. Each line carries a max_chars
  budget; stay within it. This is not a style preference - the candidate chose
  how long their resume is, and wording that overflows a line re-wraps it and
  repaginates the document. An edit over budget is discarded, so an edit that
  fits is worth more than a better one that does not.

WHAT YOU MUST NOT DO:
- Do not invent a technology, a metric, a number, a scale, a team size, a
  deployment, a customer count, or a duration. Every number in your output must
  already be in the line you are editing.
- Do not turn a prototype into a production service, a contribution into
  leadership, an internship into a staff role, or coursework into experience.
- Do not add a line, remove a line, merge two lines, or split one.
- Do not touch punctuation-only or formatting-only details for their own sake.
- Do not return a line you are not actually improving for THIS posting.

THE TAGS: lines arrive with <b>, <i> and <u> around the parts that are bold,
italic or underlined. Keep them exactly where they are around the same words. If
you re-word inside a tagged span, the tags stay around the re-worded span. Never
add a tag that was not there, and never leave one unclosed.

SKILLS LINES: these are the cheapest and most honest place to add a term. When
the posting asks for something the candidate has used - the evidence is in their
own bullets, or they have told you they have used it - add it to the relevant
skills line in the employer's spelling. Adding it there is enough; do not then
work the same term into a bullet as well.

`gaps`: requirements from the posting that this resume genuinely cannot support
in any wording. These are reported to the candidate, never written in. A missing
keyword can be fixed by wording; a missing qualification cannot, and pretending
otherwise is what gets someone caught in an interview.

`skills_added`: the terms you added to a skills line, so the candidate can see
exactly what changed.

`notes`: two or three sentences on what you emphasised and why, in plain
language."""

WRITER_SYSTEM = """You re-word one resume for one job description, working strictly
from the candidate's verified profile.

You are editing sentences, NOT redesigning a resume. The candidate chose what goes
on the page and in what order. Your job is to make each sentence speak the
employer's language.

WHAT YOU MUST NOT CHANGE - the shape of the document:
- Keep EVERY entry in the profile. Do not drop one, however irrelevant it looks.
- Keep the entries in the profile's order, each in its own section.
- Keep the SAME NUMBER of bullets in every entry, in the SAME order. One profile
  bullet in, one rewritten bullet out. Never merge two, never split one, never
  drop one, never invent one.
- Copy every heading, organization, location and date EXACTLY as the profile has
  them. Not reformatted, not abbreviated, not corrected.

WHAT YOU SHOULD CHANGE - the words inside each sentence:
- Rewrite each bullet using the employer's vocabulary where it genuinely describes
  the same work. If the profile says "built REST services in Python" and the job
  asks for "microservices", "built Python microservices (REST)" is legitimate.
- Lead every bullet with a strong past-tense action verb.
- Keep every real metric exactly as written, and surface metrics buried in prose.
- Keep each rewritten bullet close to the length of the original.
- Write a summary that uses the job's own terminology for real experience.
- Write a headline matching the job's normalized title ONLY if the candidate's
  experience genuinely supports that level. Do not inflate seniority.

SKILLS - the one place a new term may legitimately appear:
- Keep every skill the profile lists. Reorder them so the ones this job asks for
  come first, and use the employer's spelling for a skill the candidate already
  has ("Postgres" -> "PostgreSQL").
- Where the profile's skills list omits a technology the candidate's own entries
  clearly show they used, add it. Listing it is not a new claim; the evidence is
  already in their experience.
- Adding a skill to the skills list is all that is needed. Do not then rewrite a
  bullet to work the term in - that is where invention starts.

ABSOLUTE GROUNDING RULES - violating any of these makes the resume fraudulent:
- Never invent or alter an employer, job title, institution, degree, or date.
- Never invent a metric or number. Use only numbers already in the profile.
- Never describe work the candidate did not do, in any words.
- Never add a job, project, publication or credential that is not in the profile.

OUTPUT SHAPE:
- `entries` is one flat list holding every experience, project, education and
  publication item. Each entry's `section` field says which it is: "experience",
  "projects", "education" or "publications".
- Emit them in the profile's own order, grouped by section.
- Every field is required. Use an empty string or empty list where a value does
  not apply - for example an education entry usually has no bullets.

For each important job keyword the profile genuinely cannot support anywhere, put
it in `keywords_unsupported`. That list is shown to the candidate as a gap report.
An honest 78 beats a fabricated 95."""


def _profile_block(profile: dict) -> str:
    return json.dumps(profile, indent=2, default=str, sort_keys=True)


def _job_block(job) -> str:
    return json.dumps(
        {
            "title": job.title,
            "company": job.company,
            "location": job.location or "",
            "description": (job.description or "")[:24000],
        },
        indent=2,
    )


def analyze_job(job) -> dict:
    """Extract screenable keywords and requirements from a job description."""
    analysis = claude_client.parse(
        system=ANALYST_SYSTEM,
        user="Analyse this job posting for ATS screening.\n\n" + _job_block(job),
        output_format=JobAnalysis,
        effort="medium",
        max_tokens=8000,
    )
    return analysis.model_dump()


def _render_text(resume: TailoredResume, profile: dict) -> str:
    """Plain-text rendering used for scoring before the PDF exists."""
    lines = [
        f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
        " | ".join(
            str(profile[k]) for k in ("email", "phone", "location") if profile.get(k)
        ),
    ]
    for key in ("linkedin", "website", "publications_url"):
        if profile.get(key):
            lines.append(str(profile[key]))
    if resume.summary:
        lines += ["", "Summary", resume.summary]
    if resume.skills:
        lines += ["", "Skills", ", ".join(resume.skills)]
    for title in ("Experience", "Projects", "Education", "Publications"):
        entries = resume.section(title.casefold())
        if not entries:
            continue
        lines += ["", title]
        for entry in entries:
            head = " | ".join(
                x for x in (entry.heading, entry.organization, entry.location, entry.dates) if x
            )
            lines.append(head)
            lines += ["• " + b for b in entry.bullets]
    return "\n".join(lines)


def select_for_job(resume, job, analysis: dict, page_target: int,
                   extra_skills: list[str] | None = None,
                   shortfall: str = "") -> dict:
    """Choose what goes on the resume for this job, and re-word what stays.

    One call. It returns decisions rather than a document, which is what keeps
    the layout the candidate's and the cost low, and it returns a priority per
    line so running long can be fixed without asking again.
    """
    items = resume.evidence()
    if not items:
        raise ValueError(
            "No content could be read from your master resume. If it is a scanned "
            "image, export a text-based file - an ATS cannot read it either."
        )
    config = settings.load()
    listed = "\n".join(
        f"[{item['index']}] {item['kind'].upper()}"
        + ("" if item["rewritable"] else " (fact of record - keep or remove only)")
        + f" | max_chars: {item['max_chars']}"
        + (f" | section: {item['section']}" if item["section"] else "")
        + (f" | entry: {item['entry']}" if item["entry"] else "")
        + f"\n{item['text']}"
        for item in items
    )
    request = (
        "JOB POSTING:\n" + _job_block(job)
        + "\n\nHOW THIS JOB BREAKS DOWN:\n" + json.dumps(analysis, indent=2)
        + (
            "\n\nTHE CANDIDATE HAS CONFIRMED THEY HAVE USED THESE, even though the "
            "resume does not list them. Adding any of them to a skills line is "
            "accurate:\n" + json.dumps(sorted(extra_skills), indent=2)
            if extra_skills else ""
        )
        + f"\n\nTARGET LENGTH: {page_target} page"
        + ("s" if page_target != 1 else "")
        + f". The master resume runs to {resume.page_count} pages, so this is a "
        "selection exercise, not an editing one."
        + (f"\n\n{shortfall}" if shortfall else "")
        + "\n\nTHE CANDIDATE'S MASTER RESUME, one line per item:\n" + listed
        + "\n\nReturn a decision for every line you want compressed or removed."
    )
    plan = claude_client.parse(
        system=SELECTOR_SYSTEM,
        user=request,
        output_format=TailoringPlan,
        effort=config["effort"],
        max_tokens=16000,
    )
    valid = {item["index"] for item in items}
    decisions = [
        decision.model_dump() for decision in plan.decisions if decision.index in valid
    ]
    return {
        "decisions": decisions,
        "skills_added": plan.skills_added,
        "unsupported_requirements": plan.unsupported_requirements,
        "notes": plan.notes,
    }


def tailor_master(resume, job, analysis: dict, extra_skills: list[str] | None = None) -> dict:
    """Re-word one master resume for one job. One model call, edits only.

    The old path regenerated the whole resume as structured data and then
    rendered a new document from it, twice over if the first draft scored badly.
    That cost several thousand output tokens a go and produced a resume that was
    not the candidate's - different layout, different emphasis, different
    document. This asks for the changed sentences and nothing else, which is
    both what was wanted and roughly a fifth of the tokens.
    """
    blocks = resume.blocks()
    if not blocks:
        raise ValueError(
            "No editable lines were found in your master resume. It may be a "
            "scanned image rather than real text."
        )
    config = settings.load()
    listed = "\n".join(
        f"[{b['index']}] {b['kind'].upper()} | max_chars: {b['max_chars']}"
        + (f" | section: {b['section']}" if b["section"] else "")
        + (f" | entry: {b['entry']}" if b["entry"] else "")
        + f"\n{b['text']}"
        for b in blocks
    )
    request = (
        "JOB POSTING:\n" + _job_block(job)
        + "\n\nHOW THIS JOB BREAKS DOWN:\n" + json.dumps(analysis, indent=2)
        + (
            "\n\nTHE CANDIDATE HAS CONFIRMED THEY HAVE USED THESE, even though the "
            "resume does not list them. Adding any of them to a skills line is "
            "accurate:\n" + json.dumps(sorted(extra_skills), indent=2)
            if extra_skills else ""
        )
        + "\n\nTHE CANDIDATE'S RESUME, one editable line per entry:\n" + listed
        + "\n\nReturn only the lines whose wording should change for this job."
    )
    result = claude_client.parse(
        system=WORD_EDITOR_SYSTEM,
        user=request,
        output_format=TailoredEdits,
        # Deliberately not the top effort. This is constrained rewriting against
        # text that is already in front of the model, not open-ended authoring,
        # and the extra thinking bought nothing measurable for the cost.
        effort="medium" if config["effort"] in ("high", "xhigh", "max") else config["effort"],
        max_tokens=8000,
    )
    valid = {b["index"] for b in blocks}
    edits = {
        edit.index: edit.text
        for edit in result.edits
        if edit.index in valid and edit.text.strip()
    }
    changed = resume.apply(edits)
    return {
        "changed": changed,
        "proposed": len(result.edits),
        "skills_added": result.skills_added,
        "gaps": result.gaps,
        "notes": result.notes,
        "reasons": [
            {"index": e.index, "reason": e.reason, "text": e.text}
            for e in result.edits if e.index in valid
        ],
    }


SECTIONS = ("experience", "projects", "education", "publications")


def _original_heading(entry: dict) -> str:
    """The entry's own title line, built the way the profile stores it."""
    heading = str(entry.get("heading") or "").strip()
    if heading:
        return heading
    parts = [str(entry.get(k) or "").strip() for k in ("degree", "major")]
    return ", ".join(p for p in parts if p)


def _match_index(original: dict, drafted: list, used: set[int]) -> int:
    """Which drafted entry the model wrote for this profile entry.

    Best similarity on heading + organization, falling back to position. Matching
    by name rather than trusting the order means a model that reorders entries
    still has its rewritten bullets attached to the right job.
    """
    target = f"{_original_heading(original)} {original.get('organization', '')}".casefold()
    best, best_ratio = -1, 0.0
    for index, entry in enumerate(drafted):
        if index in used:
            continue
        candidate = f"{entry.heading} {entry.organization}".casefold()
        ratio = SequenceMatcher(None, target, candidate).ratio()
        if ratio > best_ratio:
            best, best_ratio = index, ratio
    return best if best_ratio >= 0.55 else -1


def enforce_shape(resume: TailoredResume, profile: dict) -> TailoredResume:
    """Rebuild the resume on the profile's structure, keeping only the new wording.

    The prompt asks the model to preserve every entry and every bullet and change
    only the words inside them. This makes that a guarantee rather than a request:
    entries and their order come from the profile, headings and dates are copied
    verbatim, and a rewritten bullet is used only where one exists for the
    original it replaces. Anything the model dropped comes back as written.
    """
    rebuilt: list[ResumeEntry] = []
    for section in SECTIONS:
        originals = [e for e in (profile.get(section) or []) if isinstance(e, dict)]
        drafted, used = resume.section(section), set()
        for position, original in enumerate(originals):
            index = _match_index(original, drafted, used)
            if index < 0 and position < len(drafted) and position not in used:
                index = position
            source = drafted[index] if index >= 0 else None
            if index >= 0:
                used.add(index)

            bullets = [str(b).strip() for b in (original.get("bullets") or []) if str(b).strip()]
            written = [str(b).strip() for b in (source.bullets if source else [])]
            merged = [
                written[i] if i < len(written) and written[i] else bullets[i]
                for i in range(len(bullets))
            ]
            rebuilt.append(
                ResumeEntry(
                    section=section,
                    heading=_original_heading(original),
                    organization=str(original.get("organization") or ""),
                    dates=format_dates(original),
                    location=str(original.get("location") or ""),
                    bullets=merged,
                )
            )
    resume.entries = rebuilt
    return resume


# A missing keyword worth adding to the skills list, as opposed to a requirement
# that is not a skill at all. Years, degrees, eligibility conditions and soft
# qualities are never skills, whatever the analyst labelled them.
NOT_A_SKILL = (
    r"\d+\s*\+?\s*year|bachelor|master|ph\.?d|degree|diploma|gpa|"
    r"citizen|clearance|authoriz|sponsor|\bvisa\b|licen[cs]e|"
    r"willing|ability|excellent|strong|passion|communicat|interpersonal|"
    r"team[\s-]*player|fast[\s-]*paced|self[\s-]*starter|detail[\s-]*oriented"
)

MAX_ADOPTED_PER_JOB = 12


def adoptable_skills(missing: list, existing: list) -> list[str]:
    """Job skills the candidate has used but never listed.

    The candidate asked for these to be added: they have the experience, it was
    simply left off a resume written for a different role. This only ever touches
    the skills list - it never writes a claim into a bullet - and every term added
    is shown back in Profile so anything that does not belong can be removed.
    """
    have = {str(s).casefold().strip() for s in (existing or [])}
    adopted = []
    for item in missing or []:
        term = str(item.get("term") if isinstance(item, dict) else item or "").strip()
        key = term.casefold()
        if not term or key in have:
            continue
        if len(term) > 40 or len(term.split()) > 4:
            continue
        if re.search(NOT_A_SKILL, key):
            continue
        have.add(key)
        adopted.append(term)
    return adopted[:MAX_ADOPTED_PER_JOB]


def tailor_resume(profile: dict, job, analysis: dict) -> tuple[TailoredResume, dict, list[dict]]:
    """Generate, score, and refine. Returns (resume, final_score, score_history)."""
    config = settings.load()
    target = float(config["ats_target_score"])
    max_passes = max(1, int(config["max_tailor_passes"]))

    base_request = (
        "CANDIDATE PROFILE (the only facts you may use):\n"
        + _profile_block(profile)
        + "\n\nJOB POSTING:\n"
        + _job_block(job)
        + "\n\nHOW THIS JOB BREAKS DOWN:\n"
        + json.dumps(analysis, indent=2)
        + "\n\nProduce the tailored resume."
    )

    def measure(draft: TailoredResume) -> dict:
        return job_match.evaluate(
            _render_text(draft, profile),
            analysis,
            profile=profile,
            job_location=getattr(job, "location", "") or "",
        )

    resume = enforce_shape(
        claude_client.parse(
            system=WRITER_SYSTEM,
            user=base_request,
            output_format=TailoredResume,
            max_tokens=16000,
        ),
        profile,
    )
    score = measure(resume)
    history = [{"pass": 1, "overall": score["overall"], "coverage": score["keyword"]["coverage_percent"]}]

    for attempt in range(2, max_passes + 1):
        if score["overall"] >= target:
            break
        missing = [m["term"] for m in score["keyword"]["missing"]][:12]
        uncovered = [
            item["responsibility"]
            for item in score["match"]["components"]["responsibilities"]["uncovered"]
        ][:8]
        feedback = (
            base_request
            + "\n\nREVISION PASS.\nYour previous draft scored "
            + f"{score['overall']} (keyword coverage {score['keyword']['coverage_percent']}%)."
            + "\n\nKeywords still missing, highest importance first:\n"
            + json.dumps(missing, indent=2)
            + "\n\nResponsibilities in this job the resume shows no evidence for:\n"
            + json.dumps(uncovered, indent=2)
            + "\n\nQuality issues detected:\n"
            + json.dumps(score["issues"], indent=2)
            + "\n\nPrevious draft:\n"
            + resume.model_dump_json(indent=2)
            + "\n\nRevise to raise the score, keeping every entry and every bullet exactly "
            "where it is - change only the words. For each missing keyword, work it into "
            "the bullet describing that work if the candidate really did it, or add it to "
            "`skills` if they have used it; otherwise leave it in keywords_unsupported. "
            "For each uncovered responsibility, re-word the bullet that already describes "
            "that work; if no bullet does, leave it alone. Never fabricate."
        )
        revised = enforce_shape(
            claude_client.parse(
                system=WRITER_SYSTEM,
                user=feedback,
                output_format=TailoredResume,
                max_tokens=16000,
            ),
            profile,
        )
        revised_score = measure(revised)
        history.append(
            {
                "pass": attempt,
                "overall": revised_score["overall"],
                "coverage": revised_score["keyword"]["coverage_percent"],
            }
        )
        # Keep the better draft; a revision can overshoot and lose ground.
        if revised_score["overall"] >= score["overall"]:
            resume, score = revised, revised_score

    return resume, score, history


COVER_SYSTEM = """You write one cover letter connecting two or three of a candidate's
qualifications to what this employer actually needs.

STRUCTURE - four paragraphs, in this order:
1. Opening: name the role, and give one specific, verifiable reason it interests
   the candidate - a product, a responsibility, or a technical problem named in
   the posting. Not the company's reputation.
2. First evidence paragraph: the candidate's strongest relevant example, mapped
   to a major responsibility of the job. Say what they built, how, and to what
   end.
3. Second evidence paragraph: a complementary example - evaluation, delivery,
   collaboration, or operating something - rather than more of the same.
4. Closing: brief interest in discussing what they would contribute, and a thank
   you. No restating of the whole letter.

LENGTH: 250-350 words. Shorter is fine when the argument is complete. Never
longer - a cover letter that runs past one page is not read.

GROUNDING:
- Use only facts in the profile. Never invent experience, metrics, motivation,
  or anything about the company that is not in the posting.
- No placeholders of any kind. No "[Company]", no "[insert X]".
- No generic praise. "Your prestigious and innovative organisation" says nothing
  and reads as a template. Refer to something real and explain the connection.
- Distinguish honestly between building a prototype and running a production
  service, between contributing to a team result and leading it, and between a
  project, an internship, and employment.

FORM: plain prose. No bullet lists, no headers, no salutation line, no sign-off
line - the template adds those.

If the posting clearly wants something the profile cannot support, leave it out
and record it in unsupported_claims_avoided rather than writing around it."""


def write_cover_letter(profile: dict, job, analysis: dict, resume_text: str = "",
                       feedback: str = "") -> CoverLetter:
    """The letter body, argued from the resume that was actually produced.

    `resume_text` is the tailored resume, not the master: the letter has to agree
    with the document the employer will read beside it, and that document is a
    subset. Without it the letter cites work the resume no longer mentions.
    """
    return claude_client.parse(
        system=COVER_SYSTEM,
        user=(
            "CANDIDATE PROFILE:\n"
            + _profile_block(profile)
            + "\n\nJOB POSTING:\n"
            + _job_block(job)
            + "\n\nWHAT THIS EMPLOYER SCREENS FOR:\n"
            + json.dumps(analysis, indent=2)
            + (
                "\n\nTHE TAILORED RESUME THIS LETTER ACCOMPANIES. Every fact, date "
                "and figure you use must appear here or in the profile above, and "
                "the letter must not simply restate these bullets:\n" + resume_text[:12000]
                if resume_text else ""
            )
            + feedback
            + "\n\nWrite the letter body."
        ),
        output_format=CoverLetter,
        effort="medium",
        max_tokens=6000,
    )


PARSER_SYSTEM = """You extract a structured profile from a resume document.

Transcribe faithfully. Copy employers, titles, dates, degrees and bullet text as
written - do not improve, summarise, or infer. Leave a field as an empty string
rather than guessing. Keep every bullet; this becomes the candidate's fact base,
and anything you drop is permanently lost to later tailoring.

OUTPUT SHAPE:
- `entries` is one flat list holding every job, project, degree and publication.
  Each entry's `section` field says which it is: "experience", "projects",
  "education" or "publications".
- `skills` should capture every technology, language and tool named anywhere in
  the document, including ones mentioned only inside a bullet.
- Every field is required; use "" or [] where the resume gives nothing."""


def parse_resume_text(resume_text: str) -> ParsedProfile:
    return claude_client.parse(
        system=PARSER_SYSTEM,
        user="Extract the structured profile from this resume:\n\n" + resume_text[:60000],
        output_format=ParsedProfile,
        effort="medium",
        max_tokens=16000,
    )


DRAFTER_SYSTEM = """You draft answers to employer-specific application questions using
only the candidate's verified profile and the job posting.

GROUNDING - the same rules as the resume:
- Never invent experience, employers, metrics, credentials or opinions.
- Never claim a motivation the profile does not evidence. Interest in the domain
  can be stated from real projects and experience; personal anecdotes cannot.

FOR EACH QUESTION, set `needs_you` to true and leave `answer` empty when the
question asks for something only the candidate can supply:
- a personal fact not in the profile (how to pronounce a name, pronouns, a
  referral name, notice period, deadlines, salary expectations)
- anything requiring consent, agreement, or a legal declaration
- a preference or opinion the profile does not record

Otherwise set `needs_you` to false and write the answer:
- Answer the question actually asked, directly, in the candidate's voice.
- Length: one or two sentences for short prompts; 90-150 words for "why this
  company" or "tell us about" prompts. Never longer.
- Use concrete detail from the profile - real projects, real technologies.
- Plain prose. No bullet lists, no headings, no markdown, no placeholders, and
  never a bracketed blank like [Company].

`reason` is one short sentence explaining the draft, or explaining what you need
from the candidate when needs_you is true. Always return one entry per question,
in the order given, with field_name copied exactly."""


def draft_answers(profile: dict, job, analysis: dict, questions: list[dict]):
    """Draft replies to employer-specific questions. Returns a list of dicts."""
    if not questions:
        return []
    listed = "\n".join(
        f"{index + 1}. field_name={item['field_name']!r} | "
        f"{'long answer' if item.get('multiline') else 'short answer'} | {item['question']}"
        for index, item in enumerate(questions)
    )
    drafted = claude_client.parse(
        system=DRAFTER_SYSTEM,
        user=(
            "CANDIDATE PROFILE:\n" + _profile_block(profile)
            + "\n\nJOB POSTING:\n" + _job_block(job)
            + "\n\nWHAT THIS EMPLOYER SCREENS FOR:\n" + json.dumps(analysis, indent=2)
            + "\n\nQUESTIONS ON THE APPLICATION FORM:\n" + listed
            + "\n\nDraft one entry per question, in order."
        ),
        output_format=DraftedAnswers,
        max_tokens=12000,
    )
    return [item.model_dump() for item in drafted.answers]
