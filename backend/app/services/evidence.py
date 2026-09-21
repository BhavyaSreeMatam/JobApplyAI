"""Which lines are the reasons to hire this candidate, and must survive trimming.

Getting a resume onto one page is arithmetic. Deciding what to lose is not, and
priority alone turned out to be the wrong instrument: it ranked lines against
each other without knowing what any of them proved. A run against a search-ranking
posting cut the first-author publication and the teaching section, and kept seven
lines of skills list - a page of the right length making the weakest possible case.

Two things protect a line here.

  Sole evidence  It is the only line covering a requirement the posting actually
                 states. Losing it does not shorten the resume, it removes an
                 answer to a question the employer asked.
  Strong kinds   Publications, mentoring and teaching, production deployment,
                 model evaluation, and implementing models are hard to evidence,
                 slow to earn, and easy to delete because they are often one line
                 each. A skills list is none of those things: it is the cheapest
                 content on the page and the first thing that should go.

Everything here is deterministic, so protecting evidence costs nothing and the
decision can be explained after the fact.
"""
import re

from app.services import ats_scorer

# Evidence that is disproportionately expensive to acquire and cheap to lose.
# Matched against a line's own text, so it protects the bullet that carries the
# proof rather than a section heading that merely names it.
STRONG_EVIDENCE = (
    ("publication", r"publish|published|paper|proceedings|conference|journal|"
                    r"first[\s-]?author|\bdoi\b|arxiv|ismar|neurips|\bicml\b|\bcvpr\b|\bacl\b"),
    ("mentoring", r"mentor|taught|teaching|instructor|lectur|course assistant|"
                  r"\bta\b|workshop|supervis|onboard(ed|ing)"),
    ("deployment", r"deploy|in production|shipped|released|docker|kubernetes|ci/cd|"
                   r"\becr\b|\bec2\b|lambda|launch|serve[ds]?\b|uptime"),
    ("evaluation", r"evaluat|benchmark|a/b|ablation|ragas|precision|recall|f1|"
                   r"accuracy|validation|error analysis|ground truth|labell?ed set"),
    ("model_work", r"\bcnn\b|\brnn\b|\blstm\b|transformer|pytorch|tensorflow|"
                   r"fine[\s-]?tun|trained? (a |the )?model|embedding|classifier"),
)

# Kinds that may be trimmed at all. A skills line is compressed instead: dropping
# its off-topic terms frees the same space without losing a claim.
TRIMMABLE_KINDS = {"bullet", "body"}


def strong_kinds(text: str) -> set[str]:
    lowered = (text or "").casefold()
    return {name for name, pattern in STRONG_EVIDENCE if re.search(pattern, lowered)}


def required_terms(analysis: dict) -> list[str]:
    """The terms the posting states it requires, hardest requirements first."""
    keywords = analysis.get("keywords") or []
    order = {"required": 0, "preferred": 1, "nice_to_have": 2}
    wanted = sorted(
        (k for k in keywords if k.get("term")),
        key=lambda k: order.get(k.get("importance", "preferred"), 1),
    )
    return [k["term"] for k in wanted]


def job_vocabulary(analysis: dict) -> set[str]:
    """Everything the posting talks about, as matchable tokens.

    Used to decide which skills survive compression: a term the employer never
    mentions is not earning its place on a resume aimed at them.
    """
    parts = [k.get("term", "") for k in (analysis.get("keywords") or [])]
    parts += list(analysis.get("responsibilities") or [])
    parts += list(analysis.get("hard_requirements") or [])
    parts += list(analysis.get("outcomes") or [])
    parts.append(analysis.get("normalized_title", ""))
    return ats_scorer.content_tokens(" ".join(str(p) for p in parts))


def coverage(document, analysis: dict) -> dict[str, list[int]]:
    """requirement term -> the indices of the live lines that evidence it."""
    found: dict[str, list[int]] = {}
    for term in required_terms(analysis):
        for line in document.live:
            if line.kind not in TRIMMABLE_KINDS and line.kind != "skills":
                continue
            if ats_scorer.phrase_present(term, line.text):
                found.setdefault(term, []).append(line.index)
    return found


def protected(document, analysis: dict) -> set[int]:
    """Line indices that trimming must not touch.

    Deliberately generous. A resume that fits the page by deleting the reasons to
    interview someone has not succeeded at anything, and the cost of protecting a
    line too many is one more skills term compressed away.
    """
    keep: set[int] = set()

    for term, indices in coverage(document, analysis).items():
        # The only line answering a stated requirement. Two lines covering it can
        # afford to lose one; one cannot.
        if len(indices) == 1:
            keep.add(indices[0])

    for line in document.live:
        if line.kind in TRIMMABLE_KINDS and strong_kinds(line.text):
            keep.add(line.index)
        if line.priority <= 1:
            keep.add(line.index)
    return keep


def lost_requirements(document, analysis: dict, before: dict[str, list[int]]) -> list[int]:
    """Lines to put back because trimming took a requirement's last evidence.

    A safety net behind `protected`: coverage is recomputed after the fact, and
    anything that went from covered to uncovered names the line that has to
    return. Something else gives up the space instead.
    """
    after = coverage(document, analysis)
    restore: list[int] = []
    for term, indices in before.items():
        if term in after or not indices:
            continue
        restore.extend(indices)
    return sorted(set(restore))
