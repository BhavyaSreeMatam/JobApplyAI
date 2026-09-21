"""Draw a parsed resume back out as a PDF that looks like the one it came from.

A PDF holds positioned glyphs, not paragraphs, so a sentence rewritten to a
different width cannot be patched into the original object - the page has to be
drawn again. Everything the parser measured is carried across: font family,
size, weight, slant, underline, colour, indent, bullet glyph, the right-aligned
dates column, hyperlinks and page breaks.

The font matters more than anything else here, because a substituted typeface is
the one difference a person notices immediately. Real TrueType files are loaded
from the system font directory when they exist - on Windows that means the actual
Times New Roman, Arial or Calibri the resume was written in - and only if none is
found does this fall back to the PDF core fonts.

Registering real fonts has a second benefit worth stating: the bullet glyphs
U+2022 and U+25CF extract as "(cid:127)" from the core fonts, which hands an ATS
a broken character at the start of every achievement. With an embedded TrueType
face they extract as themselves, so the original bullet survives. When no system
font is available the bullet falls back to a hyphen, which is the only marker the
core fonts encode cleanly.
"""
import re
from html import escape
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIRECTORIES = [
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
]

# family -> (regular, bold, italic, bold-italic) file names.
FONT_FILES = {
    "times": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "couriernew": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
}

# What a resume's font name maps onto when that exact family is not installed.
FAMILY_ALIASES = {
    "timesnewroman": "times", "times": "times", "timesnewromanps": "times",
    "liberationserif": "times", "nimbusroman": "times", "serif": "times",
    "arial": "arial", "helvetica": "arial", "liberationsans": "arial",
    "arialnarrow": "arial", "sans": "arial", "verdana": "arial", "tahoma": "arial",
    "calibri": "calibri", "carlito": "calibri",
    "georgia": "georgia", "cambria": "cambria",
    "couriernew": "couriernew", "courier": "couriernew", "consolas": "couriernew",
}

CORE_FALLBACK = {
    "times": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "arial": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "calibri": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "georgia": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "cambria": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "couriernew": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}

_REGISTERED: dict[str, tuple[str, str, str, str] | None] = {}


def _find_font_file(name: str) -> Path | None:
    for directory in FONT_DIRECTORIES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def register_family(family: str) -> tuple[tuple[str, str, str, str], bool]:
    """(regular, bold, italic, bold-italic) PDF font names, and whether they are real.

    `False` means the core fonts are in use, which is the signal that the bullet
    glyph has to degrade to a hyphen.
    """
    key = FAMILY_ALIASES.get(re.sub(r"[^a-z]", "", (family or "").casefold()), "times")
    if key in _REGISTERED:
        names = _REGISTERED[key]
        return (names, True) if names else (CORE_FALLBACK[key], False)

    files = FONT_FILES.get(key)
    resolved = []
    for suffix, filename in zip(("", "-Bold", "-Italic", "-BoldItalic"), files or ()):
        found = _find_font_file(filename)
        if found is None:
            resolved = []
            break
        registered = f"{key}{suffix}"
        try:
            if registered not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(registered, str(found)))
            resolved.append(registered)
        except Exception:
            resolved = []
            break

    if len(resolved) == 4:
        names = (resolved[0], resolved[1], resolved[2], resolved[3])
        _REGISTERED[key] = names
        try:
            pdfmetrics.registerFontFamily(
                key, normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3]
            )
        except Exception:
            pass
        return names, True

    _REGISTERED[key] = None
    return CORE_FALLBACK[key], False


def _font_for(run, family_names) -> str:
    regular, bold, italic, bold_italic = family_names
    if run.bold and run.italic:
        return bold_italic
    if run.bold:
        return bold
    if run.italic:
        return italic
    return regular


def _color(run):
    value = run.color
    if not value:
        return black
    try:
        if len(value) == 1:
            return Color(value[0], value[0], value[0])
        if len(value) >= 3:
            return Color(value[0], value[1], value[2])
    except Exception:
        pass
    return black


def _markup(line, family_names) -> str:
    """The line as ReportLab inline markup, one <font> span per run."""
    parts = []
    for run in line.runs:
        if not run.text:
            continue
        text = escape(run.text)
        colour = _color(run)
        span = (f'<font name="{_font_for(run, family_names)}" size="{run.size:.1f}" '
                f'color="#{int(colour.red * 255):02x}{int(colour.green * 255):02x}'
                f'{int(colour.blue * 255):02x}">{text}</font>')
        if run.underline:
            span = f"<u>{span}</u>"
        if run.link:
            span = f'<link href="{escape(run.link, quote=True)}">{span}</link>'
        parts.append(span)
    return "".join(parts) or "&nbsp;"


def _top_margin(setup, resume, base_leading: float, body_size: float) -> float:
    """Start the first baseline where the original document started it.

    A fixed margin plus the first line's leading lands the text lower than the
    original, and on a resume already filling its pages that loses four or five
    lines off the bottom of page one.
    """
    first = resume.live[0] if resume.live else None
    size = max((r.size for r in first.runs if r.text.strip()), default=body_size) if first else body_size
    leading = base_leading * (size / body_size) if body_size else size * 1.16
    return max(18.0, float(setup.get("first_baseline", 47.0)) - leading)


