"""Reading a year-by-year money table out of a chartered accountant's certificate.

Turnover and bid-capacity certificates state their figures as a grid, and the two halves of a fact
never share a cell. A turnover certificate puts the year and the amount side by side:

    Financial Year | Turnover (Rs)
    2020-2021      | 3,43,19,907/-
    2021-2022      | 2,81,82,821/-

while a bid-capacity working turns the same data ninety degrees and stacks derived rows under it:

    PARTICULARS                 | 2020-2021   | 2021-2022   | 2022-2023
    Amount of Work During Year  | 3,43,19,907 | 2,81,82,821 | 3,08,42,535
    Factor For Updating         | 1.40        | 1.30        | 1.20
    Updated Value of Work       | 4,80,47,870 | 3,66,37,667 | 3,70,11,042

Both orientations are read here, and the row label is kept, because which row a number came from
decides what it means. Reading "Updated Value of Work" as the bidder's turnover would apply the
escalation twice — once by the accountant and once by us — and hand the bidder a figure neither
document states.

Cells carry no currency: it sits in the column header, so `parse_amounts` (which requires a marker)
finds nothing here. A bare cell is read as rupees only when it is unambiguously a money-shaped
number — Indian grouping, or four digits and up. That deliberately excludes "1.40", "1 Year" and
"03", which share a column with real amounts and are not any.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.values import fy_start

# "3,43,19,907/-", "Rs. 41,96,280", "1,26,40,423", "217.12" is NOT money here (it is lakhs in a
# different table and is handled by its own header), and "1.40" is a factor.
_BARE = re.compile(
    r"^\s*(?:rs\.?|inr|₹)?\s*"
    r"(?P<n>\d{1,3}(?:\s*,\s*\d{2,3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)"
    r"\s*(?:/-|/–)?\s*$",
    re.IGNORECASE,
)
_FY_CELL = re.compile(r"^\s*(?:[•o·*\-]\s*)?(?:financial\s*year\s*|F\.?\s?Y\.?\s*)?(?P<y1>(?:19|20)\d{2})\s*[-–—/]\s*(?P<y2>(?:19|20)\d{2}|\d{2})\s*$", re.I)

# A number that is plainly not an amount, even though it sits in the same column as amounts.
_NOT_AMOUNT = re.compile(r"^\s*\d{1,3}(?:\.\d+)?\s*(?:year|yr|month|nos?|%)?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class YearValue:
    year: int  # financial-year start
    value: float
    cell: str  # verbatim cell text, for the citation
    label: str | None  # the row or column heading the figure sat under


_UNIT_HEAD = re.compile(r"(?P<lakh>lakh|lacs?\b|लक्ष|लाख)|(?P<crore>crore|\bcr\.?\b|कोटी)", re.IGNORECASE)
_SMALL = re.compile(r"^\s*(?P<n>\d{1,5}(?:\.\d+)?)\s*$")


def unit_of(label: str | None) -> float:
    """1, 1e5 or 1e7: the unit a column heading states its figures in ("Turnover (Fig in Lakhs)")."""
    m = _UNIT_HEAD.search(label or "")
    if not m:
        return 1.0
    return 100_000.0 if m.group("lakh") else 10_000_000.0


def cell_amount(text: str, unit: float = 1.0) -> float | None:
    """Rupees stated by a bare table cell, or None when the cell is not an amount. With a unit from
    the heading, "271.42" under "(Fig in Lakhs)" is ₹2,71,42,000."""
    t = (text or "").strip()
    if unit > 1 and _SMALL.match(t):
        return float(_SMALL.match(t).group("n")) * unit
    if not t or _NOT_AMOUNT.match(t):
        return None
    m = _BARE.match(t)
    if not m:
        return None
    try:
        return float(re.sub(r"[\s,]", "", m.group("n")))
    except ValueError:
        return None


_FY_AY = re.compile(r"^\s*F\.?\s?Y\.?\s*(?P<y1>(?:19|20)\d{2})\s*[-–—/]\s*(?P<y2>(?:19|20)\d{2}|\d{2})\s*(?:A\.?\s?Y\.?.*)?$", re.I)


def cell_year(text: str) -> int | None:
    t = (text or "").strip()
    m = _FY_CELL.match(t) or _FY_AY.match(t)  # "F. Y. 2024-25 A. Y. 2025-26": the financial year, not the assessment year
    return fy_start(m.group("y1"), m.group("y2")) if m else None


def _label_of(row: list[str]) -> str | None:
    for c in row:
        c = (c or "").strip()
        if c and cell_amount(c) is None and cell_year(c) is None:
            return c
    return None


def read_year_series(tables: list[list[list[str]]], want: re.Pattern | None = None,
                     avoid: re.Pattern | None = None) -> list[YearValue]:
    """Year-stamped amounts from every table given, in both orientations.

    `want` and `avoid` filter on the row (or column) label, which is what separates the figures the
    bidder reported from the ones the accountant derived from them.
    """
    out: list[YearValue] = []

    def keep(label: str | None) -> bool:
        if avoid is not None and label and avoid.search(label):
            return False
        if want is None:
            return True
        return bool(label and want.search(label))

    for rows in tables:
        # Orientation A: one row per year. What the figure *is* comes from the heading of its own
        # column — "Financial Year | Turnover (Rs)" labels the amount column, and taking the row's
        # first cell instead would label every turnover figure "Financial Year".
        head = [(c or "").strip() for c in rows[0]] if rows else []
        for row in rows:
            cells = [(c or "").strip() for c in row]
            years = [(i, cell_year(c)) for i, c in enumerate(cells) if cell_year(c) is not None]
            amounts = [(i, cell_amount(c)) for i, c in enumerate(cells) if cell_amount(c) is not None]
            if len(years) == 2 and years[1][1] == years[0][1] + 1:
                years = years[:1]  # "2021-2022 | 2022-2023": the financial year, then its assessment year
            if len(amounts) > 1:
                # Government + private = total: the same figure twice is one figure; otherwise the
                # column headed "total" (anywhere in the heading rows) is the one that answers.
                distinct = {v for _, v in amounts}
                heads = [" ".join((r[i] or "") for r in rows[:3] if i < len(r)) for i in range(len(cells))]
                total = [(i, v) for i, v in amounts if re.search(r"total", heads[i], re.I)]
                amounts = amounts[-1:] if len(distinct) == 1 else (total[-1:] if total else amounts)
            if len(years) == 1 and not amounts:
                # A figure in lakhs is too small to be money on its own; its heading says what it is.
                for i, c in enumerate(cells):
                    unit = unit_of(head[i] if i < len(head) else "")
                    if unit > 1 and i != years[0][0] and cell_amount(c, unit) is not None:
                        amounts = [(i, cell_amount(c, unit))]
                        break
            if len(years) == 1 and len(amounts) == 1:
                acol = amounts[0][0]
                column_head = head[acol] if acol < len(head) else ""
                if column_head and (cell_amount(column_head) is not None or cell_year(column_head) is not None):
                    column_head = ""  # the first row is data, not a heading
                label = column_head or _label_of(row) or _label_of(head)
                if keep(label):
                    out.append(YearValue(years[0][1], amounts[0][1], cells[acol], label))

        # Orientation B: a header row of years, then labelled rows of figures beneath it.
        head_idx = next((i for i, r in enumerate(rows)
                         if sum(1 for c in r if cell_year(c) is not None) >= 2), None)
        if head_idx is None:
            continue
        col_year = {i: cell_year(c) for i, c in enumerate(rows[head_idx]) if cell_year(c) is not None}
        for row in rows[head_idx + 1:]:
            cells = [(c or "").strip() for c in row]
            if sum(1 for c in cells if cell_year(c) is not None) >= 2:
                continue  # another header, not data
            label = _label_of(row)
            if not keep(label):
                continue
            unit = unit_of(label) if unit_of(label) > 1 else unit_of(" ".join(rows[head_idx]))
            for i, year in col_year.items():
                if i >= len(cells):
                    continue
                value = cell_amount(cells[i], unit)
                if value is not None:
                    out.append(YearValue(year, value, cells[i], label))

    # One figure per (year, value); the same grid is often read in both orientations.
    seen, unique = set(), []
    for yv in out:
        k = (yv.year, yv.value)
        if k not in seen:
            seen.add(k)
            unique.append(yv)
    return unique


# Labels for the figure a bidder reported, and for the ones an accountant computed from it.
TURNOVER_LABEL = re.compile(r"turn\s?over|gross\s+receipt|contract\s+receipt|receipts|revenue|amount\s*\(", re.IGNORECASE)
WORK_DONE_LABEL = re.compile(r"amount\s+of\s+work|work\s+done|value\s+of\s+(?:civil\s+)?work", re.IGNORECASE)
DERIVED_LABEL = re.compile(r"updat|factor|escalat|multipl|weightage", re.IGNORECASE)
