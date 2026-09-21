"""Conservative, local, deterministic answering; no credentials or paid model needed.

Explicit aliases are reusable. Fuzzy matches are suggestions only. Consent,
legal, demographic and AI-policy questions are never learned automatically.
"""
import re
from datetime import date
from difflib import SequenceMatcher


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


# A normalised term this short carries no signal beyond its exact characters.
EXACT_ONLY_LENGTH = 2

# Not answers. A dropdown's first row is usually an instruction, and picking it
# leaves the field looking answered while holding nothing.
PLACEHOLDER_OPTION = re.compile(
    r"^\s*(-+|_+|\.{2,}|"
    r"(please\s+)?(select|choose|pick)(\s+(an?|one|your|a\s+value|option|from))?[.:]?|"
    r"select\s*\.{0,3}|none|n/?a|not\s+(applicable|specified|selected)|"
    r"no\s+(selection|answer)|--.*--|\(.*\)|choose\.{0,3})\s*$",
    re.I,
)


def selectable(options) -> list[tuple[int, str]]:
    """(original index, label) for options that are real answers.

    Blank rows, instruction rows and disabled rows are removed before matching
    rather than after, so they cannot win a fuzzy comparison against a short
    term - an empty option normalises to "" and used to look like a plausible
    match for anything.
    """
    live = []
    for index, option in enumerate(options or []):
        label = " ".join(str(option or "").split())
        if not label or PLACEHOLDER_OPTION.match(label):
            continue
        live.append((index, label))
    return live


def best_option(desired: str, options: list[str]) -> int:
    """Index of the option best matching `desired`, or -1 when not confident.

    -1 is a real answer and callers must honour it. Picking the closest thing to
    hand is worse than picking nothing: a form that silently holds the wrong
    answer looks filled, and the applicant has no reason to look at it again.

    Lives here rather than in the autofiller because Workday's repeating-section
    code needs it too, and that module cannot import the autofiller back.
    """
    target = normalize(desired)
    if not target or not options:
        return -1

    # Blanks, "Select one" and the like are removed first. Matching against them
    # let an empty option - which normalises to "" - score as a plausible answer.
    live = selectable(options)
    if not live:
        return -1
    positions = [index for index, _ in live]
    labels = [label for _, label in live]

    def original(index: int) -> int:
        return positions[index]

    # Raw comparison first, because normalising strips the punctuation that is
    # the whole identity of some terms: "C++", "C#" and "C" all reduce to "c",
    # so a skills list answered "C++" with "C#". Below EXACT_ONLY_LENGTH nothing
    # but an exact raw match is trustworthy.
    raw_target = " ".join(str(desired).casefold().split())
    raw = [" ".join(str(label).casefold().split()) for label in labels]
    for index, option in enumerate(raw):
        if option == raw_target:
            return original(index)
    options = labels
    normalized = [normalize(option) for option in labels]

    # Aliases are checked before the short-term bail below: "JS", "ML" and "K8s"
    # are all too short to fuzzy-match safely but are exactly the abbreviations a
    # curated table exists to resolve.
    for index, option in enumerate(normalized):
        if option and (option in TECH_ALIASES.get(target, ())
                       or target in TECH_ALIASES.get(option, ())):
            return original(index)

    if len(target) <= EXACT_ONLY_LENGTH:
        return -1

    for index, option in enumerate(normalized):
        if option == target:
            return original(index)

    # Substring matching used to run here, and it is why "Java" selected
    # "JavaScript": "java" is literally inside "javascript". The same rule maps
    # "C" to "C++", "R" to "Ruby", "Go" to "Golang Developer" and "SQL" to
    # "NoSQL". These are different technologies, and claiming one because the
    # candidate has the other is a false statement on an application.
    #
    # Prefix and substring are still useful where one side is plainly a longer
    # form of the same thing, so they are kept - but only when the extra text is
    # a qualifier rather than a different word, and never for a short term where
    # the overlap is most of the string.
    if len(target) > MIN_FUZZY_LENGTH:
        hits = [
            index for index, option in enumerate(normalized)
            if option and _is_longer_form(target, option)
        ]
        if hits:
            return original(min(hits, key=lambda index: (len(normalized[index]), index)))

        scored = sorted(
            ((SequenceMatcher(None, target, option).ratio(), index)
             for index, option in enumerate(normalized) if option),
            reverse=True,
        )
        if scored and scored[0][0] >= 0.85 and (
            len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
        ):
            return original(scored[0][1])
    return -1


