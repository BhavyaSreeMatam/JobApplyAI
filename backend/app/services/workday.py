"""Filling Workday's repeating sections: work experience, education, websites.

Workday's "My Experience" step is unlike a flat form. Each section starts
collapsed behind an Add button, then renders numbered blocks ("Work Experience 1",
"Education 2") each containing its own labelled fields. A generic field sweep
cannot handle it:

  - pressing Add on every pass creates a duplicate block every time
  - "Location" inside a work-experience block means where that JOB was, not
    where the applicant lives, so the generic address rule fills it wrongly
  - dates are split into separate month and year inputs

Fields are located by their visible label inside each block rather than by
data-automation-id, because the labels are stable across Workday versions and
tenant customisations while the ids are not.
"""
import re

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

PRESENT = ("present", "current", "now", "ongoing", "to date")


def parse_range(dates: str) -> tuple[tuple[str, str] | None, tuple[str, str] | None, bool]:
    """Turn "Jan 2023 - Jun 2024" into ((01, 2023), (06, 2024), still_here).

    Returns month/year pairs as strings, and whether the role is current.
    """
    text = (dates or "").strip()
    if not text:
        return None, None, False

    current = any(word in text.casefold() for word in PRESENT)
    # The separator has to be bounded. Splitting on a bare "to" cut "October" in
    # half - "Oc" then "ber 2023" - so a range beginning in October lost its start
    # date entirely and read its end date off the wrong fragment. The word forms
    # now need whitespace around them; the dashes stand alone.
    parts = re.split(
        r"\s*(?:[-–—]|(?<=\s)(?:to|through|until)(?=\s))\s*",
        text, maxsplit=1, flags=re.I,
    )

    def one(chunk: str) -> tuple[str, str] | None:
        if not chunk:
            return None
        year = re.search(r"(19|20)\d{2}", chunk)
        if not year:
            return None
        month = ""
        name = re.search(r"[a-z]{3,}", chunk, re.I)
        if name and name.group(0)[:4].casefold() in MONTHS:
            month = f"{MONTHS[name.group(0)[:4].casefold()]:02d}"
        elif name and name.group(0)[:3].casefold() in MONTHS:
            month = f"{MONTHS[name.group(0)[:3].casefold()]:02d}"
        numeric = re.match(r"\s*(\d{1,2})[/-](19|20)\d{2}", chunk)
        if numeric:
            month = f"{int(numeric.group(1)):02d}"
        # An unknown month stays unknown. Defaulting it to "01" turned "2021" into
        # January 2021 on every form that asked, which is a date the candidate
        # never gave and cannot defend - and it silently changed how long a role
        # appeared to last.
        return (month, year.group(0))

    start = one(parts[0])
    end = None if current else one(parts[1] if len(parts) > 1 else "")
    return start, end, current


# Degree abbreviations and full forms. Order matters: longer forms come first so
# "B.Tech." is not truncated to "B" and "M.Sc." not to "M.S".
DEGREE_PATTERN = re.compile(
    r"^\s*("
    r"bachelor(?:'s)?(?:\s+of\s+[a-z]+)?"
    r"|master(?:'s)?(?:\s+of\s+[a-z]+)?"
    r"|doctor(?:ate)?(?:\s+of\s+philosophy)?"
    r"|ph\.?\s?d\.?"
    r"|b\.?\s?tech\.?|m\.?\s?tech\.?"
    r"|b\.?\s?sc\.?|m\.?\s?sc\.?"
    r"|mba|b\.?\s?e\.?|m\.?\s?e\.?|b\.?\s?a\.?|m\.?\s?a\.?"
    r"|m\.?\s?s\.?|b\.?\s?s\.?"
    r"|associate(?:'s)?"
    r")",
    re.I,
)


