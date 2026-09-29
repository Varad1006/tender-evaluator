"""NIC e-tendering BOQ spreadsheet reader (BoQ_Ver3.0, as downloaded from MahaTender).

For PMC works the BOQ is a "Percentage BoQ": one line "As per tender abstract" carrying the
estimated amount, and the bidder quotes a percentage Excess (+) / Less (-) / At Par against it.
So the spreadsheet is a clean, structured source for the estimated cost and the bid form,
which the PDFs often state inconsistently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BoqSummary:
    boq_type: str | None = None  # "Percentage" | "Item Rate" | ...
    contract_no: str | None = None
    work_name: str | None = None
    authority: str | None = None
    estimated_total: float | None = None
    cells: list[tuple[str, int, int, str]] = field(default_factory=list)  # (sheet, row, col, text) cited facts
    error: str | None = None


def _text(v) -> str:
    return re.sub(r"\s+", " ", str(v)).strip() if v is not None else ""


def _rows(path: str | Path, kind: str):
    """(sheet name, rows as lists of cell text) for .xls (xlrd) or .xlsx (openpyxl)."""
    if kind == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = [[_text(v) for v in r] for r in ws.iter_rows(max_row=400, values_only=True)]
        wb.close()
        return ws.title, rows
    import xlrd  # .xls (BIFF); the NIC template is .xls

    wb = xlrd.open_workbook(str(path))
    sheet = wb.sheet_by_index(0)
    return sheet.name, [[_text(c.value) for c in sheet.row(r)] for r in range(min(sheet.nrows, 400))]


def read_boq(path: str | Path, kind: str = ".xls") -> BoqSummary:
    try:
        sheet_name, rows = _rows(path, kind)
    except Exception as exc:  # corrupt, password-protected, or not really a spreadsheet
        return BoqSummary(error=f"Could not read spreadsheet: {exc}")

    out = BoqSummary()
    for r, row in enumerate(rows):
        row = [c if c != "None" else "" for c in row]
        first = next((c for c in row if c), "")
        if not first:
            continue
        if out.boq_type is None and first.lower().endswith("boq") and len(first) < 40:
            out.boq_type = first[:-3].strip() or None
            out.cells.append((sheet_name, r + 1, 1, first))
        m = re.match(r"(Contract No|Name of Work|Tender Inviting Authority)\s*:\s*(.+)", first, re.I)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip()
            if key.startswith("contract"):
                out.contract_no = value
            elif key.startswith("name"):
                out.work_name = value
            else:
                out.authority = value
            out.cells.append((sheet_name, r + 1, 1, first))
        if first.lower().startswith("total in figures") and out.estimated_total is None:
            numbers = [float(c) for c in row[1:] if re.fullmatch(r"\d+(\.\d+)?", c)]
            if numbers:
                out.estimated_total = numbers[0]
                out.cells.append((sheet_name, r + 1, 2, f"Total in Figures: {numbers[0]:.0f}"))
    return out
