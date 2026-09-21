"""Fill an employer's application form in the visible browser, then stop.

Hard guarantee: this module never clicks a submit control. The applicant always
sees the completed form and submits it themselves.

Two further categories are treated differently from ordinary fields:
  - Binding agreements (arbitration, AI-usage policy, terms) are left blank by
    default. They can be auto-accepted, but only when the user turns that on
    explicitly in Settings.
  - Factual disclosures about the applicant (criminal history, background-check
    answers) are NEVER auto-answered and no setting changes that. Those are
    statements of fact, and a wrong one is a misrepresentation.

Everything it does fill comes verbatim from the candidate profile. Nothing is
inferred - self-identification answers are copied exactly as the user selected
them, and work-authorisation answers are only reused while they are still current
under the answer engine's freshness rules.

Modern application forms (Greenhouse included) have no native <select>: choices
are React comboboxes, `<input role="combobox">` backed by a popup listbox. Those
need click-then-pick, not fill(), which is why a plain fill() silently no-ops.

Everything here runs on the browser thread using Playwright's sync API - see
browser_session for why.
"""
import re
from difflib import SequenceMatcher

from app.db.database import BACKEND_DIR
from app.services import browser_actions, browser_session, workday
from app.services.candidate_engine import (
    AUTH_KEYS,
    COUNTRY_HINTS,
    DIAL_CODES,
    LOOKUP,
    authorization_fresh,
    best_option,
    normalize,
)

# Field identity -> profile key. Matched against name/id/label/placeholder.
FIELD_MAP = [
    # The short forms need an explicit left boundary: unanchored, "lname" matches
    # inside "fullname" and hands a single Name box the surname alone.
    ("first_name", r"first[\s_-]*name|given[\s_-]*name|preferred[\s_-]*name|(?<![a-z0-9])fname"),
    ("last_name", r"last[\s_-]*name|family[\s_-]*name|surname|(?<![a-z0-9])lname"),
    ("email", r"\bemail\b|e-mail"),
    # Before the phone rule: "Phone Device Type" contains "phone", so the looser
    # rule below would type the number into what is actually a Mobile/Home/Work
    # picker. Workday asks for this on every application.
    ("phone_device_type", r"phone[\s_-]*device[\s_-]*type|phone[\s_-]*type|device[\s_-]*type"),
    ("phone", r"\bphone\b|telephone|mobile|contact[\s_-]*number"),
    ("github", r"\bgithub\b|\bgit hub\b"),
    ("linkedin", r"linked[\s_-]*in"),
    ("publications_url", r"scholar|publications?|orcid"),
    ("website", r"website|portfolio|personal[\s_-]*site|\bblog\b"),
    # Address parts, most specific first. Order is load-bearing: whichever rule
    # matches first wins, so every named part has to be tested before the loose
    # "address" rule, which would otherwise claim them all.
    #
    # The autocomplete tokens are in here deliberately. They are a W3C-standard
    # vocabulary and the most reliable label a form ever carries - but reading
    # them as free text is what broke this: `address-level2` is the City token,
    # and the hyphen in it is a word boundary, so a bare \baddress\b matched it
    # and typed the street address into City. Same for `address-level1`, State.
    ("address_line2", r"address[\s_-]*(line)?[\s_-]*2\b|addressline2|\bapt\b|\bsuite\b|\bunit\b"),
    ("postal_code", r"postal[\s_-]*code|postalcode|\bzip\b|zipcode|postcode"),
    ("city", r"\bcity\b|\btown\b|address[\s_-]*level[\s_-]*2"),
    ("state", r"\bstate\b|province|countryregion|\bregion\b|address[\s_-]*level[\s_-]*1"),
    ("country", r"\bcountry\b"),
    ("address_line1", r"address[\s_-]*(line)?[\s_-]*1\b|addressline1|street[\s_-]*address|"
                      r"street[\s_-]*name|\bstreet\b|"
                      r"\baddress\b(?![\s_-]*(line[\s_-]*2|level|type|city|town|state|"
                      r"region|country|zip|postal|post))"),
    ("location", r"\b(current[\s_-]*)?location\b|where.*based"),
]

# A single box asking for the whole name. `^name$` never fired in practice: the
# description is several attributes joined together, so a field labelled "Name"
# arrives as something like "name name-input name" and an anchored pattern cannot
# match it. A bare word-boundary "name" is what actually occurs on real forms.
FULL_NAME_PATTERN = (
    r"full[\s_-]*name|legal[\s_-]*name|candidate[\s_-]*name|applicant[\s_-]*name|"
    r"your[\s_-]*name|\bname\b"
)

# ...but "name" alone also appears in fields asking for somebody else's name, and
# in fields asking for one part of the applicant's. Either would be wrong to fill
# with the full name, so a description matching any of these is left to the user.
# First/last are listed because FIELD_MAP only breaks out of its loop when the
# matching profile value is non-empty; without them an empty last_name would fall
# through and drop the full name into the "Last name" box.
NOT_A_FULL_NAME = (
    r"first|last|given|family|surname|middle|initial|maiden|nick|user|"
    r"compan|employer|organi|school|universit|college|institut|"
    r"referen|supervisor|manager|recruiter|emergency|"
    r"file|display|account|product|project|domain|brand|screen"
)

# Workday concatenates section and field, e.g. "legalNameSection_firstName".
WORKDAY_FIELDS = [
    ("first_name", r"legalnamesection_firstname|\bfirstname\b|givenname"),
    ("last_name", r"legalnamesection_lastname|\blastname\b|familyname"),
    ("email", r"\bemail\b"),
    # "phone-device" used to be listed as a phone-number alias. It is not - it is
    # the device-type picker's id prefix, so the number was being typed into a
    # dropdown that only accepts Mobile / Home / Work.
    ("phone_device_type", r"phone-?device-?type|phonedevicetype"),
    ("phone", r"phone-?number|phonenumber"),
    ("city", r"addresssection_city"),
    ("address_line1", r"addresssection_addressline1"),
    ("address_line2", r"addresssection_addressline2"),
    ("postal_code", r"addresssection_postalcode"),
    ("state", r"addresssection_countryregion|addresssection_region"),
]

