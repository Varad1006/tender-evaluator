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


def _table_unit(rows: list[list[str]], head_idx: int) -> float:
    """The unit a grid states anywhere above its figures.

    `unit_of` reads one label. A CA's statement puts the unit in a second header row, under the
    years and above the data, where neither the row label nor the year header can see it:

        No. | Details    | FY 2022-23         | FY 2023-24         | FY 2024-25
            |            | Amt (Rs. in lakhs) | Amt (Rs. in lakhs) | Amt (Rs. in lakhs)
         1  | Turnover   | 407                | 417                | 453
         2  | Net-Worth  | 255                | 293                | 332

    Read without it, a turnover of ₹4.07 crore is reported as ₹407 and the bidder fails a ₹4 crore
    requirement on a unit. Only rows that state no figure of their own are consulted, so a data row
    that happens to mention "lakh" cannot re-scale its own neighbours.
    """
    for row in rows[: head_idx + 1] + [r for r in rows[head_idx + 1:]
                                       if not any(cell_amount(c) is not None for c in r)
                                       and not any(cell_year(c) is not None for c in r)]:
        unit = unit_of(" ".join(c or "" for c in row))
        if unit > 1:
            return unit
    return 1.0


_WORDY = re.compile(r"[A-Za-z\u0900-\u097F]{2}")


def _label_of(row: list[str]) -> str | None:
    """What the row is called. A numbered grid puts a serial in the first column —

        1 | Turnover  | 407 | 417 | 453
        2 | Net-Worth | 255 | 293 | 332

    and taking the first non-numeric cell labels both rows "1" and "2", so a filter looking for
    turnover finds neither. A label has to say something, so a cell needs two letters to be one;
    the old behaviour stays as the fallback for a grid whose labels are genuinely symbols."""
    plain = None
    for c in row:
        c = (c or "").strip()
        if not c or cell_amount(c) is not None or cell_year(c) is not None:
            continue
        if _WORDY.search(c):
            return c
        if plain is None:
            plain = c
    return plain


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
                    unit = unit_of(head[i] if i < len(head) else "") or 1.0
                    if unit == 1.0:
                        unit = _table_unit(rows, 0)
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
            unit = unit_of(label) if unit_of(label) > 1 else _table_unit(rows, head_idx)
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
NETWORTH_LABEL = re.compile(r"net\s*-?\s*worth|networth|net\s+assets?|owners?\s*'?\s*funds?|shareholders?\s*'?\s*funds?", re.IGNORECASE)
PROFIT_LABEL = re.compile(r"net\s+profit|profit\s+after\s+tax|\bPAT\b", re.IGNORECASE)
WORK_DONE_LABEL = re.compile(r"amount\s+of\s+work|work\s+done|value\s+of\s+(?:civil\s+)?work", re.IGNORECASE)
DERIVED_LABEL = re.compile(r"updat|factor|escalat|multipl|weightage", re.IGNORECASE)


# ----------------------------------------------------------------------------------------------
# Grids that were never ruled
# ----------------------------------------------------------------------------------------------

# A chartered accountant's certificate aligns its figures with spaces, not borders, so pdfplumber
# reports no table at all and every reader above this line sees nothing. On the DBT bid's
# Annexure-A (p.15) `page.tables` is empty while the text layer holds the whole statement:
#
#     No. FY 2022-23 FY 2023-24 FY 2024-25
#     Amt (Rs. in lakhs) Amt (Rs. in lakhs) Amt (Rs. in lakhs)
#     1 Turnover 407 417 453
#     2 Net-Worth 255 293 332
#
# The alignment is not recoverable — both pdfplumber and the OCR tier collapse runs of spaces to
# one — so the structure is rebuilt from the token pattern instead: a line naming k financial years
# declares k columns, and a line ending in k figures fills them. That is also why this works on an
# OCR'd page, which arrives as lines of text with no coordinates at all.
_FIGURE = re.compile(r"^(?:rs\.?|inr|₹)?\s*\(?-?\s*(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s*\)?\s*(?:/-|/–|%)?$", re.IGNORECASE)
_SERIAL_TOKEN = re.compile(r"^\(?\d{1,3}[.)]?$")
_FY_TOKEN = re.compile(r"^(?:F\.?\s?Y\.?)?\s*(?:19|20)\d{2}\s*[-–—/]\s*(?:(?:19|20)\d{2}|\d{2})$", re.IGNORECASE)