def split_degree(heading: str) -> tuple[str, str]:
    """"M.S. Computer Science" -> ("M.S.", "Computer Science").

    Workday keeps Degree and Field of Study as separate pickers, so a single
    heading has to be divided. A comma splits it when present; otherwise the
    leading degree abbreviation is matched and the remainder is the field.
    """
    text = (heading or "").strip()
    if not text:
        return "", ""

    if "," in text:
        degree, _, field = text.partition(",")
        return degree.strip(), field.strip()

    match = DEGREE_PATTERN.match(text)
    if match:
        degree = match.group(0).strip()
        field = text[match.end():].strip(" .,-")
        # "Bachelor of Science in Computer Science"
        field = re.sub(r"^(in|of)\s+", "", field, flags=re.I)
        return degree, field
    return text, ""


# Workday's Degree control is a level taxonomy - "Bachelor's Degree", "Master's
# Degree", "Doctorate" - while a profile holds the degree the way a resume writes
# it ("Master of science", "B.Tech."). Matching one against the other directly
# fails, correctly, so the level has to be recovered first and offered as the
# fallback. Order matters: doctorates before masters, masters before bachelors,
# so "M.Phil" is not read as a bachelor's.
# Every abbreviation needs a left word boundary: without one, "ma" at the end of
# "diploma" matches the M.A. alternative and a high-school diploma is read as a
# master's degree.
DEGREE_LEVELS = [
    ("Doctorate", r"\bph\.?\s?d|doctor|\bd\.?\s?phil"),
    ("Master", r"master|\bm\.?\s?sc?\.?\b|\bm\.?\s?tech|\bm\.?\s?eng|\bm\.?\s?a\.?\b|"
               r"\bmba\b|\bm\.?\s?e\.?\b"),
    ("Bachelor", r"bachelor|\bb\.?\s?sc?\.?\b|\bb\.?\s?tech|\bb\.?\s?eng|\bb\.?\s?a\.?\b|"
                 r"\bb\.?\s?e\.?\b"),
    ("Associate", r"associate"),
    ("High School", r"high\s?school|secondary|\bdiploma\b|\bged\b"),
]


def degree_level(text: str) -> str:
    """"Master of science" -> "Master"; "B.Tech." -> "Bachelor"; "" when unclear."""
    lowered = (text or "").casefold()
    for level, pattern in DEGREE_LEVELS:
        if re.search(pattern, lowered):
            return level
    return ""


def unique_urls(values) -> list[str]:
    """Drop duplicates that differ only by scheme or a trailing slash."""
    seen, out = set(), []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = re.sub(r"^https?://(www\.)?", "", text.casefold()).rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def is_workday(page) -> bool:
    try:
        if "myworkdayjobs.com" in (page.url or "").casefold():
            return True
        return page.locator("[data-automation-id]").count() > 5
    except Exception:
        return False


def _blocks(page, heading: str):
    """Every numbered block for a section, in order ("Work Experience 1", ...)."""
    found = []
    index = 1
    while index <= 12:
        locator = page.locator(
            f'xpath=//*[normalize-space(text())="{heading} {index}"]'
            f'/ancestor::div[@data-automation-id or @role="group"][1]'
        )
        if not locator.count():
            break
        found.append(locator.first)
        index += 1
    return found


def _ensure_blocks(page, heading: str, wanted: int) -> int:
    """Create exactly as many blocks as there are profile entries - no more.

    The duplicate-everything bug came from pressing Add unconditionally on every
    pass; this only adds the shortfall.
    """
    existing = len(_blocks(page, heading))
    if existing >= wanted:
        return existing

    for _ in range(wanted - existing):
        clicked = False
        for selector in (
            f'xpath=//*[normalize-space(text())="{heading}"]/following::button[normalize-space(text())="Add"][1]',
            f'xpath=//*[normalize-space(text())="{heading}"]/following::button[contains(.,"Add Another")][1]',
        ):
            try:
                button = page.locator(selector).first
                if button.count() and button.is_visible():
                    button.click(timeout=4000)
                    page.wait_for_timeout(900)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break
    return len(_blocks(page, heading))