# Binding agreements and acknowledgments. Left blank by default; the user can
# opt into auto-accepting these in Settings.
AGREEMENT_PATTERN = (
    r"arbitrat|agree(ment)?\b|consent|certify|attest|acknowledg|\bterms\b|"
    r"\bpolicy\b|ai usage|non[\s_-]*compete"
)

# Factual disclosures about the applicant's own history. NEVER auto-answered, and
# no setting turns this off: these are statements of fact rather than terms to
# accept, so a wrong answer is a misrepresentation, not a term you agreed to.
FACTUAL_BLOCK = r"background[\s_-]*check|felony|convict|criminal|\bexpunge"

LEGAL_BLOCK = AGREEMENT_PATTERN + r"|" + FACTUAL_BLOCK

# Preferred affirmative wordings, most explicit first.
AFFIRMATIVE_OPTIONS = (
    "Yes", "I agree", "I acknowledge", "I accept", "I have read and agree",
    "I have read", "Agree", "Accept", "Acknowledged", "Confirmed",
)

# Never guessed regardless of question type.
SENSITIVE_DATA = (
    r"salary|compensation|desired[\s_-]*pay|password|\bssn\b|social[\s_-]*security|"
    r"date[\s_-]*of[\s_-]*birth|\bdob\b"
)

# Choice controls whose answer is a plain profile value, not a Yes/No and not a
# self-identification. Checked before both, because the wording of these overlaps
# with looser rules further down.
PROFILE_CHOICE_MAP = [
    ("phone_device_type", r"phone[\s_-]*device[\s_-]*type|phone[\s_-]*type|device[\s_-]*type"),
]

# Voluntary self-identification: profile key -> question pattern.
SELF_ID_MAP = [
    ("hispanic_latino", r"hispanic|latino|latinx"),
    ("race_ethnicity", r"\brace\b|ethnicit"),
    ("veteran_status", r"veteran|protected[\s_-]*vet"),
    ("disability_status", r"disab"),
    ("gender", r"\bgender\b|\bsex\b"),
]

# Yes/No questions answered from a boolean profile field. Order matters: the
# more specific sponsorship wording must be tested before the general one.
BOOLEAN_MAP = [
    ("relocation", r"open to relocation|willing to relocate|relocat"),
    ("office_25_percent", r"in-?person.*offices?.*25|25%.*office"),
    ("authorized_to_work", r"legally authoriz|authorized to work"),
    ("requires_sponsorship_future", r"now or.*future.*sponsor|future.*(visa )?sponsor"),
    ("requires_sponsorship_now", r"require.*(visa )?sponsor"),
]

# Matches the phone-number country picker by element id/name only.
COUNTRY_CONTROL = re.compile(r"(^|[_-])(country|country_code|phone_country)([_-]|$)", re.I)

# Site furniture that is not part of any application form. Matched on the input's
# name/id: "q" is a job-search box, not a question.
SITE_CHROME_NAMES = {
    "q", "s", "l", "query", "search", "searchterm", "keywords", "keyword",
    "where", "location-search", "newsletter", "subscribe", "promo", "coupon",
}
SITE_CHROME_PATTERN = (
    r"search|newsletter|subscribe|sign[\s_-]*up for|promo|coupon|"
    r"filter|sort[\s_-]*by|what are you looking for"
)

# A real employer question is a sentence, not a one-word placeholder.
MIN_QUESTION_LENGTH = 12


def looks_like_a_question(text: str) -> bool:
    """Reject labels too thin to answer, so nothing meaningless reaches the model."""
    cleaned = (text or "").strip()
    if len(cleaned) >= MIN_QUESTION_LENGTH:
        return True
    # Short is fine when it is unambiguously a prompt.
    return cleaned.endswith("?") and len(cleaned.split()) >= 2


def open_application(page, report: list | None = None) -> str:
    """Press the control that starts an application. Returns what was pressed.

    This used to match button names against a list of substrings, one of which
    was the bare word "Apply" and another of which was "Submit application form".
    Playwright's accessible-name matching is a substring match, so "Apply" found
    "Submit application" - the list was capable of submitting an application in
    order to open one.

    Every candidate is now read in full and classified. Nothing that could send an
    application is pressed here or anywhere else.
    """
    try:
        candidates = page.locator(
            'button, a[role="button"], input[type="button"], input[type="submit"], a'
        ).all()[:120]
    except Exception:
        return ""
    for element in candidates:
        try:
            if not element.is_visible():
                continue
            label = " ".join((element.inner_text() or "").split()) or (
                element.get_attribute("value") or element.get_attribute("aria-label") or ""
            )
            if not label or len(label) > 60:
                continue
            if not browser_actions.may_click(label, browser_actions.FOR_OPENING):
                if browser_actions.is_submit(label) and report is not None:
                    report.append({"field": "apply control",
                                   "reason": browser_actions.refusal(label)})
                continue
            element.click(timeout=4000)
            page.wait_for_timeout(2000)
            return label
        except Exception:
            continue
    return ""


# Where a form asks for one part but the profile only holds the whole.
#
# `address_line1` used to fall back to `location`, which put "South Richmond
# Hill, NY" into a street-address box. That is not a street address, it is a city
# and a state, and an employer reading it sees an application that cannot be
# posted to. A street address that was never entered stays empty and is reported;
# a city can honestly be read off a "City, State" location.
VALUE_FALLBACKS = {"city": "location"}

# Fields added after a profile was last saved, where leaving the answer blank
# would stall a required control and one answer is overwhelmingly the right one.
# It is also the value Profile itself shows as the default, so nothing here says
# anything the candidate has not already seen and can change.
PROFILE_DEFAULTS = {"phone_device_type": "Mobile"}