def _body_size(lines) -> float:
    sizes = [round(run.size, 1) for line in lines for run in line.runs if run.text.strip()]
    return max(set(sizes), key=sizes.count) if sizes else 10.0


def _base_family(lines) -> str:
    counts: dict[str, int] = {}
    for line in lines:
        for run in line.runs:
            if run.text.strip():
                counts[run.font] = counts.get(run.font, 0) + len(run.text)
    return max(counts, key=counts.get) if counts else "Times"


def write(resume, destination: Path) -> Path:
    """Render a MasterResume to `destination`. Returns the path written."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    setup = resume.page_setup or {}
    width = setup.get("width") or LETTER[0]
    height = setup.get("height") or LETTER[1]
    left = setup.get("margin_left", 54.0)
    right_margin = width - setup.get("margin_right", width - 54.0)
    family_names, real_fonts = register_family(_base_family(resume.lines))
    body_size = _body_size(resume.lines)
    base_leading = setup.get("leading") or body_size * 1.16

    document = SimpleDocTemplate(
        str(destination),
        pagesize=(width, height),
        leftMargin=left,
        rightMargin=max(18.0, right_margin),
        topMargin=_top_margin(setup, resume, base_leading, body_size),
        bottomMargin=setup.get("margin_bottom", 30.0),
        title=destination.stem,
        author="",
    )
    usable = document.width

    story: list = []
    kinds: list[str] = []
    # `live` is the selection's output: removed lines stay in the parsed document
    # so a decision can be undone, but they must never reach the page.
    for line in resume.live:
        if line.kind == "blank" and not line.text.strip():
            continue

        size = max((run.size for run in line.runs if run.text.strip()), default=10.0)
        # The document's own measured leading, scaled for lines that are not at
        # the body size. Guessing a ratio instead re-flows the whole resume.
        leading = base_leading * (size / body_size) if body_size else size * 1.16
        indent = max(0.0, float(line.indent))
        style = ParagraphStyle(
            f"line{line.index}",
            fontName=family_names[0],
            fontSize=size,
            leading=leading,
            alignment=TA_LEFT,
            leftIndent=indent,
            spaceBefore=min(line.space_before, size * 1.2),
            spaceAfter=0,
        )
        markup = _markup(line, family_names)

        if line.bullet:
            glyph = line.bullet if real_fonts else "-"
            style.firstLineIndent = -(size * 0.9)
            style.leftIndent = indent + size * 0.9
            markup = (f'<font name="{family_names[0]}" size="{size:.1f}">{escape(glyph)} '
                      f"</font>{markup}")

        if line.kind == "name" or (line.kind == "contact" and _looks_centred(line, resume)):
            style.alignment = 1  # TA_CENTER
            style.leftIndent = 0

        if line.right_text:
            # Two borderless cells keep the dates on the right without a tab
            # stop, which ReportLab paragraphs do not have. Text extracts as
            # left cell then right cell, in reading order.
            tail_style = ParagraphStyle(
                f"tail{line.index}", parent=style, alignment=2, leftIndent=0,
                firstLineIndent=0, spaceBefore=0,
            )
            tail_width = min(150.0, max(60.0, len(line.right_text) * size * 0.55))
            table = Table(
                [[Paragraph(markup, style),
                  Paragraph(f'<font name="{family_names[0]}" size="{size:.1f}">'
                            f"{escape(line.right_text)}</font>", tail_style)]],
                colWidths=[usable - tail_width, tail_width],
                style=TableStyle([
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]),
            )
            if style.spaceBefore:
                story.append(Spacer(1, style.spaceBefore))
                kinds.append("spacer")
            story.append(table)
            kinds.append(line.kind)
        else:
            story.append(Paragraph(markup, style))
            kinds.append(line.kind)

    document.build(_hold_headings_with_content(story, kinds))
    return destination


# A heading or an entry stranded at the foot of a page, with the thing it
# introduces overleaf, is the most obvious sign of a document that was cut to
# length by a machine. Each one is bound to what follows it so the page break
# moves instead.
LEADING_KINDS = {"heading", "entry"}


def _hold_headings_with_content(story: list, kinds: list[str]) -> list:
    held: list = []
    index = 0
    while index < len(story):
        if kinds[index] in LEADING_KINDS and index + 1 < len(story):
            group = [story[index], story[index + 1]]
            following = index + 2
            # A heading introduces an entry which introduces a bullet; keep all
            # three together rather than pushing the orphan down one place.
            if kinds[index] == "heading" and kinds[index + 1] == "entry" \
                    and following < len(story):
                group.append(story[following])
                following += 1
            held.append(KeepTogether(group))
            index = following
            continue
        held.append(story[index])
        index += 1
    return held


def _looks_centred(line, resume) -> bool:
    """A contact line sitting away from the left margin was centred."""
    return line.indent > 12.0 or line.kind == "contact"
