"""Whether a block of text is actually a job posting, and whose posting it is.

Three different places used to decide this three different ways: the reader kept
anything over 200 characters, the paste endpoint demanded 60 words, and the
tailoring gate demanded 60 words again with its own placeholder pattern. So a
sign-in page could be stored as a description by the first, a genuine short
posting refused by the second, and the same text judged differently depending on
which door it came through.

Length alone was never the right test. A sign-in wall runs to hundreds of words;
a real posting can be under a hundred. What separates them is what the text is
made of - a posting states responsibilities, requirements or qualifications and
addresses a candidate - and whether it belongs to the job that asked for it.
Length stays, but only to break the tie when nothing else is conclusive.
"""
import re

# Text that means the reader landed somewhere other than a posting. These are
# matched against the whole block: a posting may mention "sign in" in passing,
# but a sign-in wall is mostly this and little else.
WALL_MARKERS = (
    "sign in to continue", "log in to continue", "please sign in", "please log in",
    "create an account to", "verify you are human", "verifying you are human",
    "unusual traffic", "are you a robot", "captcha", "access denied",
    "enable javascript", "turn on javascript", "cookies are disabled",
    "this page isn't working", "page not found", "404 not found",
    "no longer accepting applications", "this job has expired",
    "this job is no longer available", "the posting you are looking for",
)

# What a posting is made of. Not every posting has all of them; having none of
# them, in text of any length, means this is not one.
POSTING_MARKERS = (
    "responsibilit", "qualification", "requirement", "what you'll do",
    "what you will do", "what you'll bring", "who you are", "about the role",
    "about this role", "about the job", "the role", "we are looking for",
    "we're looking for", "you will", "you'll", "your role", "experience with",
    "experience in", "skills", "duties", "job summary", "position summary",
    "minimum qualification", "preferred qualification", "benefits",
    "salary", "compensation", "apply", "team", "candidate",
)

# The sentence the sync used to invent when it captured nothing. It reads like a
# description and counts as one everywhere that only checks for emptiness.
PLACEHOLDER = re.compile(
    r"^\s*.{0,120}?\b(?:at|@)\b.{0,120}?\.\s*see the (?:original|full) posting\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Below this, with no posting markers at all, there is nothing to tailor against.
# Above it, content decides. Deliberately low: a terse startup posting is real.
SHORT_WORDS = 40

# Enough of the text being wall markers means the page is a wall, not a posting
# that mentions one.
WALL_DENSITY = 0.25


class NotAPosting(Exception):
    """Raised with a reason the user can act on."""

    def __init__(self, reason: str, kind: str = "unusable"):
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


def words(text: str) -> int:
    return len((text or "").split())


def _wall_reason(lowered: str) -> str:
    """Which wall this is, if it is one."""
    hits = [marker for marker in WALL_MARKERS if marker in lowered]
    if not hits:
        return ""
    # A long posting that happens to contain "apply" near "sign in to continue"
    # is still a posting. A wall is short and is mostly the marker.
    covered = sum(len(marker) for marker in hits)
    if covered / max(len(lowered), 1) >= WALL_DENSITY or len(lowered.split()) < 120:
        return hits[0]
    return ""


def posting_signals(text: str) -> int:
    """How many of the things a posting is made of appear in this text."""
    lowered = (text or "").casefold()
    return sum(1 for marker in POSTING_MARKERS if marker in lowered)


def identity_matches(text: str, company: str = "", title: str = "") -> bool:
    """Whether this text plausibly belongs to the job that asked for it.

    Checked loosely on purpose. A posting rarely repeats the company's legal
    suffix and often words the title differently from the search card, so this
    asks only that some distinctive part of one of them appears.
    """
    lowered = (text or "").casefold()
    if not lowered:
        return False
    for value in (company, title):
        for part in re.split(r"[^a-z0-9+#.]+", (value or "").casefold()):
            # Short words like "of", "inc" or "ai" match everything.
            if len(part) >= 4 and part not in {"inc", "llc", "corp", "group",
                                               "limited", "company", "services",
                                               "senior", "junior", "staff"}:
                if part in lowered:
                    return True
    return False


def check(text: str, company: str = "", title: str = "",
          require_identity: bool = False) -> str:
    """Return the cleaned posting, or raise NotAPosting with why.

    `require_identity` is for text fetched by the reader, where landing on the
    wrong posting is a real failure mode. Text the user pasted is taken at their
    word - they can see what they copied.
    """
    body = (text or "").strip()
    if not body:
        raise NotAPosting("No description has been captured for this job yet.",
                          kind="missing")

    if PLACEHOLDER.match(body):
        raise NotAPosting(
            "The stored text is a placeholder, not the posting.", kind="missing"
        )

    lowered = body.casefold()
    wall = _wall_reason(lowered)
    if wall:
        raise NotAPosting(
            f"That page is a sign-in or verification wall, not the posting "
            f"(it says \"{wall}\"). Open the job in the browser, clear it, then retry.",
            kind="wall",
        )

    signals = posting_signals(body)
    if signals == 0:
        raise NotAPosting(
            "That text does not read like a job posting - it states no "
            "responsibilities, requirements or qualifications.",
            kind="unusable",
        )

    # Length only breaks the tie: very short AND barely any posting structure.
    if words(body) < SHORT_WORDS and signals < 3:
        raise NotAPosting(
            f"Only {words(body)} words were captured, and they do not set out "
            "what the role involves. Paste the full posting instead.",
            kind="thin",
        )

    if require_identity and (company or title) and not identity_matches(body, company, title):
        raise NotAPosting(
            "The page that was read does not mention this company or role, so it "
            "is probably a different posting. The description was not stored.",
            kind="mismatch",
        )

    return body


def usable(text: str, company: str = "", title: str = "",
           require_identity: bool = False) -> bool:
    try:
        check(text, company, title, require_identity)
        return True
    except NotAPosting:
        return False