def _field(block, label: str):
    """The control inside this block whose visible label matches.

    `select` is in the list because some of these are native dropdowns rather
    than Workday's search widgets - Degree usually is. Without it the label
    matched nothing, the field was reported as absent, and the degree never got
    picked.
    """
    # One union step, not a tag at a time. Asking for `following::input[1]` first
    # and `following::select[1]` second does not mean "an input if there is one":
    # each is the nearest of ITS kind, so the Degree label reached past its own
    # <select> and returned the Field of Study input that came after it.
    control = "*[self::input or self::select or self::textarea]"
    for selector in (
        f'xpath=.//label[starts-with(normalize-space(text()),"{label}")]/following::{control}[1]',
        f'xpath=.//*[starts-with(normalize-space(text()),"{label}")]/following::{control}[1]',
    ):
        try:
            found = block.locator(selector).first
            if found.count():
                return found
        except Exception:
            continue
    return None


def _tag(element) -> str:
    try:
        return str(element.evaluate("(el) => el.tagName") or "").casefold()
    except Exception:
        return ""


def _select_native(field, value: str, label: str, report: list, unresolved,
                   fallback: str = "") -> bool:
    """Pick from a real <select>, which Workday uses for Degree among others."""
    try:
        options = [
            text.strip() for text in field.locator("option").all_inner_texts()
        ]
    except Exception:
        return unresolved("could not read the dropdown")
    # "Select One" and friends are placeholders, never an answer.
    choosable = [
        option for option in options
        if option and not re.fullmatch(r"select\s*(one|an?\s+\w+)?\.{0,3}|-+|choose.*", option, re.I)
    ]
    index = _first_match(choosable, value, fallback)
    if index < 0:
        return unresolved(f'no option matches "{str(value)[:32]}" - choose it yourself')
    try:
        field.select_option(label=choosable[index], timeout=4000)
    except Exception:
        return unresolved(f'could not select "{choosable[index][:32]}"')
    report.append({"field": label, "value": choosable[index][:60]})
    return True


def _type(block, label: str, value: str, report: list) -> bool:
    if not value:
        return False
    field = _field(block, label)
    if field is None:
        return False
    try:
        field.fill(str(value), timeout=4000)
        report.append({"field": label, "value": str(value)[:50]})
        return True
    except Exception:
        return False


OPTION_SELECTOR = '[role=option]:visible, [data-automation-id*="promptOption"]:visible'


def _visible_options(page) -> list[str]:
    try:
        return [
            text.strip().replace("\n", " ")
            for text in page.locator(OPTION_SELECTOR).all_inner_texts()
            if text.strip()
        ]
    except Exception:
        return []


def _settled_options(page, timeout_ms: int = 5000, poll_ms: int = 300) -> list[str]:
    """The option list once it has stopped changing.

    Workday filters these server-side, so the list visible a fixed moment after
    typing is often still the unfiltered one. Reading it then, and clicking its
    first entry, is how "Computer Science" became "Accounting" - the first item
    in the unfiltered Field of Study list. Waiting for two identical reads costs
    a few hundred milliseconds and removes the race entirely.
    """
    previous, waited = None, 0
    while waited < timeout_ms:
        page.wait_for_timeout(poll_ms)
        waited += poll_ms
        current = _visible_options(page)
        if current and current == previous:
            return current
        previous = current
    return previous or []


def _committed(block, chosen: str) -> bool:
    """Whether the chosen option actually landed in the control.

    Workday shows a selection as a removable pill and clears the search input, so
    the proof is the block's own text. Typed text that was never committed lives
    in the input's value, which `inner_text` does not return - which is exactly
    how an empty "School or University" got reported as filled.
    """
    from app.services.candidate_engine import normalize

    try:
        text = normalize(block.inner_text(timeout=3000))
    except Exception:
        return True  # cannot see it; do not claim a failure we did not observe
    target = normalize(chosen)
    return bool(target) and target[:30] in text