def _profile_value(profile: dict, key: str) -> str:
    value = profile.get(key)
    if not value and key in VALUE_FALLBACKS:
        value = _derive(profile, key, VALUE_FALLBACKS[key])
    if not value and key in PROFILE_DEFAULTS:
        value = PROFILE_DEFAULTS[key]
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value or "").strip()


def _derive(profile: dict, key: str, source_key: str) -> str:
    """One part of an address read out of the combined location string.

    Only the part that is genuinely in there. "South Richmond Hill, NY" holds a
    city and a state; it holds no street address, and handing it to a street-
    address field produced an application an employer could not post to.
    """
    source = str(profile.get(source_key) or "").strip()
    if not source:
        return ""
    if key == "city":
        return source.split(",")[0].strip()
    return ""


def _blocked(description: str) -> str:
    if re.search(FACTUAL_BLOCK, description):
        return "a factual disclosure about you - never auto-answered"
    if re.search(AGREEMENT_PATTERN, description):
        return "legal agreement - only you can accept this"
    if re.search(SENSITIVE_DATA, description):
        return "sensitive - never auto-filled"
    return ""


def is_agreement(description: str) -> bool:
    """A term to accept, as opposed to a factual disclosure about the applicant."""
    return bool(
        re.search(AGREEMENT_PATTERN, description)
        and not re.search(FACTUAL_BLOCK, description)
        and not re.search(SENSITIVE_DATA, description)
    )


# --------------------------------------------------------------------------- #
# Value resolution - pure, unit tested without a browser
# --------------------------------------------------------------------------- #

def resolve_value(profile: dict, description: str, answers: dict, element_name: str | None) -> str:
    """What to type into a free-text field, or "" to leave it for the user."""
    if _blocked(description):
        return ""

    for key, pattern in FIELD_MAP + WORKDAY_FIELDS:
        if re.search(pattern, description):
            direct = _profile_value(profile, key)
            if direct:
                return direct
            break

    if re.search(FULL_NAME_PATTERN, description) and not re.search(
        NOT_A_FULL_NAME, description
    ):
        full = " ".join(
            x for x in (_profile_value(profile, "first_name"),
                        _profile_value(profile, "last_name")) if x
        )
        if full:
            return full

    # The answer engine's confirmed aliases, e.g. "When is the earliest you would
    # want to start working with us?" -> earliest_start. Authorisation keys are
    # excluded here; they go through resolve_choice's freshness check instead.
    normalized = normalize(description)
    best_key, best_length = "", 0
    for phrase, key in LOOKUP.items():
        if key in AUTH_KEYS or not phrase:
            continue
        if phrase in normalized and len(phrase) > best_length:
            best_key, best_length = key, len(phrase)
    if best_key:
        aliased = _profile_value(profile, best_key)
        if aliased:
            return aliased

    if element_name and isinstance(answers.get(element_name), str):
        return answers[element_name]

    return ""


def resolve_choice(profile: dict, description: str, job_location: str = "",
                   answers: dict | None = None, element_name: str | None = None,
                   accept_agreements: bool = False) -> tuple[str, str]:
    """What option to pick in a choice control.

    Returns (desired_option_text, skip_reason); exactly one is non-empty.
    """
    if re.search(FACTUAL_BLOCK, description) or re.search(SENSITIVE_DATA, description):
        return "", _blocked(description)

    if is_agreement(description):
        if accept_agreements:
            # Enabled explicitly in Settings. The option actually chosen is
            # reported back, so what was accepted stays visible in the report.
            return AFFIRMATIVE_OPTIONS[0], ""
        return "", "legal agreement - only you can accept this"

    for key, pattern in PROFILE_CHOICE_MAP:
        if re.search(pattern, description):
            stored = _profile_value(profile, key)
            if stored:
                return stored, ""
            return "", f"set {key.replace('_', ' ')} in your profile"

    for key, pattern in SELF_ID_MAP:
        if re.search(pattern, description):
            stored = _profile_value(profile, key)
            if stored:
                return stored, ""
            return "", "self-identification not set in your profile"

    for key, pattern in BOOLEAN_MAP:
        if re.search(pattern, description):
            if key in AUTH_KEYS and not authorization_fresh(profile, job_location):
                return "", ("work authorisation needs confirming for this location - set "
                            "work country and today's review date in Profile")
            stored = _profile_value(profile, key)
            if stored:
                return stored, ""
            return "", "no answer saved in your profile"

    if answers and element_name and isinstance(answers.get(element_name), str):
        return answers[element_name], ""

    return "", "no saved answer"


def resolve_phone_country(profile: dict) -> str:
    """Which country the phone-number selector should show.

    Purely a formatting concern - it decides which dial code sits beside the
    number, and is never used for work-authorisation logic.
    """
    stated = str(profile.get("work_country") or "").strip()
    if stated:
        return stated

    haystack = " ".join(
        str(profile.get(key) or "") for key in ("location", "phone")
    ).casefold()
    for country, pattern in COUNTRY_HINTS:
        if re.search(pattern, haystack):
            return country

    match = re.match(r"\s*(\+\d{1,3})", str(profile.get("phone") or ""))
    if match:
        code = match.group(1)
        if code in DIAL_CODES:
            return DIAL_CODES[code]
        if code == "+1":
            return "United States"
    return ""


def strip_dial_suffix(label: str) -> str:
    """"United States+1" -> "United States" so country names compare cleanly."""
    return re.sub(r"\s*\+\d{1,4}\s*$", "", (label or "").strip())


