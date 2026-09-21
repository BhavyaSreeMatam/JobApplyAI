"""Your own resume, read as lines of styled text - and written back the same way.

The point of this module is a promise: tailoring changes words, never layout.
Nothing here invents a document. It reads the file you uploaded, hands the model
the sentences and nothing else, and puts the model's wording back into the exact
positions, fonts, weights, indents and dates it came from.

Two formats, with different guarantees:

  .docx  Edited in place. Runs keep their own font, size, colour and weight, so
         the output is byte-for-byte your styling with different words in it.
         This is the format to prefer.
  .pdf   Reconstructed. A PDF has no paragraphs, only positioned glyphs, so
         rewriting a sentence to a different width cannot preserve the original
         object - the page has to be drawn again. Everything measurable is
         carried across (font family, size, weight, italics, underlines, indents,
         hyperlinks, right-aligned dates, page breaks), which in practice looks
         like the original; it is a faithful copy, not the same file.

Inline weight is preserved through a tiny markup - <b>, <i>, <u> - because a
line like "**Programming Languages:** Python, Java" is one line with two styles,
and handing the model plain text would lose the bold the moment it edited that
line. The model is told to keep the tags where they are.

What is editable is deliberately narrow. Headings, names, employers, job titles,
institutions and dates are never sent for rewriting: those are facts of record,
and a tailoring pass has no business touching them. Bullets, summary prose and
skill lists are what tailoring is actually for.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.workday import format_dates

# A line's indent, rounded to this many points before being compared. PDF x
# positions wobble by a fraction of a point between lines that are visually
# flush, and an unrounded comparison turns that into a ragged left edge.
INDENT_ROUNDING = 3.0

# A gap this wide inside one line, with the text after it ending near the right
# margin, is a right-aligned tail: the dates column on an experience entry.
RIGHT_TAIL_GAP = 60.0
RIGHT_TAIL_MARGIN = 90.0

# How much longer a re-worded line may be than the one it replaces. Tailoring
# that adds half a sentence everywhere silently repaginates the resume, which is
# a layout change however carefully the words were chosen.
LENGTH_ALLOWANCE = 1.12

# Baseline spacing beyond the document's own leading that marks a new paragraph
# rather than a wrapped row.
PARAGRAPH_GAP = 1.3

BULLET_GLYPHS = "•●▪◦‣⁃·-–"

# Editable kinds. Everything else is a fact of record or a structural label.
EDITABLE_KINDS = {"summary", "bullet", "skills", "body"}

# Lines the selection pass may keep, shorten or drop. Entries are in here so a
# whole irrelevant project can go, but their text is never rewritten: an
# employer, a job title and a set of dates are facts of record.
SELECTABLE_KINDS = EDITABLE_KINDS | {"entry"}

# Never removed and never rewritten. A heading goes only when its section empties.
STRUCTURAL_KINDS = {"name", "contact", "heading"}

# Sections whose entries exist only to introduce evidence. A project heading with
# no bullets left under it contributes a title and nothing else.
#
# Publications, awards and certifications are deliberately NOT here. A citation is
# self-contained - "Morgan, A. (2025). Adaptive VR... IEEE ISMAR. First author." is the
# whole evidence, and pruning it for having no bullet beneath it deleted the
# strongest line on the resume.
EVIDENCE_ONLY_SECTION = re.compile(r"project", re.I)

TAG_PATTERN = re.compile(r"</?([biu])>")


@dataclass
class Run:
    """A stretch of text sharing one style."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    link: str = ""
    size: float = 10.0
    font: str = "Times"
    color: tuple | None = None


@dataclass
class Line:
    index: int
    runs: list[Run] = field(default_factory=list)
    kind: str = "body"
    indent: float = 0.0
    bullet: str = ""
    right_text: str = ""
    space_before: float = 0.0
    page: int = 0
    section: str = ""
    entry: str = ""
    # Geometry kept only long enough to rejoin wrapped rows and to reproduce a
    # bullet's hanging indent.
    hanging: float = 0.0
    right_edge: float = 0.0
    height: float = 0.0
    first_word_width: float = 0.0
    all_bold: bool = False
    baseline: float = 0.0
    baseline_last: float = 0.0
    # Baseline distance from the row above, measured before any joining. The
    # render-time `space_before` is derived later and is zero at join time, so
    # the paragraph-gap test needs its own untouched copy.
    raw_gap: float = 0.0
    # The runs this line held before tailoring, so an edit can be undone.
    original: list | None = None
    # Whether the bullet glyph was typed into the text, as opposed to applied by
    # a list style. Writing a literal one back into a styled list paragraph shows
    # two bullets; leaving a typed one out loses it entirely.
    literal_bullet: bool = False
    # Selection state. A removed line stays in the parsed document - the master
    # file is never altered - but is not rendered and not counted as content.
    removed: bool = False
    # How strongly this line earns its space, 1 (essential) to 5. Used to trim
    # deterministically when the rendered pages come out over target, so getting
    # shorter never costs another model call.
    priority: int = 3

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE_KINDS and len(self.text.strip()) > 3

    def markup(self) -> str:
        """The line as tagged text, which is what the model sees and returns."""
        out = []
        for run in self.runs:
            text = run.text
            if not text:
                continue
            for flag, tag in ((run.bold, "b"), (run.italic, "i"), (run.underline, "u")):
                if flag:
                    text = f"<{tag}>{text}</{tag}>"
            out.append(text)
        return "".join(out)