def _first_match(labels: list[str], *candidates: str) -> int:
    """Index of the first candidate wording any option matches, else -1."""
    from app.services.candidate_engine import best_option

    for candidate in candidates:
        if not candidate:
            continue
        index = best_option(candidate, labels)
        if index >= 0:
            return index
    return -1


def _type_ahead(page, block, label: str, value: str, report: list,
                pending: list | None = None, fallback: str = "") -> bool:
    """Workday's search-and-select controls: type, wait for the list, pick a match.

    Everything here is about not lying. The old version clicked whatever option
    happened to be first and then recorded the value it had wanted rather than
    the one it got, so a wrong answer and a silently empty field both read as
    successes in the report.
    """
    def unresolved(reason: str) -> bool:
        if pending is not None:
            pending.append({"field": label, "reason": reason[:90]})
        return False

    if not value:
        return False
    field = _field(block, label)
    if field is None:
        return unresolved("no such field in this block")
    if _tag(field) == "select":
        return _select_native(field, value, label, report, unresolved, fallback)
    try:
        field.click(timeout=4000)
        field.fill(str(value)[:60], timeout=4000)
        labels = _settled_options(page)
        if not labels:
            field.press("Escape")
            return unresolved("the option list never appeared - choose it yourself")

        index = _first_match(labels, value, fallback)
        if index < 0:
            # Leaving the typed text behind would look filled and be discarded on
            # blur, so clear it: an empty required field is at least visible.
            field.fill("", timeout=3000)
            field.press("Escape")
            return unresolved(
                f'no option matches "{str(value)[:32]}" - choose it yourself'
            )

        page.locator(OPTION_SELECTOR).nth(index).click(timeout=4000)
        page.wait_for_timeout(450)
        chosen = labels[index]
        if not _committed(block, chosen):
            return unresolved(f'"{chosen[:32]}" did not stick - choose it yourself')
        report.append({"field": label, "value": chosen[:60]})
        return True
    except Exception as error:
        try:
            field.press("Escape")
        except Exception:
            pass
        return unresolved(f"could not use the picker ({type(error).__name__})")


def _date(block, label: str, pair: tuple[str, str] | None, report: list,
          pending: list | None = None) -> bool:
    """Workday splits a date into adjacent month and year inputs.

    A missing month fills the year and says so, rather than inventing January.
    Workday will not accept a part-filled date, so this is reported as something
    the candidate has to supply - which is true, and is the only honest answer
    when the profile does not hold the month.
    """
    if not pair:
        return False
    month, year = pair
    if not month:
        try:
            inputs = block.locator(
                f'xpath=.//*[starts-with(normalize-space(text()),"{label}")]'
                f"/following::input[position()<=2]"
            )
            if inputs.count() >= 2:
                inputs.nth(1).fill(year, timeout=3000)
        except Exception:
            pass
        if pending is not None:
            pending.append({
                "field": f"{label} month",
                "reason": f"your profile has only the year ({year}) - add the month "
                          "in Profile, or type it here",
            })
        return False
    try:
        inputs = block.locator(
            f'xpath=.//*[starts-with(normalize-space(text()),"{label}")]/following::input[position()<=2]'
        )
        if inputs.count() >= 2:
            inputs.nth(0).fill(month, timeout=3000)
            inputs.nth(1).fill(year, timeout=3000)
            report.append({"field": f"{label} date", "value": f"{month}/{year}"})
            return True
        if inputs.count() == 1:
            inputs.nth(0).fill(f"{month}/{year}", timeout=3000)
            report.append({"field": f"{label} date", "value": f"{month}/{year}"})
            return True
    except Exception:
        pass
    return False


