"""What a control on an employer's page actually does, decided in one place.

Every click this application makes goes through here. That is the point: the
rules about what must never be pressed were previously spread across the
autofiller, the question collector, the upload revealer and the Workday adapter,
each with its own list, and one of those lists had "Submit application form" in
it as something to click to *open* an application.

Four kinds of control, and they are not interchangeable:

  OPEN      starts an application - "Apply", "Apply now", "I'm interested"
  NAVIGATE  moves between steps - "Next", "Continue", "Save and continue"
  SAVE      records a draft without sending it - "Save", "Save draft"
  SUBMIT    sends the application to the employer

SUBMIT is never clicked. Not by navigation, not while hunting for a file input,
not by a platform adapter, not on a retry. The applicant presses it themselves on
the employer's own page, having read what they are sending.

Matching is deliberately conservative in both directions. A bare "Apply" substring
is not enough to call something safe - "Apply" appears inside "Submit application"
and inside "Reapply" - so the whole label is examined, submit wording is tested
first, and anything that cannot be classified confidently comes back UNKNOWN and
is left alone.
"""
import re

OPEN = "open"
NAVIGATE = "navigate"
SAVE = "save"
SUBMIT = "submit"
UNKNOWN = "unknown"

# Tested first and allowed to veto everything else. If a label could plausibly
# send the application, it is a submit, whatever else it also says.
SUBMIT_PATTERN = re.compile(
    r"\bsubmit\b|\bsend\b(?!\s+(me|a\s+copy|link))|submit\s+application|"
    r"finish\s+(and\s+)?(apply|submit)|complete\s+application|"
    r"apply\s+now\s+and\s+submit|confirm\s+(and\s+)?(submit|send|apply)|"
    r"\bfinalize\b|\bfinalise\b|i\s+accept\s+and\s+submit",
    re.I,
)

# Legal declarations and consents. Never pressed automatically, and kept separate
# from submission so the reason reported to the applicant is accurate.
DECLARATION_PATTERN = re.compile(
    r"\bi\s+(agree|accept|certify|consent|acknowledge|declare|attest|confirm\s+that)\b|"
    r"\bagree\s+(and|to)\b|accept\s+(the\s+)?terms|electronic\s+signature|\be-?sign\b",
    re.I,
)

OPEN_PATTERN = re.compile(
    r"^\s*(apply\s*(now|here|for\s+this\s+(job|role|position))?|"
    r"apply\s+on\s+company\s+site|easy\s+apply|quick\s+apply|start\s+(your\s+)?application|"
    r"i'?m\s+interested|begin\s+application|continue\s+to\s+application)\s*$",
    re.I,
)

NAVIGATE_PATTERN = re.compile(
    r"^\s*(next|continue|save\s+(and|&)\s+continue|save\s+(and|&)\s+next|"
    r"next\s+step|continue\s+to\s+\w+|proceed|go\s+to\s+next|"
    r"review\s*(your\s+application|and\s+submit)?|back|previous)\s*[>»→]?\s*$",
    re.I,
)

SAVE_PATTERN = re.compile(
    r"^\s*(save|save\s+draft|save\s+for\s+later|save\s+(my\s+)?(progress|application))\s*$",
    re.I,
)


def classify(label: str) -> str:
    """What pressing this control would do. UNKNOWN when it cannot be told."""
    text = " ".join(str(label or "").split())
    if not text:
        return UNKNOWN
    # Submit is tested before anything else and is never overridden. "Review and
    # submit" reads as navigation and is not; "Apply and submit" contains the word
    # this used to trust.
    if SUBMIT_PATTERN.search(text):
        return SUBMIT
    if DECLARATION_PATTERN.search(text):
        return SUBMIT  # not a submission, but equally never pressed automatically
    if SAVE_PATTERN.match(text):
        return SAVE
    if OPEN_PATTERN.match(text):
        return OPEN
    if NAVIGATE_PATTERN.match(text):
        return NAVIGATE
    return UNKNOWN


def is_submit(label: str) -> bool:
    return classify(label) == SUBMIT


def may_click(label: str, allowed: set[str]) -> bool:
    """Whether this control may be pressed for the purpose described by `allowed`.

    Callers name what they are trying to do. Nothing may click a SUBMIT, and an
    UNKNOWN label is only pressed where a caller has explicitly opted into it -
    which no submission-adjacent caller does.
    """
    kind = classify(label)
    if kind == SUBMIT:
        return False
    return kind in allowed


def refusal(label: str) -> str:
    """Why a control was left alone, in words the applicant can act on."""
    text = " ".join(str(label or "").split())[:60]
    if SUBMIT_PATTERN.search(text):
        return f'"{text}" submits the application - press it yourself when ready'
    if DECLARATION_PATTERN.search(text):
        return f'"{text}" is a declaration - only you can make it'
    return f'"{text}" could not be identified safely, so it was left alone'


# What each caller is allowed to press.
FOR_OPENING = {OPEN}
FOR_NAVIGATION = {NAVIGATE, SAVE}
FOR_REVEALING_UPLOAD: set[str] = set()  # only UNKNOWN-safe helpers, checked separately