def split_dial_code(phone: str, dial_code: str) -> str:
    """Drop the country calling code when the form has its own country selector.

    "+1 9298421865" with a "+1" country control becomes "9298421865"; without
    this the submitted number is "+1 +1 929...".
    """
    value = (phone or "").strip()
    if not value:
        return ""
    code = re.sub(r"[^\d]", "", dial_code or "")
    digits_only = re.sub(r"[^\d+]", "", value)
    if code and digits_only.startswith("+" + code):
        remainder = digits_only[len(code) + 1:]
        # Only if something recognisable as a phone number is left. "+1" against
        # a seven-digit number whose own first digit is 1 would otherwise remove
        # a digit of the number itself.
        return remainder if len(remainder) >= MIN_SUBSCRIBER_DIGITS else value

    if value.startswith("+"):
        # A country control exists but its code could not be read. Strip a
        # leading +NN only when the rest still looks like a whole number;
        # guessing wrong here mangles the number rather than tidying it.
        stripped = re.sub(r"^\+\d{1,3}[\s.\-()]*", "", value)
        if len(re.sub(r"[^\d]", "", stripped)) >= MIN_SUBSCRIBER_DIGITS:
            return stripped.strip()
    return value.strip()


# Shorter than this and what remains is not a phone number, so whatever was about
# to be removed was part of the number rather than a country code.
MIN_SUBSCRIBER_DIGITS = 7


# --------------------------------------------------------------------------- #
# Browser interaction
# --------------------------------------------------------------------------- #

def _describe(page, element) -> str:
    """Everything that hints at what a field is asking for."""
    parts = []
    # data-automation-id is how Workday names every field; without it a Workday
    # form looks completely unlabelled and every field gets skipped.
    for attribute in ("name", "id", "placeholder", "aria-label", "data-qa",
                      "data-automation-id", "data-uxi-element-id", "autocomplete"):
        try:
            value = element.get_attribute(attribute)
        except Exception:
            value = None
        if value:
            parts.append(value)
    try:
        element_id = element.get_attribute("id")
        if element_id:
            label = page.locator(f'label[for="{element_id}"]')
            if label.count() == 1:
                parts.append(label.inner_text())
    except Exception:
        pass
    return " ".join(parts).casefold()


REPEATING_HEADINGS = ("work experience", "education", "languages", "websites")


# Workday names every field in a numbered repeating block after the section and
# that block's own number: "workExperience-37--location", "education-108--school".
# That prefix is a far more reliable signal than walking up the DOM hunting for a
# heading, which is what missed these before.
REPEATING_FIELD_ID = re.compile(
    r"(workexperience|work[\s_-]experience|education|language|webaddress|website|"
    r"certification|licen[cs]e)[\s_-]*\d+[\s_-]*-{1,2}",
    re.I,
)

# The fields inside such a block that the generic rules would fill wrongly: the
# block's Location is the job's, not the applicant's, and its dates are the
# entry's. Everything else in there is either already handled or harmless.
BLOCK_OWNED_FIELD = (
    r"\blocation\b|\baddress\b|\bcity\b|\bstate\b|\bcountry\b|\bregion\b|"
    r"\bfrom\b|\bto\b|\bdate\b|datesection"
)


def _belongs_to_a_block(element, description: str) -> bool:
    """Whether this field is one a numbered repeating block owns."""
    if not re.search(BLOCK_OWNED_FIELD, description):
        return False
    return bool(REPEATING_FIELD_ID.search(description)) or _inside_repeating_block(element)


def _inside_repeating_block(element) -> bool:
    """Whether this input sits inside a numbered Workday section block."""
    try:
        return bool(element.evaluate(
            """(el) => {
                const headings = ["work experience", "education", "languages", "websites"];
                let node = el;
                for (let i = 0; i < 8 && node; i++) {
                    const text = (node.innerText || "").slice(0, 120).toLowerCase();
                    if (headings.some((h) => text.includes(h + " 1") || text.includes(h + " 2"))) {
                        return true;
                    }
                    node = node.parentElement;
                }
                return false;
            }"""
        ))
    except Exception:
        return False


def _has_country_selector(page) -> str:
    """Return the selected dial code when the form has its own country control."""
    for selector in (".iti__selected-dial-code", ".iti__selected-flag", "#country",
                     'button[aria-label*="country" i]'):
        try:
            element = page.locator(selector).first
            if element.count() and element.is_visible():
                text = (element.inner_text() or "").strip()
                match = re.search(r"\+\d{1,3}", text)
                return match.group(0) if match else "+"
        except Exception:
            continue
    return ""


def _pick_combobox(page, element, desired: str, clean=None) -> tuple[bool, str]:
    """Open a React combobox and choose the matching option.

    `clean` optionally normalises each option label before matching, e.g. to drop
    the "+1" that the country list appends to every country name.
    """
    try:
        element.click(timeout=5000)
        page.wait_for_timeout(600)
        listbox = element.get_attribute("aria-controls") or element.get_attribute("aria-owns")
        options_locator = (
            page.locator(f'#{listbox} [role=option]') if listbox
            else page.locator('[role=option]:visible')
        )
        labels = [t.strip().replace("\n", " ") for t in options_locator.all_inner_texts()]
        labels = [label for label in labels if label]
        if not labels:
            page.keyboard.press("Escape")
            return False, "could not read the option list"

        # A long list (the country picker has ~250 entries) is filtered by typing,
        # which is both faster and how a person would do it.
        if len(labels) > 40:
            try:
                element.fill(desired, timeout=3000)
                page.wait_for_timeout(600)
                filtered = [t.strip().replace("\n", " ")
                            for t in options_locator.all_inner_texts()]
                filtered = [label for label in filtered if label]
                if filtered:
                    labels = filtered
            except Exception:
                pass

        index = best_option(desired, [clean(label) for label in labels] if clean else labels)
        if index < 0:
            page.keyboard.press("Escape")
            return False, f'no confident match for "{desired[:38]}" among {len(labels)} options'

        options_locator.nth(index).click(timeout=5000)
        page.wait_for_timeout(350)
        return True, labels[index]
    except Exception as error:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False, f"could not open the list ({type(error).__name__})"