# At or below this length, only an exact or aliased match counts. "Java" is
# four characters and is a prefix of a different language.
MIN_FUZZY_LENGTH = 5

# Defensible equivalences. Each pair is the same technology under two names, not
# two technologies that share letters. Named apart from the field-label ALIASES
# further down - sharing that name silently replaced this table with a list of
# form labels and turned every equivalence below into dead code.
TECH_ALIASES = {
    "postgres": ("postgresql",), "postgresql": ("postgres",),
    "js": ("javascript",), "javascript": ("js",),
    "ts": ("typescript",), "typescript": ("ts",),
    "py": ("python",), "python": ("py", "python 3"),
    "k8s": ("kubernetes",), "kubernetes": ("k8s",),
    "gcp": ("google cloud platform", "google cloud"),
    "google cloud platform": ("gcp",), "google cloud": ("gcp",),
    "aws": ("amazon web services",), "amazon web services": ("aws",),
    "ml": ("machine learning",), "machine learning": ("ml",),
    "nlp": ("natural language processing",),
    "natural language processing": ("nlp",),
    "ci cd": ("ci/cd", "continuous integration"),
    "rag": ("retrieval augmented generation",),
    "retrieval augmented generation": ("rag",),
    "llm": ("large language models", "large language model"),
    "large language models": ("llm", "llms"),
}


def _is_longer_form(target: str, option: str) -> bool:
    """Whether `option` is the same thing as `target` said at greater length.

    Word-wise, not character-wise. "Computer Science" is a longer form of
    "Computer Science and Engineering"; "Java" is not a shorter form of
    "JavaScript", because "javascript" is one word and the overlap is a fragment
    of it rather than a whole word.
    """
    target_words = target.split()
    option_words = option.split()
    if not target_words or not option_words:
        return False
    shorter, longer = sorted((target_words, option_words), key=len)
    return longer[:len(shorter)] == shorter


ALIASES = {
    "first_name": ["First Name", "Given name"],
    "last_name": ["Last Name", "Family name", "Surname"],
    "email": ["Email", "Email address"],
    "phone": ["Phone", "Phone number", "Telephone"],
    "website": ["Website", "Portfolio URL", "Personal website"],
    "linkedin": ["LinkedIn", "LinkedIn Profile", "LinkedIn URL", "LinkedIn profile URL"],
    "publications_url": ["Publications (e.g. Google Scholar) URL", "Google Scholar URL"],
    "earliest_start": ["When is the earliest you would want to start working with us?", "Earliest start date"],
    "relocation": ["Are you open to relocation for this role?", "Are you willing to relocate?"],
    "office_25_percent": ["Are you open to working in-person in one of our offices 25% of the time?"],
    "authorized_to_work": ["Are you legally authorized to work in the United States?", "Are you authorized to work in the United States?"],
    "requires_sponsorship_now": ["Do you require visa sponsorship?"],
    "requires_sponsorship_future": ["Will you now or will you in the future require employment visa sponsorship to work in the country in which the job you're applying for is located?", "Will you now or in the future require sponsorship for employment visa status?"],
}
LOOKUP = {normalize(label): key for key, labels in ALIASES.items() for label in labels}
SENSITIVE = ("arbitrat", "agree", "consent", "ai policy", "ai usage", "certify", "attest", "disabil", "veteran", "gender", "ethnic", "race", "criminal", "background check")
AUTH_KEYS = {"authorized_to_work", "requires_sponsorship_now", "requires_sponsorship_future"}
# Enough to resolve the common cases without shipping a geo database. Used only
# to pick the phone-number country control; never for anything immigration-related.
DIAL_CODES = {
    "+44": "United Kingdom", "+91": "India", "+61": "Australia", "+49": "Germany",
    "+33": "France", "+81": "Japan", "+86": "China", "+82": "South Korea",
    "+65": "Singapore", "+31": "Netherlands", "+34": "Spain", "+39": "Italy",
    "+46": "Sweden", "+41": "Switzerland", "+353": "Ireland", "+64": "New Zealand",
    "+27": "South Africa", "+55": "Brazil", "+52": "Mexico", "+972": "Israel",
}