def _tick_current(block, report: list, pending: list) -> bool:
    """Tick "I currently work here".

    A statement of fact taken straight from the profile entry, not an agreement -
    the rule about never auto-accepting terms does not reach it. It matters
    beyond the tick itself: while it is clear, Workday keeps the To date required,
    and an entry with no end date can never satisfy it.

    Workday hides the real <input> behind a styled span, so a plain check() often
    fails on an element Playwright considers invisible; clicking the label is the
    fallback. The old code looked on the preceding:: axis, where the checkbox has
    never been - the label sits above it - and swallowed every failure silently.
    """
    candidates = (
        'xpath=.//input[@type="checkbox"][contains(@data-automation-id,"urrentlyWork")]',
        'xpath=.//label[contains(normalize-space(.),"currently work here")]'
        '//input[@type="checkbox"]',
        'xpath=.//*[contains(normalize-space(.),"currently work here")]'
        '/following::input[@type="checkbox"][1]',
        'xpath=.//*[contains(normalize-space(.),"currently work here")]'
        '/preceding::input[@type="checkbox"][1]',
    )
    for selector in candidates:
        try:
            box = block.locator(selector).first
            if not box.count():
                continue
            if box.is_checked():
                return True
            try:
                box.check(timeout=2500)
            except Exception:
                # The visible control is the label, not the input behind it.
                box.evaluate("(el) => el.closest('label, div')?.click() ?? el.click()")
            if box.is_checked():
                report.append({"field": "I currently work here", "value": "checked"})
                return True
        except Exception:
            continue
    pending.append({
        "field": "I currently work here",
        "reason": "could not tick it - do it yourself, or the To date stays required",
    })
    return False


def _report_shortfall(heading: str, entries: list, blocks: list, pending: list) -> None:
    """Say so when the form holds fewer entries than the candidate has.

    `zip` stops at the shorter side, so a form offering three blocks for five jobs
    filled three and said nothing about the other two. The applicant would submit
    an application missing history they believed had been entered. A form is
    entitled to its own limit - what is not acceptable is that limit being
    invisible.
    """
    missing = len(entries) - len(blocks)
    if missing <= 0:
        return
    names = [
        (entry.get("organization") or entry.get("heading") or "an entry")
        for entry in entries[len(blocks):]
    ]
    pending.append({
        "field": f"{heading} (form holds {len(blocks)})",
        "reason": (
            f"{missing} more to add by hand: " + ", ".join(str(n)[:40] for n in names[:4])
            + (" ..." if len(names) > 4 else "")
        ),
    })


def fill_work_experience(page, entries: list[dict],
                         pending: list | None = None) -> list[dict]:
    report: list[dict] = []
    pending = pending if pending is not None else []
    # No arbitrary cap. Six was a guess, and a candidate with seven jobs simply
    # lost the seventh with nothing said about it. The form's own limit is
    # discovered below, and reported when it bites.
    entries = [e for e in entries if (e.get("heading") or e.get("organization"))]
    if not entries:
        return report

    blocks = _blocks(page, "Work Experience")
    if len(blocks) < len(entries):
        _ensure_blocks(page, "Work Experience", len(entries))
        blocks = _blocks(page, "Work Experience")
    _report_shortfall("Work Experience", entries, blocks, pending)

    for entry, block in zip(entries, blocks):
        start, end, current = date_parts(entry)
        _type(block, "Job Title", entry.get("heading", ""), report)
        _type(block, "Company", entry.get("organization", ""), report)
        # This Location is where the JOB was - never the applicant's address.
        _type(block, "Location", entry.get("location", ""), report)
        _type(block, "Role Description",
              " ".join(entry.get("bullets", []) or [])[:1800], report)
        _date(block, "From", start, report, pending)
        if current:
            _tick_current(block, report, pending)
        elif end:
            _date(block, "To", end, report, pending)
        else:
            pending.append({
                "field": f"To date for {entry.get('heading', 'this role')}"[:70],
                "reason": "this entry has neither an end date nor 'current' set in your profile",
            })
    return report


