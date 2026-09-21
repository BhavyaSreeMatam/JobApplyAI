"""Reading every word out of a .docx, including the words inside tables.

python-docx exposes `document.paragraphs` and `document.tables` as two separate
collections, and reading only the first is an easy mistake that costs a lot: a
resume laid out as a two-column table - which is a common template, and the usual
way people put dates on the right - returns almost nothing. The candidate sees
"could not read enough text from that file" for a document full of text, or worse,
an import that silently drops the half of their history that lived in a table.

Paragraphs and tables are interleaved in the document body, so they are walked in
document order rather than concatenated collection by collection. Order matters
for a resume: a heading has to arrive before the entries under it.
"""


def _iter_block_items(parent):
    """Paragraphs and tables of one container, in the order they appear."""
    from docx.document import Document as _Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import _Cell, Table
    from docx.text.paragraph import Paragraph

    if isinstance(parent, _Document):
        element = parent.element.body
    elif isinstance(parent, _Cell):
        element = parent._tc
    else:
        return

    for child in element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _table_lines(table) -> list[str]:
    """One line per row, cells separated. Nested tables are followed."""
    lines = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            parts = []
            for item in _iter_block_items(cell):
                if hasattr(item, "rows"):
                    parts.extend(_table_lines(item))
                else:
                    text = (item.text or "").strip()
                    if text:
                        parts.append(text)
            if parts:
                cells.append(" ".join(parts))
        if cells:
            # Tabs rather than spaces: a two-column layout is usually content on
            # the left and a date on the right, and keeping them apart lets the
            # parser see them as two fields.
            lines.append("\t".join(cells))
    return lines


def extract(path) -> str:
    """All readable text from a .docx, paragraphs and tables alike."""
    import docx

    document = docx.Document(str(path))
    lines: list[str] = []
    for item in _iter_block_items(document):
        if hasattr(item, "rows"):
            lines.extend(_table_lines(item))
        else:
            text = (item.text or "").strip()
            if text:
                lines.append(text)

    # Headers and footers hold contact details often enough to be worth reading,
    # and never hold anything that would confuse a parser.
    for section in document.sections:
        for container in (section.header, section.footer):
            try:
                for paragraph in container.paragraphs:
                    text = (paragraph.text or "").strip()
                    if text and text not in lines:
                        lines.append(text)
            except Exception:
                continue

    return "\n".join(lines)
