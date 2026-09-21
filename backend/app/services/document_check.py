"""Checking a tailored resume and cover letter before either is allowed out.

Everything here is deterministic. A model asked to grade its own output will
usually say it did well, and paying for that opinion buys nothing; these checks
compare the rendered documents against the candidate's own source material and
either pass, or say exactly which line failed and why.

Three kinds of check:

  grounding   Every number, and every technology named, must already exist in the
              line it came from, in the master resume, or in the candidate's
              verified skills. This is what stops a re-wording becoming a claim.
  consistency The cover letter may not state a year, a title or a metric the
              resume and profile do not carry.
  rendering   The PDF has to be the length it was meant to be, hold extractable
              text, keep the contact details, and actually not contain the
              content that was removed.

A failure is not fatal on its own: the caller reverts the offending lines and
renders again. It becomes fatal only when it survives the retry budget, because
an unverified document must never be returned silently.
"""
import re

from app.services import ats_scorer

# A claim of quantity. Years like "2024" are handled separately because they are
# dates rather than metrics.
NUMBER = re.compile(r"\d[\d,.]*\s*(?:%|percent|x\b|k\b|m\b|bn\b|\+)?", re.I)
YEAR = re.compile(r"(?:19|20)\d{2}")

# Text that should never survive into a finished document.
PLACEHOLDER = re.compile(
    r"\[[^\]]{2,40}\]|\{\{.*?\}\}|<insert[^>]*>|\bTBD\b|\bTODO\b|\bXXXX?\b|"
    r"\byour company\b|\bcompany name\b|\bposition title\b|lorem ipsum",
    re.I,
)

# Words that are ordinary English wherever they appear, so finding one in a
# re-worded line proves nothing about new claims.
COMMON_WORDS = {
    "the", "and", "for", "with", "from", "into", "over", "using", "used", "built",
    "led", "designed", "developed", "improved", "reduced", "increased", "scaled",
    "team", "teams", "data", "system", "systems", "service", "services", "model",
    "models", "api", "apis", "pipeline", "pipelines", "platform", "engineering",
    "software", "research", "product", "production", "testing", "evaluation",
    "analysis", "learning", "machine", "deep", "real", "time", "based", "end",
    "user", "users", "code", "search", "cloud", "web", "mobile", "database",
}

# Legal suffixes. A letter addressed to "TikTok" names "TikTok Inc." perfectly
# well, and a check that insists on the registered form reports a problem that
# is not one.
LEGAL_SUFFIX = re.compile(
    r"[,\s]+(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|"
    r"s\.a|sa|ag|bv|pty|llp)\.?\s*$",
    re.I,
)

MIN_COVER_WORDS = 200
MAX_COVER_WORDS = 400


def _normalise(word: str) -> str:
    """One word stripped of surrounding punctuation and case, nothing more."""
    return re.sub(r"^[^\w+#]+|[^\w+#]+$", "", str(word or "")).casefold()


def _singular(word: str) -> str:
    """A crude singular, used only alongside the word itself and never instead.

    Both forms go into the vocabulary and both are tried when checking, because
    guessing one way round is wrong either way: strip too eagerly and
    "Kubernetes" becomes "kubernete", strip too timidly and "pipelines" no longer
    matches the "pipeline" in the source.
    """
    return re.sub(r"(?<=[a-z]{3})s$", "", word)


def _vocabulary(text: str) -> set[str]:
    """Every word in some text, with its singular, and the parts of compounds."""
    words = set()
    for raw in re.split(r"[\s,;:()\[\]/|]+", text or ""):
        normalised = _normalise(raw)
        if not normalised:
            continue
        words.update({normalised, _singular(normalised)})
        if "-" in normalised:
            for part in normalised.split("-"):
                if part:
                    words.update({part, _singular(part)})
    return words