def _fy_tokens(line: str) -> list[int]:
    """The financial years a line names, read off the line as whole tokens."""
    out = []
    tokens = line.split()
    i = 0
    while i < len(tokens):
        for width in (3, 2, 1):  # "F. Y. 2022-23", "FY 2022-23", "2022-23"
            if i + width <= len(tokens):
                joined = " ".join(tokens[i: i + width])
                if _FY_TOKEN.match(joined):
                    year = cell_year(joined)
                    if year is not None:
                        out.append(year)
                    i += width
                    break
        else:
            i += 1
            continue
        continue
    return out


def _trailing_figures(line: str, want: int) -> tuple[str, list[str]] | None:
    """(label, figures) when a line ends in exactly `want` figures, else None."""
    tokens = line.split()
    if len(tokens) < want + 1:
        return None
    figures = tokens[-want:]
    if not all(_FIGURE.match(t) for t in figures):
        return None
    head = tokens[: -want]
    if head and _SERIAL_TOKEN.match(head[0]):
        head = head[1:]  # the Sr. No. column
    label = " ".join(head).strip(" :|-")
    return (label, figures) if label else None


def grid_from_lines(lines: list[str]) -> list[list[list[str]]]:
    """Grids rebuilt from whitespace-aligned text lines, in the shape `read_year_series` expects.

    Only emits a grid when the line structure is unambiguous: a header naming two or more financial
    years, with rows that end in exactly that many figures. A row that does not fit is skipped
    rather than padded, because a half-aligned row would file a figure under the wrong year.
    """
    grids: list[list[list[str]]] = []
    clean = [re.sub(r"\s+", " ", l).strip() for l in lines]

    # Orientation B: a header of years, then one labelled row per quantity.
    for i, line in enumerate(clean):
        years = _fy_tokens(line)
        if len(years) < 2 or len(set(years)) != len(years):
            continue
        header = [""] + [f"{y}-{str(y + 1)[-2:]}" for y in years]
        body: list[list[str]] = []
        captions: list[list[str]] = []
        for follow in clean[i + 1: i + 14]:
            if not follow or _fy_tokens(follow):
                break
            hit = _trailing_figures(follow, len(years))
            if hit:
                body.append([hit[0]] + hit[1])
            elif not body and unit_of(follow) > 1:
                # "Amt (Rs. in lakhs) Amt (Rs. in lakhs) ..." — the unit row, above the figures.
                captions.append([follow] + [""] * len(years))
        if body:
            grids.append([header] + captions + body)

    # Orientation A: one line per year, "2020-2021 3,43,19,907/-". The heading that says what the
    # figures are is the nearest line above that states neither a year nor a figure.
    run: list[list[str]] = []
    heading = ""
    for line in clean:
        years = _fy_tokens(line)
        hit = _trailing_figures(line, 1) if len(years) == 1 else None
        if len(years) == 1 and hit is not None and not hit[0]:
            pass
        if len(years) == 1:
            tokens = line.split()
            figures = [t for t in tokens if _FIGURE.match(t) and not _SERIAL_TOKEN.match(t)]
            if len(figures) == 1:
                run.append([f"{years[0]}-{str(years[0] + 1)[-2:]}", figures[0]])
                continue
        if len(run) >= 2:
            grids.append([["Financial Year", heading or "Amount"]] + run)
        run = []
        if line and not years and not any(_FIGURE.match(t) for t in line.split()):
            heading = line
    if len(run) >= 2:
        grids.append([["Financial Year", heading or "Amount"]] + run)
    return grids