COUNTRY_HINTS = (
    ("United States", r"\bunited states\b|\busa?\b|\bu\.s\.?a?\b|new york|san francisco|"
                      r"seattle|boston|austin|chicago|los angeles|\bny\b|\bca\b|\bwa\b|\bma\b|\btx\b"),
    ("United Kingdom", r"\bunited kingdom\b|\buk\b|\blondon\b|\bengland\b|\bscotland\b"),
    ("India", r"\bindia\b|bangalore|bengaluru|hyderabad|mumbai|delhi|chennai|pune"),
    ("Canada", r"\bcanada\b|toronto|vancouver|montreal|ottawa"),
    ("Australia", r"\baustralia\b|sydney|melbourne"),
    ("Germany", r"\bgermany\b|berlin|munich"),
    ("Ireland", r"\bireland\b|dublin"),
    ("Singapore", r"\bsingapore\b"),
)


SUPPORTED = {"input_text", "textarea", "multi_value_single_select", "multi_value_multi_select", "input_file"}


def sensitive(label):
    return any(word in normalize(label) for word in SENSITIVE)


def present(value):
    return value is not None and value != "" and value != [] and (not isinstance(value, str) or bool(value.strip()))


def question_fields(data):
    for question in data.get("questions", []) + data.get("location_questions", []):
        for field in question["fields"]:
            yield question, field


def value_for_field(value, field):
    if not present(value):
        return None
    if field["type"] in {"multi_value_single_select", "multi_value_multi_select"}:
        values = value if isinstance(value, list) else [value]
        mapped = []
        for item in values:
            label = "yes" if item is True else "no" if item is False else normalize(item)
            option = next((x for x in field.get("values", []) if normalize(x["label"]) == label), None)
            if option is None:
                return None
            mapped.append(option["value"])
        return mapped if field["type"] == "multi_value_multi_select" else mapped[0]
    return str(value) if not isinstance(value, (dict, list)) else None


def country_of(location):
    """Best-effort country for a job location string, or "" when unclear."""
    text = normalize(location)
    if not text:
        return ""
    for country, pattern in COUNTRY_HINTS:
        if re.search(pattern, text):
            return country
    return ""


def authorization_fresh(profile, location, today=None):
    today = today or date.today()
    country = normalize(profile.get("work_country", ""))
    text = normalize(location)
    # The country must be established, either stated outright in the location or
    # recognised from a well-known city/state in it. A bare "Remote" is never
    # enough - which country the role is in decides which authorisation applies.
    if not country:
        return False
    if country not in text and normalize(country_of(location)) != country:
        return False

    permanent = not profile.get("authorization_end") and (
        profile.get("requires_sponsorship_future") is False
    )
    try:
        if not permanent:
            # A time-limited status can lapse, so it must have been confirmed
            # recently. Permanent unrestricted authorisation cannot lapse, so
            # re-confirming it monthly is pointless friction.
            reviewed = date.fromisoformat(profile.get("authorization_reviewed_on") or "")
            if not 0 <= (today - reviewed).days <= 30:
                return False
        start = profile.get("authorization_start")
        end = profile.get("authorization_end")
        if start and date.fromisoformat(start) > today:
            return False
        if end and date.fromisoformat(end) < today:
            return False
    except ValueError:
        return False
    return True