def parse_markup(markup: str, template: list[Run]) -> list[Run]:
    """Turn tagged text back into runs, styled from the line it replaces.

    Each new run inherits from whichever original run carried the same weight
    and slant - that is what keeps "Programming Languages:" bold when the list
    after it changes. With no such run to copy, the first one is used, so font,
    size and colour still come from the document rather than from a default.
    """
    if not template:
        template = [Run("")]
    segments: list[tuple[str, bool, bool, bool]] = []
    bold = italic = underline = False
    position = 0
    for match in TAG_PATTERN.finditer(markup):
        chunk = markup[position:match.start()]
        if chunk:
            segments.append((chunk, bold, italic, underline))
        tag, closing = match.group(1), markup[match.start() + 1] == "/"
        value = not closing
        if tag == "b":
            bold = value
        elif tag == "i":
            italic = value
        else:
            underline = value
        position = match.end()
    tail = markup[position:]
    if tail or not segments:
        segments.append((tail, bold, italic, underline))

    runs = []
    for text, is_bold, is_italic, is_underline in segments:
        source = next(
            (r for r in template
             if r.bold == is_bold and r.italic == is_italic and r.underline == is_underline),
            template[0],
        )
        runs.append(Run(
            text=text, bold=is_bold, italic=is_italic, underline=is_underline,
            link=source.link, size=source.size, font=source.font, color=source.color,
        ))
    return runs