def fill_education(page, entries: list[dict], pending: list | None = None) -> list[dict]:
    report: list[dict] = []
    pending = pending if pending is not None else []
    entries = [e for e in entries if (e.get("organization") or e.get("heading"))]
    if not entries:
        return report

    blocks = _blocks(page, "Education")
    if len(blocks) < len(entries):
        _ensure_blocks(page, "Education", len(entries))
        blocks = _blocks(page, "Education")
    _report_shortfall("Education", entries, blocks, pending)

    for entry, block in zip(entries, blocks):
        start, end, _current = date_parts(entry)
        _type_ahead(page, block, "School or University", entry.get("organization", ""),
                    report, pending)
        # Explicit profile fields win; splitting a heading is only a fallback for
        # older entries saved before Degree and Major were separate.
        degree = (entry.get("degree") or "").strip()
        field_of_study = (entry.get("major") or "").strip()
        if not degree and not field_of_study:
            degree, field_of_study = split_degree(entry.get("heading", ""))
        _type_ahead(page, block, "Degree", degree, report, pending,
                    fallback=degree_level(degree))
        _type_ahead(page, block, "Field of Study", field_of_study, report, pending)
        _type(block, "Overall Result", (entry.get("gpa") or "").strip(), report)
        if start:
            _type(block, "From", start[1], report)
        if end:
            _type(block, "To", end[1], report)
    return report


def fill_websites(page, profile: dict) -> list[dict]:
    report: list[dict] = []
    urls = unique_urls(
        profile.get(key) for key in ("website", "github", "publications_url")
    )[:3]
    if not urls:
        return report

    blocks = _blocks(page, "Websites")
    if len(blocks) < len(urls):
        _ensure_blocks(page, "Websites", len(urls))
        blocks = _blocks(page, "Websites")
    for url, block in zip(urls, blocks):
        _type(block, "URL", url, report)
    return report


def fill_skills(page, skills: list[str], limit: int = 0,
                pending: list | None = None) -> list[dict]:
    """Add skills to Workday's multi-select, recording only the ones that stuck.

    Workday's skills picker accepts only terms from its own taxonomy, so a skill
    the profile lists may simply not exist there. That is a fine outcome - what
    is not fine is the old behaviour of clicking whatever option was showing and
    recording the profile's wording, which left the box empty while the report
    claimed ten skills had been added.

    `limit` is 0 by default, meaning every skill offered is attempted. The old
    default of 10 silently discarded the rest of a candidate's skills, ordered by
    however the profile happened to be stored, and never mentioned it. A form with
    a real limit of its own stops accepting selections and that is reported; a cap
    this code invented is not a limit, it is data loss.

    Three outcomes are reported separately, because they mean different things:
    selections the page confirmed, terms Workday's taxonomy does not contain, and
    terms not reached because the form stopped accepting them.
    """
    from app.services.candidate_engine import best_option

    report: list[dict] = []
    pending = pending if pending is not None else []
    wanted, seen = [], set()
    for skill in skills or []:
        text = str(skill).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            wanted.append(text)
    if limit:
        wanted = wanted[:limit]
    if not wanted:
        return report

    box = None
    for selector in (
        'xpath=//*[contains(normalize-space(text()),"Type to Add Skills")]/following::input[1]',
        'input[placeholder*="skill" i]',
        'input[aria-label*="skill" i]',
    ):
        try:
            candidate = page.locator(selector).first
            if candidate.count() and candidate.is_visible():
                box = candidate
                break
        except Exception:
            continue
    if box is None:
        pending.append({"field": "skills", "reason": "no skills box on this page"})
        return report

    # Already on the form before this ran - a second pass must not add them twice.
    existing = _selected_skills(page)
    unmatched, unattempted, added = [], [], set(existing)

    for position, skill in enumerate(wanted):
        if skill.casefold() in added:
            continue
        try:
            box.click(timeout=3000)
            box.fill(skill, timeout=3000)
            labels = _settled_options(page, timeout_ms=3500)
            index = best_option(skill, labels) if labels else -1
            if index < 0:
                box.fill("", timeout=2500)
                box.press("Escape")
                unmatched.append(skill)
                continue
            page.locator(OPTION_SELECTOR).nth(index).click(timeout=3000)
            page.wait_for_timeout(350)
            chosen = labels[index][:60]
            if chosen.casefold() in added:
                continue
            added.add(chosen.casefold())
            report.append({"field": "skill", "value": chosen})
        except Exception:
            # The form has stopped taking selections - usually its own cap. What
            # was not reached is reported as not reached, rather than folded in
            # with terms the taxonomy genuinely lacks.
            unattempted.extend(wanted[position:])
            break

    if unmatched:
        pending.append({
            "field": "skills not in Workday's list",
            "reason": ", ".join(unmatched[:10]) + (" ..." if len(unmatched) > 10 else ""),
        })
    if unattempted:
        pending.append({
            "field": f"skills not attempted ({len(unattempted)})",
            "reason": "the form stopped accepting selections: "
                      + ", ".join(unattempted[:6]),
        })
    return report