def _claim_terms(text: str) -> set[str]:
    """The words in some text that assert something, rather than describe it.

    A proper noun mid-sentence, or anything carrying a digit or a symbol:
    "PyTorch", "Spark", "C++", "K8s". Ordinary prose is excluded deliberately.
    Checking every word instead flagged "embedding-based", "metrics." and
    "standard" as invented technologies, which is noise that would bury the one
    finding that matters - a tool named on the resume that the candidate has
    never touched.

    The limit of this is worth stating: a claim written in lower case slips
    through. The selection prompt writes technology names the way resumes do,
    and the skills a job may add are gated separately, so the exposure is small
    and the false-positive rate is what makes the check usable at all.
    """
    terms = set()
    for sentence in re.split(r"(?<=[.;:!?])\s+|\n", text or ""):
        for position, word in enumerate(sentence.split()):
            bare = re.sub(r"^[^\w+#]+|[^\w+#]+$", "", word)
            if len(bare) < 2:
                continue
            has_symbol = bool(re.search(r"\d|[+#]", bare))
            if position == 0 and not has_symbol:
                continue  # a capital at the start of a sentence proves nothing
            if bare[0].isupper() or has_symbol:
                terms.add(_normalise(bare))
    return {term for term in terms if term and term not in COMMON_WORDS}


# "PyTorch/TensorFlow" and "CNN/RNN/Transformer" are one word to a tokeniser and
# several claims to a reader. Each part is checked on its own, so a list of
# technologies the candidate really has does not read as one invented name.
COMPOUND = re.compile(r"[/\-]")


def _supported(term: str, verified: set[str]) -> bool:
    if term in verified or _singular(term) in verified:
        return True
    parts = [part for part in COMPOUND.split(term) if part]
    return len(parts) > 1 and all(
        part in verified or _singular(part) in verified for part in parts
    )


def _tokens(text: str) -> set[str]:
    return {
        token for token in ats_scorer.tokens(text)
        if len(token) > 2 and token not in COMMON_WORDS
    }


def _strip_contact(text: str) -> str:
    """Take phone numbers and email addresses out before looking for claims.

    Neither is an assertion about the candidate's work, and leaving them in made
    every cover letter report the digits of its own contact line as figures the
    resume did not support.
    """
    without = re.sub(r"[\w.+-]+@[\w.-]+", " ", text or "")
    return re.sub(r"\+?\d[\d\s().-]{6,}\d", " ", without)


def _numbers(text: str) -> set[str]:
    """Quantities in the text, with years and contact details excluded."""
    text = _strip_contact(text)
    found = set()
    for match in NUMBER.finditer(text or ""):
        value = match.group(0).strip().casefold()
        digits = re.sub(r"[^\d]", "", value)
        if not digits or YEAR.fullmatch(digits):
            continue
        # "15 percent" and "15%" are one figure written two ways, and a cover
        # letter spells out what a resume abbreviates. Comparing them literally
        # reported the resume's own metric as invented.
        found.add(re.sub(r"\s+", "", value).replace("percent", "%"))
    return found


def ground_edits(resume, verified_terms: set[str]) -> list[dict]:
    """Lines whose new wording says something their source did not.

    `verified_terms` is everything the candidate has actually evidenced: the
    whole master resume plus their profile skills. A term outside it, appearing
    in a line that was re-worded, is a claim that came from the job description
    rather than from the candidate - which is the one thing tailoring must never
    do.
    """
    failures = []
    for line in resume.lines:
        if line.original is None or line.removed:
            continue
        was = "".join(run.text for run in line.original)
        now = line.text

        invented = _numbers(now) - _numbers(was)
        if invented:
            failures.append({
                "index": line.index,
                "problem": "a number that is not in the source line",
                "detail": ", ".join(sorted(invented))[:80],
            })
            continue

        new_terms = {
            term for term in _claim_terms(now) - _claim_terms(was)
            if not _supported(term, verified_terms)
        }
        if new_terms:
            failures.append({
                "index": line.index,
                "problem": "a term the candidate's own material does not support",
                "detail": ", ".join(sorted(new_terms))[:80],
            })
    return failures