DOCUMENT_PATTERNS = {
    "resume": r"resume|résumé|\bcv\b",
    # "cover letter" alone is too strict for the control's own name: the input
    # Indeed reveals is called "coverUpload", which contains no "letter".
    "cover letter": r"cover[\s_-]*letter|coverletter|\bcover\b",
}

# Marks the file inputs that were on the page before a reveal, so the one a
# click produces can be identified by being new rather than by being named
# something recognisable - which it frequently is not.
SEEN_ATTRIBUTE = "data-autofill-seen"

# A file input is often not on the page until something is clicked. Indeed's
# review step shows the resume already on your account behind an "Edit" link and
# renders no file input at all; Greenhouse hides the cover letter behind
# "Attach". Without opening these, the only resume that ever reaches the employer
# is whichever one the site already had - which is the whole point of tailoring
# one, lost at the last step.
REVEAL_TRIGGERS = {
    "resume": r"replace|re-?upload|upload|change|different|another|\bedit\b|attach|"
              r"add\s+(a\s+)?(new\s+)?(resume|file)|new\s+resume",
    "cover letter": r"attach|upload|add\s+(a\s+)?cover|\bedit\b|replace|different",
}

# Never clicked while looking for an upload, whatever else the text matches.
# Revealing a file picker must never be able to send an application.
NEVER_CLICK = r"submit|send|apply\s+now|continue|next|finish|confirm|agree|accept|delete|remove"


# How far up from a control to look for the block it belongs to, and how much
# text that block may hold before it is too broad to mean anything.
BLOCK_DEPTH = 6
BLOCK_MAX_CHARS = 700


def _belongs_to_document(element, pattern: str) -> bool:
    """Whether this control sits inside a block about this document.

    This is what tells the "Edit" beside Resume from the "Edit" beside Contact -
    the two are identical as elements and differ only in what surrounds them.
    The walk stops before <body>: reaching it would match the resume section from
    anywhere on the page, which is how the contact step got opened instead.
    """
    try:
        return bool(element.evaluate(
            """(el, source) => {
                const test = new RegExp(source, "i");
                let node = el.parentElement;
                for (let index = 0; index < %d && node; index++) {
                    if (node.tagName === "BODY" || node.tagName === "HTML") return false;
                    const text = node.innerText || "";
                    if (text.length < %d && test.test(text)) return true;
                    node = node.parentElement;
                }
                return false;
            }""" % (BLOCK_DEPTH, BLOCK_MAX_CHARS),
            pattern,
        ))
    except Exception:
        return False


def _mark_existing_uploads(page) -> None:
    try:
        page.evaluate(
            """(attribute) => {
                document.querySelectorAll('input[type="file"]')
                    .forEach((el) => el.setAttribute(attribute, "1"));
            }""",
            SEEN_ATTRIBUTE,
        )
    except Exception:
        pass


def _reveal_upload(page, label: str, before: int) -> str:
    """Click whatever is hiding this document's file input. Returns what was clicked.

    `before` is how many file inputs the page already had. A click counts as
    having worked only when that number goes up: otherwise attaching the resume
    first would make every later document look already solved, and the cover
    letter would be recorded against the resume's control.
    """
    pattern = DOCUMENT_PATTERNS[label]
    trigger_pattern = REVEAL_TRIGGERS[label]
    try:
        candidates = page.locator(
            'button, a, label, [role="button"], [role="radio"], [role="tab"]'
        ).all()[:150]
    except Exception:
        return ""
    for element in candidates:
        try:
            if not element.is_visible():
                continue
            text = " ".join((element.inner_text() or "").split())
            if not text or len(text) > 44:
                continue
            lowered = text.casefold()
            if re.search(NEVER_CLICK, lowered) or not re.search(trigger_pattern, lowered):
                continue
            if not _belongs_to_document(element, pattern):
                continue
            element.click(timeout=4000)
            page.wait_for_timeout(1400)
            if page.locator('input[type="file"]').count() > before:
                return text
        except Exception:
            continue
    return ""


# Wording sites use when they reject a file rather than store it.
UPLOAD_REJECTED = re.compile(
    r"(file|upload|document|resume|attachment)[^.]{0,40}"
    r"(too large|exceeds|not supported|invalid|failed|error|could not|unable)|"
    r"(only|must be)[^.]{0,30}(pdf|docx?|accepted)|"
    r"(upload|attach)[^.]{0,20}(failed|unsuccessful)|virus|malware",
    re.I,
)


def _upload_accepted(page, element, filename: str) -> tuple[bool, str]:
    """Whether the site actually took the file, as opposed to the picker taking it.

    `set_input_files` succeeds as soon as the browser attaches a local file to the
    input. That is not the employer accepting it: the page may reject the type or
    the size, may still be showing a previously attached document, or may simply
    never process the change event. Treating the call's return value as proof is
    how an application went out with the wrong resume attached, or none.

    Three things are checked: the input holds this file, the page names it
    somewhere a person could see, and no rejection message appeared.
    """
    stem = re.sub(r"\.[^.]+$", "", filename)[:40]

    try:
        attached = element.evaluate(
            "(el) => (el.files && el.files[0]) ? el.files[0].name : ''"
        ) or ""
    except Exception:
        attached = ""
    if attached and attached != filename:
        return False, f'the control now holds "{attached[:40]}", not the tailored file'

    try:
        body = page.locator("body").inner_text(timeout=4000)
    except Exception:
        body = ""

    rejection = UPLOAD_REJECTED.search(body or "")
    if rejection:
        return False, f"the site reported a problem: {rejection.group(0)[:70]}"

    if attached == filename:
        # The browser holds the right file. Whether the page has echoed the name
        # yet is a rendering detail, and some sites never do.
        return True, ""

    if stem and stem.casefold() in (body or "").casefold():
        return True, ""

    return False, (
        "the file was selected but the page never showed it - check the upload "
        "before submitting"
    )