def _selected_skills(page) -> set[str]:
    """Skills already showing as chosen, so a rerun does not duplicate them."""
    chosen = set()
    for selector in ('[data-automation-id*="selectedItem"]', "[role=listitem]",
                     '[data-automation-id="pill"]'):
        try:
            for text in page.locator(selector).all_inner_texts():
                label = " ".join(text.split()).strip("×x ").casefold()
                if label and len(label) < 60:
                    chosen.add(label)
        except Exception:
            continue
    return chosen


def fill_all(page, profile: dict) -> tuple[list[dict], list[dict]]:
    """Fill every repeating section Workday shows on My Experience.

    Returns (filled, pending). Anything that could not be committed lands in
    `pending` with a reason, so the report never claims a field is done when the
    page still shows it empty.
    """
    from app.services.profile_merge import evidenced_skills

    report: list[dict] = []
    pending: list[dict] = []
    report += fill_work_experience(page, profile.get("experience") or [], pending)
    report += fill_education(page, profile.get("education") or [], pending)
    report += fill_websites(page, profile)
    report += fill_skills(page, evidenced_skills(profile), pending=pending)
    return report, pending


MONTH_NAMES = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def format_dates(entry: dict) -> str:
    """A single display line from the separate month/year parts.

    Falls back to whatever `dates` string an older entry already holds.
    """
    def side(month: str, year: str) -> str:
        month, year = (month or "").strip(), (year or "").strip()
        if not year:
            return ""
        name = MONTH_NAMES.get(month.zfill(2)) if month else ""
        return f"{name} {year}".strip()

    start = side(entry.get("start_month", ""), entry.get("start_year", ""))
    end = "Present" if entry.get("current") else side(
        entry.get("end_month", ""), entry.get("end_year", "")
    )
    if start and end:
        return f"{start} \u2013 {end}"
    if start or end:
        return start or end
    return (entry.get("dates") or "").strip()


def date_parts(entry: dict) -> tuple[tuple[str, str] | None, tuple[str, str] | None, bool]:
    """(start, end, is_current) from the stored parts, or by parsing `dates`.

    A month the candidate never supplied comes back empty rather than as "01".
    Substituting January produced a precise-looking date nobody had stated, which
    then went onto forms as fact and shifted every duration calculated from it.
    """
    def side(month, year):
        if not str(year or "").strip():
            return None
        return (str(month or "").strip().zfill(2) if str(month or "").strip() else "",
                str(year).strip())

    if entry.get("start_year"):
        start = side(entry.get("start_month"), entry.get("start_year"))
        end = None
        if not entry.get("current"):
            end = side(entry.get("end_month"), entry.get("end_year"))
        return start, end, bool(entry.get("current"))
    return parse_range(entry.get("dates", ""))
