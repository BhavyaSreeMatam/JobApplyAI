"""Keep imported text as text when exporting to Excel."""
from urllib.parse import urlsplit


def protect_text_cells(workbook) -> None:
    """Do not let a posting or candidate field become an Excel formula.

    These exports contain values only; no application-generated formulas need
    to be retained. Force string cells explicitly instead of changing their
    visible contents with an apostrophe.
    """
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"


def is_web_link(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False