def _attach_document(page, label: str, absolute, filled: list, skipped: list) -> None:
    """Put our file into the page's upload control, opening it first if need be."""
    pattern = DOCUMENT_PATTERNS[label]

    def candidates():
        try:
            everything = page.locator('input[type="file"]').all()
        except Exception:
            return [], []
        named = [e for e in everything if re.search(pattern, _describe(page, e))]
        return everything, named

    everything, matches = candidates()
    opened = ""
    if not matches and not (label == "resume" and len(everything) == 1):
        _mark_existing_uploads(page)
        opened = _reveal_upload(page, label, len(everything))
        everything, matches = candidates()
        if opened and not matches:
            # The control a click just produced, identified by being the one that
            # was not there a moment ago. Naming is unreliable - Indeed's is
            # "coverUpload" - but newness is not.
            fresh = page.locator(f'input[type="file"]:not([{SEEN_ATTRIBUTE}])').all()
            if len(fresh) == 1:
                matches = fresh

    # A styled drop zone hides its input and labels it nothing useful. When the
    # page offers exactly one upload and we are placing the resume, use it.
    if not matches and label == "resume" and len(everything) == 1:
        matches = everything

    if len(matches) == 1:
        try:
            matches[0].set_input_files(str(absolute), timeout=8000)
            page.wait_for_timeout(1500)
            accepted, detail = _upload_accepted(page, matches[0], absolute.name)
            if accepted:
                filled.append({
                    "field": label,
                    "value": absolute.name + (f' (opened via "{opened}")' if opened else ""),
                })
            else:
                skipped.append({"field": label, "reason": detail})
        except Exception as error:
            skipped.append({"field": label, "reason": f"upload failed ({type(error).__name__})"})
    elif not matches:
        skipped.append({
            "field": label,
            "reason": ("this page has no upload control - on Indeed and similar, open the "
                       "resume step and run Fill again"),
        })
    else:
        skipped.append({"field": label, "reason": f"{len(matches)} upload controls matched - ambiguous"})


def fill_form(page, profile: dict, answers: dict, resume_path, cover_letter_path,
              job_location: str = "", accept_agreements: bool = False,
              application_id: str = "") -> dict:
    """Fill what is safe. Returns a report of filled, skipped and pending fields."""
    filled, skipped, pending = [], [], []

    # Workday's repeating sections need their own handling: numbered blocks, a
    # per-block "Location" that means the job's location, and split date inputs.
    # Run it first so the generic sweep then sees those fields already filled.
    if workday.is_workday(page):
        try:
            section_filled, section_pending = workday.fill_all(page, profile)
            filled.extend(section_filled)
            pending.extend(section_pending)
        except Exception as error:
            skipped.append({"field": "Workday sections",
                            "reason": f"{type(error).__name__}: {error}"[:90]})

    browser_session.check_cancelled(application_id)
    dial_code = _has_country_selector(page)

    # ---- choice controls (React comboboxes and any native selects) ----
    #
    # Addressed by stable id/name, never by position. Answering one question can
    # reveal another (on Greenhouse, "Hispanic/Latino? -> No" inserts the race
    # question), and Playwright's .all() hands back index-based locators that
    # re-resolve against the live DOM - so an insertion shifts every later index
    # and silently drops the last control. Re-scan instead, until nothing new
    # appears.
    handled_ids: set[str] = set()
    for _sweep in range(4):
        targets = []
        for selector in ('input[role="combobox"]', "select"):
            for element in page.locator(selector).all():
                try:
                    if not element.is_visible():
                        continue
                    identifier = element.get_attribute("id") or element.get_attribute("name") or ""
                    if not identifier or identifier in handled_ids:
                        continue
                    targets.append((selector, identifier))
                except Exception:
                    continue
        if not targets:
            break

        for selector, identifier in targets:
            if identifier in handled_ids:
                continue
            handled_ids.add(identifier)
            try:
                element = page.locator(
                    f'#{identifier}' if page.locator(f'#{identifier}').count() == 1
                    else f'{selector}[name="{identifier}"]'
                ).first
                if not element.count() or not element.is_visible():
                    continue

                description = _describe(page, element)
                if not description:
                    continue

                # The phone-number country control, identified by its element id -
                # NOT by the word "country" appearing in the question, which also
                # occurs in "...sponsorship to work in the country in which the job
                # is located" and would put a country name into a Yes/No dropdown.
                if COUNTRY_CONTROL.search(identifier):
                    country = resolve_phone_country(profile)
                    if not country:
                        pending.append({"field": description[:70],
                                        "reason": "set your work country in Profile"})
                        continue
                    picked, detail = _pick_combobox(
                        page, element, country, clean=strip_dial_suffix
                    )
                    if picked:
                        filled.append({"field": description[:70], "value": detail[:60]})
                    else:
                        pending.append({"field": description[:70], "reason": detail})
                    continue

                desired, reason = resolve_choice(
                    profile, description, job_location, answers, identifier,
                    accept_agreements=accept_agreements,
                )
                if not desired:
                    pending.append({"field": description[:70], "reason": reason})
                    continue

                if selector == "select":
                    labels = [x.strip() for x in element.locator("option").all_inner_texts()]
                    index = best_option(desired, labels)
                    if index < 0:
                        pending.append({"field": description[:70],
                                        "reason": f'no match for "{desired[:30]}"'})
                        continue
                    element.select_option(index=index, timeout=4000)
                    filled.append({"field": description[:70], "value": labels[index][:60]})
                    continue

                candidates = (
                    list(AFFIRMATIVE_OPTIONS) if is_agreement(description) else [desired]
                )
                for attempt in candidates:
                    picked, detail = _pick_combobox(page, element, attempt)
                    if picked:
                        break
                if picked:
                    entry = {"field": description[:70], "value": detail[:60]}
                    if is_agreement(description):
                        entry["agreement"] = True
                    filled.append(entry)
                else:
                    pending.append({"field": description[:70], "reason": detail})
            except Exception as error:
                pending.append({"field": identifier[:70], "reason": f"{type(error).__name__}"})

    # ---- free-text inputs and textareas ----
    for element in page.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea"
    ).all():
        try:
            if not element.is_visible() or not element.is_editable():
                continue
            input_type = (element.get_attribute("type") or "text").casefold()
            if input_type in {"checkbox", "radio", "file", "submit", "button", "image"}:
                continue
            role = (element.get_attribute("role") or "").casefold()
            element_id = element.get_attribute("id") or ""
            if role == "combobox" or element_id in handled_ids:
                continue  # already handled as a choice control

            description = _describe(page, element)
            if not description:
                continue

            reason = _blocked(description)
            if reason:
                pending.append({"field": description[:70], "reason": reason})
                continue

            # Inside a work-experience or education block, "Location" is where the
            # job was and the dates are that entry's dates. Those belong to
            # Workday's handler, which knows which block is which entry; the
            # generic rules know only one address and would put it in all of them.
            if _belongs_to_a_block(element, description):
                skipped.append({
                    "field": description[:70],
                    "reason": "part of a Work Experience / Education block - filled from that entry",
                })
                continue

            if (element.input_value() or "").strip():
                skipped.append({"field": description[:70], "reason": "already filled"})
                continue

            value = resolve_value(profile, description, answers, element.get_attribute("name"))
            if not value:
                pending.append({"field": description[:70], "reason": "no saved answer"})
                continue

            # A separate country selector means the number must not repeat its code.
            if dial_code and re.search(r"\bphone\b|telephone|mobile|\btel\b", description):
                value = split_dial_code(value, dial_code)

            element.fill(value, timeout=4000)
            filled.append({"field": description[:70], "value": value[:60]})
        except Exception as error:
            skipped.append({"field": "unknown field", "reason": f"could not fill ({type(error).__name__})"})

    # ---- documents ----
    browser_session.check_cancelled(application_id)
    for label, path in (("resume", resume_path), ("cover letter", cover_letter_path)):
        if not path:
            continue
        absolute = (BACKEND_DIR / path).resolve()
        if not absolute.is_file():
            skipped.append({"field": label, "reason": "generated file missing"})
            continue
        _attach_document(page, label, absolute, filled, skipped)

    # ---- checkboxes and radios are always the user's to tick ----
    for element in page.locator('input[type="checkbox"], input[type="radio"]').all():
        try:
            if element.is_visible():
                pending.append({
                    "field": _describe(page, element)[:70] or "checkbox",
                    "reason": "tick this yourself",
                })
        except Exception:
            continue

    return {
        "filled": filled,
        "skipped": skipped,
        "needs_your_input": pending[:60],
        "filled_count": len(filled),
        "skipped_count": len(skipped),
        "pending_count": len(pending),
    }


