"""Write a parsed resume back into its own .docx, changing only the words.

This is the format where the promise is absolute. A Word document already stores
paragraphs and runs, so a rewritten sentence goes back into the same paragraph
with the same style, and everything the parser never touched - margins, tabs,
tables, headers, section breaks, theme fonts, spacing - is still the original
XML. Nothing is redrawn.
"""
from pathlib import Path


def _apply_run_style(target, source) -> None:
    """Copy the character formatting of the run this text replaces."""
    target.bold = source.bold
    target.italic = source.italic
    target.underline = source.underline
    if source.font:
        target.font.name = source.font
    if source.size:
        from docx.shared import Pt

        target.font.size = Pt(source.size)


def _delete_paragraph(paragraph) -> None:
    element = paragraph._element
    element.getparent().remove(element)


def write(resume, destination: Path) -> Path:
    import docx

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    document = docx.Document(str(resume.path))
    paragraphs = document.paragraphs
    for line in resume.lines:
        if line.index >= len(paragraphs):
            continue
        paragraph = paragraphs[line.index]
        if line.removed:
            # Emptied rather than deleted: removing the paragraph would renumber
            # every later one and break the index the rest of this loop relies on.
            _delete_paragraph(paragraph)
            continue
        if not paragraph.runs:
            continue
        original = list(paragraph.runs)
        # Re-use the existing run objects where possible: keeping them means
        # keeping every property python-docx does not model - highlighting,
        # character styles, language, spell-check state.
        for position, run in enumerate(line.runs):
            text = run.text
            if position == 0 and line.bullet and line.literal_bullet:
                text = f"{line.bullet} {text}"
            if position < len(original):
                original[position].text = text
                _apply_run_style(original[position], run)
            else:
                added = paragraph.add_run(text)
                _apply_run_style(added, run)
        for leftover in original[len(line.runs):]:
            leftover.text = ""

    document.save(str(destination))
    return destination