class MasterResume:
    """A parsed resume: its lines, and the ability to write them back out."""

    def __init__(self, path: Path, lines: list[Line], page_setup: dict, source: str):
        self.path = Path(path)
        self.lines = lines
        self.page_setup = page_setup
        self.source = source  # "docx" or "pdf"

    # -- what the model is shown -------------------------------------------- #

    def blocks(self) -> list[dict]:
        """The editable lines, with enough context to tailor them sensibly."""
        return [
            {
                "index": line.index,
                "kind": line.kind,
                "section": line.section,
                "entry": line.entry,
                "text": line.markup(),
                # The length this line has to stay within. A resume that fits two
                # pages is a layout decision the candidate already made; words
                # that push it to three have changed the layout, whatever the
                # instructions said.
                "max_chars": int(len(line.text) * LENGTH_ALLOWANCE) + 4,
            }
            for line in self.lines if line.editable
        ]

    @property
    def page_count(self) -> int:
        return max((line.page for line in self.lines), default=0) + 1

    def evidence(self) -> list[dict]:
        """Every line the selection pass may act on, in document order.

        Wider than `blocks()`: that lists only what may be re-worded, while this
        also offers whole entries, because dropping an irrelevant project is a
        selection decision and re-wording its title is not.
        """
        return [
            {
                "index": line.index,
                "kind": line.kind,
                "section": line.section,
                "entry": line.entry,
                "text": line.markup(),
                "max_chars": int(len(line.text) * LENGTH_ALLOWANCE) + 4,
                "rewritable": line.kind in EDITABLE_KINDS,
            }
            for line in self.lines
            if line.kind in SELECTABLE_KINDS and line.text.strip()
        ]

    @property
    def live(self) -> list[Line]:
        """The lines that survive selection, in order."""
        return [line for line in self.lines if not line.removed]

    def apply_plan(self, decisions: list[dict]) -> dict:
        """Carry out keep / compress / remove for each line the plan names.

        Unnamed lines are kept as they are, so a plan that forgets a line leaves
        the resume longer rather than accidentally emptying it. Entries take only
        keep or remove - their text is an employer, a title and dates.
        """
        by_index = {line.index: line for line in self.lines}
        counts = {"kept": 0, "compressed": 0, "removed": 0, "refused": 0}
        for decision in decisions:
            line = by_index.get(int(decision.get("index", -1)))
            action = str(decision.get("action") or "keep").casefold()
            if line is None or line.kind not in SELECTABLE_KINDS:
                counts["refused"] += 1
                continue
            line.priority = max(1, min(5, int(decision.get("priority") or 3)))
            if action == "remove":
                line.removed = True
                counts["removed"] += 1
                # An entry's bullets belong to that entry. Leaving them behind put
                # one employer's achievements under the next employer's heading.
                if line.kind == "entry":
                    counts["removed"] += self._remove_children(line.index)
                continue
            text = str(decision.get("text") or "").strip()
            if text and line.kind in EDITABLE_KINDS and text != line.markup():
                if line.original is None:
                    line.original = list(line.runs)
                line.runs = parse_markup(text, line.runs)
                counts["compressed"] += 1
            else:
                counts["kept"] += 1
        self.prune_empty_sections()
        self.resettle_spacing()
        return counts

    def _remove_children(self, index: int) -> int:
        """Remove the bullets and body lines belonging to one entry."""
        removed = 0
        for line in self.lines[index + 1:]:
            if line.kind in {"entry", "heading"}:
                break
            if not line.removed:
                line.removed = True
                removed += 1
        return removed

    def prune_empty_sections(self) -> None:
        """Drop a heading with nothing under it, and an entry with nothing left.

        A section title above blank space is the classic sign of a resume that was
        cut down by a machine, and an orphaned heading at a page break is worse.
        An entry keeps its place with no bullets on purpose - that is the compressed
        form that holds career context - but an entry inside a section that has been
        emptied goes with it.
        """
        # A project or publication left with no bullets under it is a title and
        # nothing else. An employment entry in the same state is different - the
        # employer and the dates are the career history - so only the sections
        # whose entries exist to carry evidence are cleared out.
        for position, line in enumerate(self.lines):
            if line.kind != "entry" or line.removed:
                continue
            if not EVIDENCE_ONLY_SECTION.search(line.section or ""):
                continue
            carries = False
            for following in self.lines[position + 1:]:
                if following.kind in {"entry", "heading"}:
                    break
                if not following.removed and following.text.strip():
                    carries = True
                    break
            line.removed = not carries

        for position, line in enumerate(self.lines):
            if line.kind != "heading" or line.removed:
                continue
            has_content = False
            for following in self.lines[position + 1:]:
                if following.kind == "heading":
                    break
                if not following.removed and following.text.strip():
                    has_content = True
                    break
            line.removed = not has_content

    def restore(self, index: int) -> bool:
        """Put one removed line back, and any heading or entry it needs to show."""
        by_index = {line.index: line for line in self.lines}
        line = by_index.get(index)
        if line is None:
            return False
        line.removed = False
        # A restored bullet with its entry or heading still removed would appear
        # under nothing, so its ancestors come back with it.
        for earlier in reversed(self.lines[:index]):
            if earlier.kind == "entry" and earlier.removed:
                earlier.removed = False
            if earlier.kind == "heading":
                earlier.removed = False
                break
        self.resettle_spacing()
        return True

    def restore_all(self) -> None:
        """Undo every selection decision, back to the master as parsed."""
        for line in self.lines:
            line.removed = False
            if line.original is not None:
                line.runs, line.original = line.original, None

    def trim_to_fit(self, drop: int, protect: set | None = None) -> list[int]:
        """Remove the `drop` least important surviving bullets. Returns their indices.

        Getting under the page target this way costs nothing: the plan already
        said how hard each line was working, so the weakest can go without asking
        the model again. Only bullets are eligible - never a heading, an entry, a
        contact line, or the last bullet an entry has left.
        """
        protect = protect or set()
        dropped: list[int] = []

        def bullets():
            return [
                line for line in self.lines
                if not line.removed and line.kind in {"bullet", "body"} and line.text.strip()
            ]

        # The priority the plan gave each entry, so a role the job actually turns
        # on cannot be reduced to a bare line while a weaker one keeps its bullets.
        entry_priority = {
            line.text.strip(): line.priority
            for line in self.lines if line.kind == "entry"
        }

        # Weakest bullets first. An entry may end with none - that is the
        # compressed form, an employer and dates kept for career context - but
        # only where the entry itself was not judged central to this application.
        # Protected lines are never touched: priority 1, the sole evidence for a
        # stated requirement, and the kinds of proof that take years to earn and
        # one line to delete.
        for line in sorted(bullets(), key=lambda item: (-item.priority, -item.index)):
            if len(dropped) >= drop:
                break
            if line.priority <= 1 or line.index in protect:
                continue
            siblings = sum(1 for other in bullets() if other.entry == line.entry)
            if siblings <= 1 and entry_priority.get(line.entry, 3) <= 2:
                continue  # the strongest roles keep at least one bullet
            line.removed = True
            dropped.append(line.index)

        # Still over: drop whole low-value entries that now carry nothing. A job
        # with no bullets left and no relevance to this posting is costing a line
        # for nothing; one the plan rated important keeps its place.
        if len(dropped) < drop:
            carrying = {line.entry for line in bullets()}
            for line in sorted(
                (item for item in self.lines
                 if not item.removed and item.kind == "entry" and item.priority >= 4),
                key=lambda item: (-item.priority, -item.index),
            ):
                if len(dropped) >= drop:
                    break
                if line.text.strip() in carrying or line.index in protect:
                    continue
                line.removed = True
                dropped.append(line.index)

        self.prune_empty_sections()
        self.resettle_spacing()
        return dropped

    def compress_skills(self, wanted: set[str], keep_at_least: int = 4) -> int:
        """Drop skills the posting never mentions. Returns how many terms went.

        This is where the space for a one-page resume should come from. A skills
        block is the cheapest content on the page - a list of nouns, no evidence
        attached - and on a long master resume it can run to a third of the page
        while the publication and the teaching section get deleted to fit.
        Removing a term cannot invent a claim, so unlike trimming a bullet this
        costs the candidate nothing but breadth they were not being read for.

        Each line keeps its first few terms whatever happens: the selection pass
        has already ordered them by relevance, and a category heading with two
        entries under it reads worse than one with five.
        """
        dropped = 0
        for line in self.live:
            if line.kind != "skills":
                continue
            listing = [run for run in line.runs if not run.bold and "," in run.text]
            if not listing:
                continue
            if line.original is None:
                line.original = list(line.runs)
            for run in listing:
                # Whatever separated this run from the next one has to survive.
                # Rebuilding the run without it welded the last surviving term to
                # the first word of the following run - "PyTorch, " plus "GANs"
                # came out as "PyTorchGANs".
                trailing = re.search(r"[,;\s]+$", run.text)
                suffix = trailing.group(0) if trailing else ""
                terms = [term.strip() for term in run.text.split(",")]
                kept = []
                for position, term in enumerate(terms):
                    bare = re.sub(r"[^\w+#\s]", "", term).casefold()
                    relevant = any(word in wanted for word in bare.split())
                    if position < keep_at_least or relevant or not bare:
                        kept.append(term)
                    else:
                        dropped += 1
                body = ", ".join(term for term in kept if term)
                if body.rstrip().endswith(".") and not suffix:
                    run.text = body
                else:
                    run.text = body.rstrip(" ,;") + suffix
        return dropped

    def resettle_spacing(self) -> None:
        """Repair the gaps above lines whose neighbour above them has gone.

        Every line's spacing was measured against the line that used to precede
        it. Remove that line and the gap is meaningless.

        The replacement is keyed on the pair of kinds - what now sits above, and
        what this line is - because that is what the gap actually encodes. A
        per-kind median is not enough: an entry following a bullet needs the
        space that separates two jobs, while an entry directly under its own
        heading needs almost none, and giving both the same figure cost this
        resume 160 points of white space, about twelve lines, which is more than
        the trimming was taking out.

        Lines whose predecessor survived keep their measured spacing untouched,
        so a resume with nothing removed renders exactly as it was parsed.
        """
        pairs: dict[tuple[str, str], list[float]] = {}
        previous = None
        for line in self.lines:
            if previous is not None:
                pairs.setdefault((previous.kind, line.kind), []).append(line.space_before)
            previous = line
        median = {
            key: sorted(gaps)[len(gaps) // 2] for key, gaps in pairs.items() if gaps
        }

        live_previous = None
        for position, line in enumerate(self.lines):
            if line.removed:
                continue
            original_previous = self.lines[position - 1] if position else None
            if live_previous is not None and original_previous is not live_previous:
                line.space_before = median.get(
                    (live_previous.kind, line.kind), line.space_before
                )
            live_previous = line

    def plain_text(self) -> str:
        """The whole document as text, for keyword scoring."""
        out = []
        for line in self.live:
            prefix = f"{line.bullet} " if line.bullet else ""
            tail = f"  {line.right_text}" if line.right_text else ""
            out.append(f"{prefix}{line.text}{tail}")
        return "\n".join(out)

    # -- applying the model's wording ---------------------------------------- #

    def apply(self, edits: dict) -> int:
        """Replace the wording of the given lines. Returns how many changed."""
        by_index = {line.index: line for line in self.lines}
        changed = 0
        for index, markup in edits.items():
            line = by_index.get(int(index))
            if line is None or not line.editable:
                continue
            new = str(markup or "").strip()
            if not new or new == line.markup():
                continue
            if line.original is None:
                line.original = list(line.runs)
            line.runs = parse_markup(new, line.runs)
            changed += 1
        return changed

    def revert(self, index: int) -> bool:
        """Put one line back exactly as it was before tailoring."""
        for line in self.lines:
            if line.index == index and line.original is not None:
                line.runs, line.original = line.original, None
                return True
        return False

    def growth(self) -> list[tuple[int, int]]:
        """(index, characters added) for every edited line, most grown first."""
        grown = [
            (line.index, len(line.text) - len("".join(r.text for r in line.original)))
            for line in self.lines if line.original is not None
        ]
        return sorted(grown, key=lambda item: item[1], reverse=True)

    def write(self, destination: Path, as_pdf: bool = False) -> Path:
        from app.services import layout_pdf, layout_docx

        destination = Path(destination)
        if self.source == "docx" and not as_pdf:
            return layout_docx.write(self, destination.with_suffix(".docx"))
        # Everything is renderable as a PDF, including a .docx master: the parsed
        # lines carry their own fonts, sizes and weights, so the PDF is drawn from
        # the same structure the .docx edit would have produced.
        return layout_pdf.write(self, destination.with_suffix(".pdf"))


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

def _font_flags(fontname: str) -> tuple[bool, bool]:
    lowered = (fontname or "").casefold()
    bold = "bold" in lowered or "black" in lowered or "heavy" in lowered
    italic = "italic" in lowered or "oblique" in lowered
    return bold, italic


def _family(fontname: str) -> str:
    """The font family, stripped of the subset prefix PDFs put on embedded fonts."""
    name = re.sub(r"^[A-Z]{6}\+", "", fontname or "")
    name = re.split(r"[-,]", name)[0]
    name = re.sub(r"(MT|PS|PSMT|Regular|Bold|Italic)$", "", name)
    return name or "Times"


# How far above or below a character's baseline a drawn rule still counts as its
# underline. It has to allow a little negative: a PDF's reported glyph bottom
# includes the font's descender space, so an underline drawn tight to the text
# sits fractionally *above* it. Requiring a non-negative gap missed every section
# heading's rule.
UNDERLINE_ABOVE = -2.5
UNDERLINE_BELOW = 5.0


def _underlined(char: dict, rules: list[dict]) -> bool:
    """Whether a drawn rule sits directly under this character."""
    for rule in rules:
        if (rule["x0"] - 1 <= char["x0"] and char["x1"] <= rule["x1"] + 1
                and UNDERLINE_ABOVE <= rule["top"] - char["bottom"] <= UNDERLINE_BELOW):
            return True
    return False


def _link_at(char: dict, links: list[dict]) -> str:
    for link in links:
        if (link["x0"] - 1 <= char["x0"] and char["x1"] <= link["x1"] + 1
                and link["top"] - 2 <= char["top"] and char["bottom"] <= link["bottom"] + 2):
            return link.get("uri") or ""
    return ""


def _parse_pdf(path: Path) -> tuple[list[Line], dict]:
    import pdfplumber

    lines: list[Line] = []
    setup = {}
    with pdfplumber.open(str(path)) as pdf:
        first = pdf.pages[0]
        setup = {"width": float(first.width), "height": float(first.height)}
        left_edges, right_edges, page_lines = [], [], []

        for page_number, page in enumerate(pdf.pages):
            rules = [
                {"x0": item["x0"], "x1": item["x1"], "top": item["top"]}
                for item in list(page.lines) + list(page.rects)
                if abs(item.get("height", 0)) <= 2 and item["x1"] - item["x0"] > 4
            ]
            links = list(page.hyperlinks)

            for chars in _rows(page.chars):
                page_lines.append((page_number, chars, rules, links))
                left_edges.append(chars[0]["x0"])
                right_edges.append(chars[-1]["x1"])

        margin_left = min(left_edges) if left_edges else 54.0
        margin_right = max(right_edges) if right_edges else setup["width"] - 54.0
        setup["margin_left"] = margin_left
        setup["margin_right"] = margin_right
        # Where the first baseline actually sits, so the renderer can start the
        # text at the same height rather than at a guessed margin.
        setup["first_baseline"] = page_lines[0][1][0]["bottom"] if page_lines else 47.0

        previous_bottom, previous_page, deltas = None, 0, []
        for page_number, chars, rules, links in page_lines:
            line = _line_from_chars(
                len(lines), chars, rules, links, margin_left, margin_right
            )
            line.page = page_number
            line.right_edge = chars[-1]["x1"]
            line.height = chars[0]["bottom"] - chars[0]["top"]
            line.first_word_width = _first_word_width(chars)
            line.all_bold = all(_font_flags(c["fontname"])[0] for c in chars if c["text"].strip())
            line.baseline = line.baseline_last = chars[0]["bottom"]
            if previous_bottom is not None and page_number == previous_page:
                line.raw_gap = line.baseline - previous_bottom
                deltas.append(line.raw_gap)
            previous_bottom, previous_page = line.baseline, page_number
            lines.append(line)

        body_size = _common_size(lines)
        setup["leading"] = _common_leading(deltas, body_size)

    lines = _join_wrapped(lines, margin_right, setup["leading"])
    _settle_hanging_indents(lines)
    _settle_spacing(lines, setup["leading"])
    return lines, setup


def _common_size(lines: list[Line]) -> float:
    sizes = [round(run.size, 1) for line in lines for run in line.runs if run.text.strip()]
    return max(set(sizes), key=sizes.count) if sizes else 10.0


def _common_leading(deltas: list[float], body_size: float) -> float:
    """The document's own baseline-to-baseline distance for ordinary text."""
    inside = sorted(d for d in deltas if body_size * 0.8 <= d <= body_size * 1.9)
    if not inside:
        return body_size * 1.16
    return inside[len(inside) // 2]


def _settle_spacing(lines: list[Line], leading: float) -> None:
    """Turn measured row positions into the gap *in addition to* normal leading.

    Renderers stack paragraphs at one leading apart on their own, so the space
    recorded here has to be what the original document had beyond that. Storing
    the raw gap instead double-counts the leading on every line and pushes the
    resume onto an extra page.
    """
    previous = None
    for line in lines:
        if previous is not None and previous.page == line.page:
            line.space_before = max(0.0, line.baseline - previous.baseline_last - leading)
        else:
            line.space_before = 0.0
        previous = line


def _settle_hanging_indents(lines: list[Line]) -> None:
    """Give every bullet the same hanging indent the wrapped ones revealed.

    A bullet short enough not to wrap has no continuation row to measure, so it
    would otherwise render with no hang at all and sit out of line with its
    neighbours. The document's own measurement is used where there is one.
    """
    measured = [line.hanging for line in lines if line.bullet and line.hanging > 0]
    if not measured:
        return
    common = max(set(measured), key=measured.count)
    for line in lines:
        if line.bullet and line.hanging <= 0:
            line.hanging = common


# Characters whose baselines sit within this many points are on the same row.
ROW_TOLERANCE = 2.5


def _rows(chars: list[dict]) -> list[list[dict]]:
    """Group characters into rows by shared baseline.

    Bucketing on `top` divided by a constant, which this used to do, puts a hard
    boundary in an arbitrary place: a bullet glyph sitting a fraction of a point
    higher than its own sentence lands in the row above and is stranded there.
    Clustering on the baseline instead keeps mixed sizes on one row - which is
    what a baseline is for - and a run of leader characters cannot split a line.
    """
    usable = [char for char in chars if char["text"].strip()]
    if not usable:
        return []
    rows: list[list[dict]] = []
    for char in sorted(usable, key=lambda c: (round(c["bottom"], 1), c["x0"])):
        if rows and abs(char["bottom"] - rows[-1][0]["bottom"]) <= ROW_TOLERANCE:
            rows[-1].append(char)
        else:
            rows.append([char])
    return [sorted(row, key=lambda c: c["x0"]) for row in rows]


def _first_word_width(chars: list[dict]) -> float:
    width = 0.0
    for char in chars:
        if char["text"].isspace():
            break
        width += char["x1"] - char["x0"]
    return width


def _join_wrapped(lines: list[Line], margin_right: float, leading: float) -> list[Line]:
    """Rejoin the visual lines a PDF broke a sentence across.

    A PDF stores no paragraphs, only glyphs on rows, so one bullet arrives as
    two or three rows. Editing those rows separately is meaningless: change the
    wording and the wrap point moves, leaving the second row's text stranded
    mid-sentence. So they are joined back into the sentence the writer typed,
    and the renderer wraps them again wherever the new words happen to break.

    A row is a continuation when the row above it was full - that is, when the
    next word would not have fitted on it. Comparing against the margin alone is
    not enough: a line ending one long word short of the margin still wrapped.
    """
    joined: list[Line] = []
    for line in lines:
        previous = joined[-1] if joined else None
        if previous is not None and _continues(previous, line, margin_right, leading):
            if previous.runs and line.runs:
                previous.runs[-1].text = previous.runs[-1].text.rstrip() + " "
            previous.runs.extend(line.runs)
            previous.right_edge = line.right_edge
            previous.first_word_width = line.first_word_width
            previous.baseline_last = line.baseline
            # The deeper indent of a continuation row is the hanging indent, which
            # is what keeps wrapped bullet text clear of its own glyph.
            previous.hanging = max(previous.hanging, line.indent - previous.indent)
            continue
        line.index = len(joined)
        joined.append(line)
    return joined


def _continues(previous: Line, line: Line, margin_right: float, leading: float) -> bool:
    if line.bullet or line.right_text or previous.right_text:
        return False
    if line.all_bold or previous.page != line.page:
        return False
    if line.raw_gap > leading * PARAGRAPH_GAP:
        return False  # a real paragraph gap, not a wrap
    if not previous.runs or not line.runs:
        return False
    # Different type size means a different kind of line - the name above the
    # contact details, a heading above its first sentence. Never a wrap.
    if abs(_line_size(previous) - _line_size(line)) > 0.6:
        return False
    # A row opening in bold where the row above ended in ordinary text is a new
    # labelled item, not a wrap: this is exactly the shape of a skills section,
    # "Programming Languages: ..." then "AI/ML & Generative AI: ...". Both rows
    # run the full width, so width alone cannot tell them apart. A wrap that
    # happens to fall inside a bold phrase is still allowed, because there the
    # row above ends in bold too.
    if _starts_bold(line) and not _ends_bold(previous):
        return False
    # Would the first word of this row have fitted on the previous one?
    space = max(2.0, previous.height * 0.25)
    return previous.right_edge + space + line.first_word_width > margin_right


def _line_size(line: Line) -> float:
    return max((run.size for run in line.runs if run.text.strip()), default=10.0)


def _starts_bold(line: Line) -> bool:
    run = next((r for r in line.runs if r.text.strip()), None)
    return bool(run and run.bold)


def _ends_bold(line: Line) -> bool:
    run = next((r for r in reversed(line.runs) if r.text.strip()), None)
    return bool(run and run.bold)


def _split_right_tail(chars: list[dict], margin_right: float) -> tuple[list[dict], str]:
    """Separate a right-aligned dates column from the text on its left.

    Split on the characters, before any runs are built. Doing it afterwards by
    counting characters was off by however many spaces the run builder had
    reconstructed from the geometry, which chopped entry lines mid-word -
    "Research Assistant, New York University, New Yo".
    """
    for position in range(len(chars) - 1, 0, -1):
        gap = chars[position]["x0"] - chars[position - 1]["x1"]
        if gap >= RIGHT_TAIL_GAP and chars[-1]["x1"] >= margin_right - RIGHT_TAIL_MARGIN:
            tail = "".join(c["text"] for c in chars[position:]).strip()
            if tail:
                return chars[:position], tail
            break
    return chars, ""


def _line_from_chars(index, chars, rules, links, margin_left, margin_right) -> Line:
    left = chars[0]["x0"]
    chars, right_text = _split_right_tail(chars, margin_right)
    runs: list[Run] = []
    previous = None
    for char in chars:
        bold, italic = _font_flags(char["fontname"])
        style = (
            bold, italic, _underlined(char, rules), _link_at(char, links),
            round(char["size"], 1), _family(char["fontname"]),
            tuple(char.get("non_stroking_color") or ()) or None,
        )
        # A space wide enough to be a real gap is rebuilt from the geometry; PDF
        # text has no spaces of its own.
        if previous is not None and char["x0"] - previous["x1"] > previous["size"] * 0.17:
            if runs:
                runs[-1].text += " "
        if runs and style == (
            runs[-1].bold, runs[-1].italic, runs[-1].underline, runs[-1].link,
            runs[-1].size, runs[-1].font, runs[-1].color,
        ):
            runs[-1].text += char["text"]
        else:
            runs.append(Run(
                text=char["text"], bold=style[0], italic=style[1], underline=style[2],
                link=style[3], size=style[4], font=style[5], color=style[6],
            ))
        previous = char

    if runs:
        runs[-1].text = runs[-1].text.rstrip()
    line = Line(index=index, runs=runs, right_text=right_text)
    line.indent = round((left - margin_left) / INDENT_ROUNDING) * INDENT_ROUNDING
    _detach_bullet(line)
    return line


def _detach_bullet(line: Line) -> None:
    """Take the bullet glyph out of the text so wording edits cannot lose it."""
    if not line.runs:
        return
    stripped = line.runs[0].text.lstrip()
    if stripped and stripped[0] in BULLET_GLYPHS and len(line.text.strip()) > 2:
        rest = stripped[1:].lstrip()
        # A lone hyphen starting a sentence is not a bullet; a hyphen followed by
        # a space at the start of an indented line is.
        if stripped[0] not in "-–" or line.indent > 0:
            line.bullet = stripped[0]
            line.literal_bullet = True
            line.runs[0].text = rest


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #

def _parse_docx(path: Path) -> tuple[list[Line], dict]:
    import docx

    document = docx.Document(str(path))
    lines: list[Line] = []
    # Paragraphs only. A resume laid out in a table is read for its text elsewhere
    # (see docx_text), but editing one in place needs paragraph objects to write
    # back into, so a table-based master is reported rather than half-handled.
    if not document.paragraphs and document.tables:
        raise ValueError(
            "This .docx lays its content out in tables, which cannot be edited in "
            "place. Export it to PDF and use that as your master resume instead."
        )
    for paragraph in document.paragraphs:
        runs = []
        for run in paragraph.runs:
            if not run.text:
                continue
            runs.append(Run(
                text=run.text,
                bold=bool(run.bold),
                italic=bool(run.italic),
                underline=bool(run.underline),
                size=(run.font.size.pt if run.font.size else 10.0),
                font=run.font.name or "Times",
            ))
        line = Line(index=len(lines), runs=runs)
        line.indent = float(paragraph.paragraph_format.left_indent.pt
                            if paragraph.paragraph_format.left_indent else 0.0)
        style = (paragraph.style.name or "").casefold()
        if "list" in style or paragraph._p.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr"
        ) is not None:
            line.bullet = "•"
        _detach_bullet(line)
        lines.append(line)
    return lines, {"docx": True}


# --------------------------------------------------------------------------- #
# Classification - the same rules whatever the file was
# --------------------------------------------------------------------------- #

# Matched against the WHOLE line, not its opening words. A prefix match reads
# "Research Assistant, New York University" as the section heading "Research",
# which then swallows the employer and the job title into a structural label and
# hides the real heading above it.
HEADING_WORDS = re.compile(
    r"\s*(education|work\s+experience|"
    r"professional(\s*[&]|\s+and)?\s*(research\s+)?experience|experience|"
    r"employment(\s+history)?|technical\s+skills|skills|core\s+competenc\w*|"
    r"projects?|publications?|research(\s+experience)?|teaching(\s+experience)?|"
    r"certifications?|licen[cs]es?|awards?(\s*([&]|and)\s*honou?rs)?|honou?rs|"
    r"activities|leadership|summary|professional\s+summary|objective|profile|"
    r"(relevant\s+)?coursework|interests|languages|volunteer(\s+experience)?|"
    r"extracurriculars?|achievements|patents|presentations|references)\s*:?\s*$",
    re.I,
)

# A section heading is a label, not a sentence. Anything longer than this, or
# carrying a comma or a date, is an entry.
HEADING_MAX_CHARS = 46

SKILLS_HEADING = re.compile(r"skill|technolog|competenc", re.I)


def classify(lines: list[Line]) -> None:
    """Label every line, then hang section and entry context off it."""
    sizes = [run.size for line in lines for run in line.runs if run.text.strip()]
    body_size = max(set(sizes), key=sizes.count) if sizes else 10.0
    base_indent = min((line.indent for line in lines), default=0.0)

    for position, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            line.kind = "blank"
            continue
        bold = all(run.bold for run in line.runs if run.text.strip())
        underlined = any(run.underline for run in line.runs if run.text.strip())
        size = max((run.size for run in line.runs if run.text.strip()), default=body_size)

        if position == 0 and size > body_size + 1.5:
            line.kind = "name"
        elif position <= 2 and re.search(r"@|\|\||https?://|linkedin|github", text, re.I):
            line.kind = "contact"
        elif line.bullet:
            line.kind = "bullet"
        elif (
            (bold or underlined)
            and abs(line.indent - base_indent) < INDENT_ROUNDING * 1.5
            and not line.right_text
            and "," not in text
            and len(text) <= HEADING_MAX_CHARS
            and (underlined or text.isupper() or HEADING_WORDS.fullmatch(text))
        ):
            line.kind = "heading"
        elif bold or line.right_text:
            # An employer, a job title, a degree, a project name - a fact of
            # record with its dates beside it. Never rewritten.
            line.kind = "entry"
        else:
            line.kind = "body"

    section, entry = "", ""
    seen_heading = False
    for line in lines:
        if line.kind == "heading":
            section, entry, seen_heading = line.text.strip(), "", True
            continue
        if line.kind == "entry":
            entry = line.text.strip()
        line.section, line.entry = section, entry
        if line.kind == "body":
            if not seen_heading:
                line.kind = "summary"
            elif SKILLS_HEADING.search(section):
                line.kind = "skills"
        elif line.kind == "bullet" and SKILLS_HEADING.search(section):
            line.kind = "skills"


def from_profile(profile: dict) -> MasterResume:
    """A document built from the candidate profile, for candidates with no master.

    This exists so there is one generation pipeline rather than two. The profile
    path used to regenerate a resume wholesale from structured data, keeping
    everything by default and adopting whatever keywords a posting asked for -
    none of the selection, grounding or layout checks the master path had. Two
    pipelines meant two sets of rules, and only one of them was being maintained.

    The lines are laid out the way the renderer expects, so everything downstream
    - selection, trimming, verification, rendering - is the same code.
    """
    from app.services.profile_merge import evidenced_profile

    profile = evidenced_profile(profile)
    lines: list[Line] = []
    size, heading_size = 10.0, 10.0

    def add(text_runs, kind, indent=0.0, bullet="", right="", gap=0.0):
        lines.append(Line(
            index=len(lines), runs=list(text_runs), kind=kind, indent=indent,
            bullet=bullet, right_text=right, space_before=gap, literal_bullet=bool(bullet),
        ))

    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    add([Run(name or "Candidate", bold=True, size=15.0)], "name")
    contact = " | ".join(
        str(profile[key]) for key in ("email", "phone", "location") if profile.get(key)
    )
    links = " | ".join(
        str(profile[key]) for key in ("linkedin", "github", "website", "publications_url")
        if profile.get(key)
    )
    if contact:
        add([Run(contact, size=size)], "contact")
    if links:
        add([Run(links, size=size)], "contact")
    if profile.get("summary"):
        add([Run(str(profile["summary"]), size=size)], "summary", gap=8.0)

    if profile.get("skills"):
        add([Run("SKILLS", bold=True, underline=True, size=heading_size)], "heading", gap=10.0)
        add([Run("Skills: ", bold=True, size=size),
             Run(", ".join(str(s) for s in profile["skills"]), size=size)], "skills", indent=9.0)

    titles = {
        "experience": "EXPERIENCE", "projects": "PROJECTS",
        "education": "EDUCATION", "publications": "PUBLICATIONS",
    }
    for section, title in titles.items():
        entries = [e for e in (profile.get(section) or []) if isinstance(e, dict)]
        if not entries:
            continue
        add([Run(title, bold=True, underline=True, size=heading_size)], "heading", gap=10.0)
        for entry in entries:
            head = entry.get("heading") or ", ".join(
                x for x in (entry.get("degree"), entry.get("major")) if x
            )
            label = " | ".join(
                str(x) for x in (head, entry.get("organization"), entry.get("location")) if x
            )
            add([Run(label or "Entry", bold=True, size=size)], "entry", indent=9.0,
                right=format_dates(entry), gap=6.0)
            if entry.get("gpa"):
                add([Run(f"GPA: {entry['gpa']}", size=size)], "body", indent=18.0)
            for bullet in entry.get("bullets") or []:
                if str(bullet).strip():
                    add([Run(str(bullet).strip(), size=size)], "bullet",
                        indent=18.0, bullet="-")

    classify(lines)
    setup = {"width": 612.0, "height": 792.0, "margin_left": 54.0,
             "margin_right": 558.0, "first_baseline": 60.0, "leading": 13.2}
    return MasterResume(Path("profile"), lines, setup, "profile")


def load(path, original_name: str = "") -> MasterResume:
    """Read a master resume from disk."""
    path = Path(path)
    suffix = (Path(original_name or path).suffix or "").casefold()
    if suffix == ".docx":
        lines, setup = _parse_docx(path)
        source = "docx"
    elif suffix == ".pdf":
        lines, setup = _parse_pdf(path)
        source = "pdf"
    else:
        raise ValueError(
            "A master resume has to be a PDF or a DOCX - those are the only "
            "formats that carry the styling this has to preserve."
        )
    if not lines:
        raise ValueError(
            "No text could be read from that resume. If it is a scanned image, "
            "export a text-based file: an ATS cannot read it either."
        )
    classify(lines)
    return MasterResume(path, lines, setup, source)


class SourceDocument:
    """Where a tailored resume starts from, whichever kind of source that is.

    One pipeline needs one way of naming its input. A marked master resume and a
    candidate profile are opened differently and produce the same object, so
    nothing downstream has to know which it got.
    """

    def __init__(self, label: str, opener, path: str = ""):
        self.label = label
        self._open = opener
        self.file_path = path

    @classmethod
    def from_library(cls, document) -> "SourceDocument":
        from app.db.database import BACKEND_DIR

        def opener():
            return load(
                (BACKEND_DIR / document.file_path).resolve(), document.original_filename
            )

        return cls(document.label, opener, document.file_path)

    # The identity written into a package's fingerprint. A fixed token, not the
    # display label, so the staleness check derives the same string whether it is
    # holding a SourceDocument or nothing at all.
    PROFILE_IDENTITY = "profile"

    @classmethod
    def from_profile_record(cls, profile: dict) -> "SourceDocument":
        return cls("Your profile", lambda: from_profile(profile),
                   path=cls.PROFILE_IDENTITY)

    def open(self) -> MasterResume:
        return self._open()