async def open_and_fill(job_url: str, profile: dict, answers: dict, resume_path,
                        cover_letter_path, job_location: str = "",
                        accept_agreements: bool = False,
                        application_id: str = "") -> dict:
    """Open the employer form in the visible browser and fill it. Never submits."""

    def command(worker):
        page = worker.context().new_page()
        # Bound to this application before anything else happens, so every later
        # step acts on this page rather than on whichever tab happens to be
        # newest - a second application, or a popup, used to be indistinguishable.
        browser_session.bind_page(application_id, page)
        page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        browser_session.check_cancelled(application_id)

        open_application(page)
        browser_session.check_cancelled(application_id)

        report = fill_form(page, profile, answers, resume_path, cover_letter_path,
                           job_location, accept_agreements, application_id)
        report["url"] = page.url
        report["note"] = (
            "Nothing was submitted. Review every field in the browser window, complete "
            "anything listed as needing your input, then click Submit yourself."
        )
        return report

    with browser_session.exclusive(application_id, "Open and fill"):
        return await browser_session.run(command, timeout=300,
                                         application_id=application_id)


def _question_label(page, element) -> str:
    """The full question as a person would read it.

    Combines the visible label with any sub-prompt beneath it, because forms
    routinely split a question across the two - taking only the label yields
    fragments like "What", which cannot be answered.
    """
    parts: list[str] = []
    try:
        element_id = element.get_attribute("id")
        if element_id:
            label = page.locator(f'label[for="{element_id}"]')
            if label.count():
                parts.append((label.inner_text() or "").strip())
            description = page.locator(f'#{element_id}-description')
            if description.count():
                parts.append((description.inner_text() or "").strip())
    except Exception:
        pass

    if not any(parts):
        for attribute in ("aria-label", "placeholder", "title"):
            try:
                value = element.get_attribute(attribute)
            except Exception:
                value = None
            if value and value.strip():
                parts.append(value.strip())
                break

    combined = re.sub(r"\s+", " ", " - ".join(p for p in parts if p)).strip(" *-")
    return combined