def check_resume(resume, pdf_text: str, page_count: int, page_target: int,
                 profile: dict) -> list[str]:
    """Whether the rendered resume is usable. Returns problems, empty when fine."""
    problems = []
    if page_count < 1:
        problems.append("the resume rendered no pages")
    elif page_count > page_target:
        problems.append(f"{page_count} pages rendered against a target of {page_target}")

    if len(pdf_text.strip()) < 200:
        problems.append("almost no text could be extracted - an ATS would read a blank page")
    if "(cid:" in pdf_text:
        problems.append("the PDF contains unmapped glyphs, which extract as garbage")

    lowered = pdf_text.casefold()
    for field, label in (("email", "email address"), ("phone", "phone number")):
        value = str(profile.get(field) or "").strip()
        if not value:
            continue
        needle = re.sub(r"[^a-z0-9@.]", "", value.casefold())[-9:]
        if needle and needle not in re.sub(r"[^a-z0-9@.]", "", lowered):
            problems.append(f"the {label} is missing from the rendered resume")

    if PLACEHOLDER.search(pdf_text):
        problems.append("the resume still contains placeholder text")

    # What was removed has to be gone. Compared on the distinctive words of each
    # dropped line, because a couple of common words will survive anywhere.
    for line in resume.lines:
        if not line.removed or len(line.text.split()) < 6:
            continue
        distinctive = list(_tokens(line.text))[:8]
        if len(distinctive) >= 4 and sum(
            1 for term in distinctive if term in lowered
        ) == len(distinctive):
            problems.append(
                f"line {line.index} was removed but its text is still in the PDF"
            )
            break
    return problems


def check_cover_letter(body: str, pdf_text: str, page_count: int, job,
                       resume_text: str, profile: dict, master_text: str = "") -> list[str]:
    """Whether the cover letter is consistent with the resume and fit to send."""
    problems = []
    words = len((body or "").split())
    if words < MIN_COVER_WORDS:
        problems.append(f"the cover letter is {words} words - too thin to make a case")
    elif words > MAX_COVER_WORDS:
        problems.append(f"the cover letter is {words} words - it will not fit one page")
    if page_count > 1:
        problems.append(f"the cover letter rendered {page_count} pages")
    if PLACEHOLDER.search(body or "") or PLACEHOLDER.search(pdf_text or ""):
        problems.append("the cover letter still contains a placeholder")

    # Dates and figures are checked against everything the candidate can
    # evidence, not only against the trimmed resume. A metric that is true, and
    # sits in the master resume, is legitimate in a letter even when the bullet
    # carrying it was cut for space - flagging that would force the letter to
    # argue from a subset of the candidate's own history.
    supporting = " ".join([resume_text or "", master_text or "", json_safe(profile)])

    # A year may also come from the posting - "2027 Start" is the employer's date,
    # not a claim about the candidate - so the job's own text counts for dates.
    # Figures do not get the same latitude: a metric taken from a job description
    # and written into a letter is exactly the invented claim being guarded against.
    posting = " ".join(
        str(getattr(job, field, "") or "") for field in ("title", "description")
    )
    invented = set(YEAR.findall(body or "")) - set(YEAR.findall(supporting + " " + posting))
    if invented:
        problems.append(
            "the cover letter states dates the candidate's material does not: "
            + ", ".join(sorted(invented))
        )

    invented_numbers = _numbers(body or "") - _numbers(supporting)
    if invented_numbers:
        problems.append(
            "the cover letter states figures the candidate's material does not: "
            + ", ".join(sorted(invented_numbers))[:80]
        )

    company = LEGAL_SUFFIX.sub("", (getattr(job, "company", "") or "").strip())
    if company and not company.casefold().startswith("unknown") \
            and company.casefold() not in (body or "").casefold():
        problems.append("the cover letter never names the company")
    return problems


def json_safe(profile: dict) -> str:
    import json

    return json.dumps(profile, default=str)


def vocabulary_of(values) -> set[str]:
    """Matchable words for a list of confirmed terms."""
    return _vocabulary(" ".join(str(value) for value in (values or [])))


def verified_terms(resume, profile: dict) -> set[str]:
    """Everything the candidate has actually evidenced, as matchable words.

    Built from the master resume as parsed - before any editing - plus the
    profile. This is the only vocabulary a re-worded line is allowed to draw a
    claim from.
    """
    parts = [
        "".join(run.text for run in (line.original or line.runs))
        for line in resume.lines
    ]
    # Only skills a person or a document stands behind. Including the whole
    # skills list let a term this application added from a job description
    # yesterday authorise a claim today - the machine citing itself as evidence.
    from app.services import profile_merge

    parts.extend(str(skill) for skill in profile_merge.evidenced_skills(profile))
    for section in ("experience", "projects", "education", "publications"):
        for item in profile.get(section) or []:
            if isinstance(item, dict):
                parts.extend(str(value) for value in item.values() if isinstance(value, str))
                parts.extend(str(b) for b in (item.get("bullets") or []))
    return _vocabulary(" ".join(parts))