async def collect_open_questions(job_url: str, profile: dict, answers: dict) -> list[dict]:
    """Find employer-specific free-text questions this app cannot already answer."""

    def command(worker):
        # The step the applicant is actually on, not a fresh copy of the job
        # listing. Opening the original URL in a new tab asked the questions of
        # page one of the application while the applicant stood on page three,
        # collected answers to the wrong step, and left an orphan tab behind that
        # the next "fill the page" call could then pick up as the active page.
        page = _active_page(worker)
        opened_here = False
        try:
            if not _has_form_controls(page):
                opened_here = bool(open_application(page))
                page.wait_for_timeout(800)

            found, seen = [], set()

            # Choice questions the profile cannot answer - "Have you interviewed
            # here before?" and the like. Collected with their real options so the
            # user picks once and the answer is stored for the next run.
            for element in page.locator('input[role="combobox"]').all():
                try:
                    if not element.is_visible():
                        continue
                    identifier = element.get_attribute("id") or element.get_attribute("name") or ""
                    description = _describe(page, element)
                    if not identifier or identifier in seen or not description:
                        continue
                    if COUNTRY_CONTROL.search(identifier):
                        continue
                    desired, _reason = resolve_choice(
                        profile, description, "", answers, identifier
                    )
                    if desired:
                        continue  # already answerable
                    blocked = _blocked(description)
                    options = []
                    try:
                        element.click(timeout=4000)
                        page.wait_for_timeout(500)
                        listbox = (element.get_attribute("aria-controls")
                                   or element.get_attribute("aria-owns"))
                        if listbox:
                            raw = page.locator(f'#{listbox} [role=option]').all_inner_texts()
                            options = [" ".join(text.split()) for text in raw if text.strip()][:12]
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                    except Exception:
                        pass
                    if not options:
                        continue
                    question = _question_label(page, element)
                    if not looks_like_a_question(question):
                        continue
                    seen.add(identifier)
                    found.append({
                        "field_name": identifier,
                        "question": question,
                        "multiline": False,
                        "options": options,
                        "legal": bool(blocked),
                    })
                except Exception:
                    continue

            for element in page.locator("input[type=text], input:not([type]), textarea").all():
                try:
                    if not element.is_visible() or not element.is_editable():
                        continue
                    if (element.get_attribute("role") or "").casefold() == "combobox":
                        continue
                    description = _describe(page, element)
                    if not description or _blocked(description):
                        continue
                    if (element.input_value() or "").strip():
                        continue
                    if resolve_value(profile, description, answers, element.get_attribute("name")):
                        continue  # the profile already answers this
                    name = element.get_attribute("name") or element.get_attribute("id") or ""
                    if not name or name in seen:
                        continue
                    if name.casefold() in SITE_CHROME_NAMES:
                        continue
                    if re.search(SITE_CHROME_PATTERN, description):
                        continue
                    question = _question_label(page, element)
                    if not looks_like_a_question(question):
                        continue
                    seen.add(name)
                    found.append({
                        "field_name": name,
                        "question": question,
                        "multiline": element.evaluate("e => e.tagName.toLowerCase()") == "textarea",
                        "options": [],
                        "legal": False,
                    })
                except Exception:
                    continue
            return found
        finally:
            # The page belongs to the applicant now - it is the step they are
            # standing on. Closing it here (which this did, back when the
            # collector opened its own tab) would throw away their progress.
            pass

    return await browser_session.run(command, timeout=180)


# Hosts that are never an employer's application form, whatever tab order says.
NOT_AN_APPLICATION = re.compile(
    r"^(www\.)?(google|bing|duckduckgo|mail\.google|outlook|gmail|youtube|"
    r"facebook|twitter|x|reddit|chatgpt|claude)\.", re.I
)


def _has_form_controls(page) -> bool:
    """Whether this page is showing a form rather than a listing or a search result."""
    try:
        return page.locator(
            'input:not([type="hidden"]), select, textarea, [role="combobox"]'
        ).count() >= 3
    except Exception:
        return False


def _page_identity(page) -> dict:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(page.url or "")
        return {"url": page.url, "host": parsed.netloc, "path": parsed.path,
                "title": (page.title() or "")[:80]}
    except Exception:
        return {"url": "", "host": "", "path": "", "title": ""}


def _active_page(worker, expect_host: str = "", application_id: str = ""):
    """The page this application is being filled on.

    Taking the newest tab in the whole browser was wrong in a way that is easy to
    miss and expensive when it happens: open a second application, or let a
    "share this job" popup appear, or leave the tab the question collector used to
    open, and the newest tab is not the form the applicant is working on. The
    autofiller would then type one application's answers into another's page.

    A page bound to this application wins. Failing that, a page on the employer's
    own host wins. Only when neither exists does tab order decide, and the
    identity of whatever was chosen is reported back so a wrong guess is visible
    rather than silent.
    """
    pages = [p for p in worker.context().pages if p.url and not p.url.startswith("about:")]
    pages = [p for p in pages if not NOT_AN_APPLICATION.match(_page_identity(p)["host"])]
    if not pages:
        raise RuntimeError(
            "No application page is open in the browser. Press 'Open & fill form' "
            "first, or open the application yourself in the JobApplyAI window."
        )

    bound = browser_session.bound_page(application_id) if application_id else None
    if bound is not None and bound in pages:
        return bound

    if expect_host:
        matching = [p for p in pages if _page_identity(p)["host"].endswith(expect_host)]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            # Several tabs on the employer's site: prefer one that is a form.
            forms = [p for p in matching if _has_form_controls(p)]
            return (forms or matching)[-1]
        raise AmbiguousPage(
            f"The open tab is {_page_identity(pages[-1])['host'] or 'unknown'}, but this "
            f"application is on {expect_host}. Bring the right tab to the front, or "
            "press 'Open & fill form' to start it."
        )

    return pages[-1]


class AmbiguousPage(RuntimeError):
    """The page to act on could not be identified, so nothing was touched."""


async def fill_open_page(profile: dict, answers: dict, resume_path, cover_letter_path,
                         job_location: str = "", accept_agreements: bool = False,
                         application_id: str = "", expect_host: str = "") -> dict:
    """Fill the page already open in the browser, wherever the user has navigated to.

    This is what makes multi-step and sign-in-walled applications workable:
    Workday, Taleo and similar put the real form several clicks past a login, so
    a single navigate-and-fill pass can never reach it. Sign in yourself, get to
    any step, then run this - and again on each subsequent step.
    """

    def command(worker):
        page = _active_page(worker, expect_host, application_id)
        browser_session.bind_page(application_id, page)
        page.wait_for_timeout(600)
        report = fill_form(page, profile, answers, resume_path, cover_letter_path,
                           job_location, accept_agreements, application_id)
        report["url"] = page.url
        report["title"] = page.title()
        report["page_identity"] = _page_identity(page)
        report["note"] = (
            "Filled the page you had open. Nothing was submitted. On a multi-step "
            "application, move to the next step and run this again."
        )
        return report

    with browser_session.exclusive(application_id, "Fill this page"):
        return await browser_session.run(command, timeout=240,
                                         application_id=application_id)
