"""Price-level indexation: bringing a bidder's older figures up to today's rupees.

PMC works tenders do not compare a bidder's past turnover or past project values at face value.
Clause 4 of the qualification criteria prints a table beside each financial criterion:

    Figures of earlier years carry a weightage of 10% per year, to bring them to the current
    year's price level.

        Financial year   Multiplying Factor
        2024-2025        1.10
        2023-2024        1.21
        2022-2023        1.33
        2021-2022        1.46
        2020-2021        1.61

and the same table governs three separate criteria: maximum annual turnover, the cost of
completed similar works, and the `A` term of the bid-capacity formula.

Ignoring it is not a rounding error. A bidder whose best year was ₹30 lakh in FY 2022-23 is
being offered ₹39.9 lakh by the tender's own rule; against a ₹35 lakh threshold that is the
difference between disqualified and qualified. Reading the raw figure produces a **wrong FAIL**,
which is the one outcome this tool must never produce on its own arithmetic.

The factors are read from the tender, never assumed. Two properties make a misread table
detectable without knowing which tender it came from:

  * the factors form a geometric progression — each year back multiplies by (1 + rate), so the
    stated "10% per year" and the printed column must agree; and
  * they increase monotonically as the year gets older.

A table that fails either is reported and not applied, because applying a wrong factor moves a
bidder's money in a direction nobody can see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.values import format_inr

# "2024-2025 1.10", "2024-25  1.10", "FY 2020-21 : 1.61". A factor is a small number just above 1;
# anything at or below 1 is a share, not an escalation, and anything large is a rupee figure.
_ROW = re.compile(
    r"\b(?:F\.?Y\.?\s*)?(?P<y1>20\d{2})\s*[-–—/]\s*(?P<y2>20\d{2}|\d{2})\b[^\d\n]{0,20}(?P<f>[1-9]\.\d{1,4})\b"
)
_RATE = re.compile(r"weightage\s+of\s+(\d{1,2}(?:\.\d+)?)\s*%\s*per\s*year", re.IGNORECASE)
_INTRO = re.compile(
    r"multiplying\s+factor|weightage\s+of\s+\d+\s*%\s*per\s*year|current\s+year\s+price\s+level"
    r"|enhancing\s+the\s+costs?|updated\s+to\s+.{0,20}price\s+level",
    re.IGNORECASE,
)

_MIN_ROWS = 3
_TOLERANCE = 0.02  # printed factors are rounded to 2 dp, so 1.33 stands for 1.331


@dataclass(frozen=True)
class IndexTable:
    """Multiplying factors by financial year, as the tender prints them."""

    factors: dict[int, float]  # financial-year START year -> multiplier
    stated_rate: float | None  # the "10% per year" the prose claims, as a fraction
    implied_rate: float | None  # the rate the printed column actually follows
    page: int
    quote: str
    document_id: str | None = None
    doc_name: str | None = None
    consistent: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def current_year(self) -> int | None:
        """The financial year the factors bring older money up to: one after the newest row."""
        return max(self.factors) + 1 if self.factors else None

    def factor_for(self, fy_start: int | None) -> float | None:
        """The multiplier for a financial year, extrapolating beyond the printed rows.

        A tender prints five years because it asks for five. A bidder may still cite a sixth; the
        progression the table itself follows is used rather than dropping the figure.
        """
        if fy_start is None or not self.factors or not self.consistent:
            return None
        if fy_start in self.factors:
            return self.factors[fy_start]
        newest = max(self.factors)
        if fy_start > newest:  # at or after the current price level: no escalation
            return 1.0
        rate = self.implied_rate or self.stated_rate
        if rate is None:
            return None
        oldest = min(self.factors)
        return round(self.factors[oldest] * (1 + rate) ** (oldest - fy_start), 4)


@dataclass(frozen=True)
class Escalated:
    raw: float
    factor: float | None
    value: float
    fy_start: int | None

    @property
    def applied(self) -> bool:
        return self.factor is not None and abs(self.factor - 1.0) > 1e-9

    def explain(self) -> str:
        if not self.applied:
            return format_inr(self.raw)
        return (f"{format_inr(self.raw)} × {self.factor:g} = {format_inr(self.value)} "
                f"(FY {self.fy_start}-{str((self.fy_start or 0) + 1)[-2:]} brought to current price level)")


def _fy_start(y1: str, y2: str) -> int | None:
    """'2024-2025' and '2024-25' both start in 2024; a pair that is not consecutive is not an FY."""
    start = int(y1)
    end = int(y2) if len(y2) == 4 else int(str(start)[:2] + y2)
    return start if end == start + 1 else None


def parse_index_table(text: str, page: int, document_id: str | None = None,
                      doc_name: str | None = None) -> IndexTable | None:
    """Read a multiplying-factor table out of one page's text."""
    if not _INTRO.search(text):
        return None
    factors: dict[int, float] = {}
    for m in _ROW.finditer(text):
        start = _fy_start(m.group("y1"), m.group("y2"))
        factor = float(m.group("f"))
        if start is None or not 1.0 < factor < 5.0:
            continue
        factors.setdefault(start, factor)
    if len(factors) < _MIN_ROWS:
        return None

    rate_m = _RATE.search(text)
    stated = float(rate_m.group(1)) / 100 if rate_m else None

    years = sorted(factors, reverse=True)  # newest first
    notes: list[str] = []
    consistent = all(factors[a] < factors[b] for a, b in zip(years, years[1:]))
    if not consistent:
        notes.append("The multiplying factors do not increase with age — the table was misread and is not applied.")

    ratios = [factors[b] / factors[a] for a, b in zip(years, years[1:]) if factors[a]]
    implied = round(sum(ratios) / len(ratios) - 1, 4) if ratios else None
    if consistent and implied is not None and any(abs(r - (1 + implied)) > _TOLERANCE for r in ratios):
        consistent = False
        notes.append("The printed factors do not follow one constant rate; the table is reported, not applied.")
    if consistent and stated is not None and implied is not None and abs(stated - implied) > _TOLERANCE:
        notes.append(f"The tender says {stated:.0%} per year but its factors follow {implied:.1%}; "
                     "the printed factors are used, as those are what the committee applies.")

    quote = next((line.strip() for line in text.splitlines() if _INTRO.search(line)), "")[:300]
    return IndexTable(factors=factors, stated_rate=stated, implied_rate=implied, page=page,
                      quote=quote or f"Multiplying factors for FY {years[-1]} to FY {years[0]}",
                      document_id=document_id, doc_name=doc_name, consistent=consistent, notes=notes)


# The rule in prose with no table beside it: PMC's tender-procedure circular asks for turnover, the
# cost of works and so on to be worked out at current rates with a प्रतिवर्षी १०% वाढ (a 10%
# increase for each year).
_PROSE_RATE = re.compile(
    r"प्रतिवर्षी\s*(?P<mr>[०-९\d]{1,2}(?:\.[०-९\d]+)?)\s*%\s*वाढ"
    r"|(?:weightage|increase|escalation|enhancement)\s+of\s+(?P<en>\d{1,2}(?:\.\d+)?)\s*%\s*per\s*(?:year|annum)",
    re.IGNORECASE)


def table_from_stated_rule(docs, current_fy: int | None, years: int = 6) -> IndexTable | None:
    """The factors a stated rate implies, for a tender that states the rate but prints no table.

    The current price level is the tender's own financial year; each year back multiplies by
    (1 + rate), exactly the progression a printed table must follow. Without the tender's year
    there is no current level to escalate to, and nothing is applied.
    """
    from app.services.values import ascii_digits

    if current_fy is None:
        return None
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            if p.quality.kind not in ("english", "ocr_en", "ocr_mr"):
                continue
            text = p.text or ""
            m = _PROSE_RATE.search(text)
            if not m:
                continue
            rate = float(ascii_digits(m.group("mr") or m.group("en"))) / 100
            if not 0 < rate <= 0.25:
                continue
            start, end = text.rfind("\n", 0, m.start()) + 1, text.find("\n", m.end())
            factors = {current_fy - k: round((1 + rate) ** k, 4) for k in range(1, years + 1)}
            return IndexTable(
                factors=factors, stated_rate=rate, implied_rate=rate, page=p.number,
                quote=text[start:end if end != -1 else None].strip()[:300], document_id=d.document_id,
                doc_name=d.name, consistent=True,
                notes=[f"The tender states {rate:.0%} per year and prints no factors; they are computed from that "
                       f"rate with FY {current_fy}-{str(current_fy + 1)[-2:]} as the current year."])
    return None


def find_index_tables(docs) -> list[IndexTable]:
    """Every multiplying-factor table in the bundle's readable pages, newest page first.

    `docs` are DocViews; only pages with a text layer can carry a table this precise, and a
    factor recovered from a bad OCR layer would be worse than none.
    """
    from app.adapters.readability import ENGLISH_KINDS

    out: list[IndexTable] = []
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            if p.quality.kind not in ENGLISH_KINDS:
                continue
            table = parse_index_table(p.raw_text or p.text, p.number, d.document_id, d.name)
            if table:
                out.append(table)
    return out


def merge(tables: list[IndexTable]) -> IndexTable | None:
    """One table for the bundle. The same factors are reprinted beside each criterion; identical
    repeats are one table, and a genuine disagreement is surfaced rather than silently resolved."""
    usable = [t for t in tables if t.consistent]
    if not usable:
        return tables[0] if tables else None
    first = usable[0]
    differing = [t for t in usable if t.factors != first.factors]
    if not differing:
        return first
    notes = list(first.notes) + [
        "The bundle prints more than one multiplying-factor table and they differ ("
        + "; ".join(f"p.{t.page}: " + ", ".join(f"{y}:{t.factors[y]:g}" for y in sorted(t.factors, reverse=True)[:3])
                    for t in [first, *differing][:3])
        + "). The first is used; the committee should confirm."]
    return IndexTable(first.factors, first.stated_rate, first.implied_rate, first.page, first.quote,
                      first.document_id, first.doc_name, first.consistent, notes)


def table_from_params(params: dict) -> IndexTable | None:
    """Rebuild the table a criterion carries. Year keys survive a JSON round-trip as strings."""
    raw = (params or {}).get("index_factors") or {}
    if not raw or not params.get("index_applies", True):
        return None
    try:
        factors = {int(y): float(f) for y, f in raw.items()}
    except (TypeError, ValueError):
        return None
    if not factors:
        return None
    rate = params.get("index_rate")
    return IndexTable(factors=factors, stated_rate=None, implied_rate=float(rate) if rate else None,
                      page=int(params.get("index_page") or 0), quote="", consistent=True)


def escalate(amount: float | None, fy_start: int | None, table: IndexTable | None) -> Escalated | None:
    """A bidder's figure brought to the tender's current price level."""
    if amount is None:
        return None
    factor = table.factor_for(fy_start) if table else None
    if factor is None:
        return Escalated(raw=amount, factor=None, value=amount, fy_start=fy_start)
    return Escalated(raw=amount, factor=factor, value=round(amount * factor, 2), fy_start=fy_start)


def best_escalated(values: list[tuple[float, int | None]], table: IndexTable | None) -> Escalated | None:
    """The highest of a bidder's year-stamped figures after indexation.

    "Maximum turnover in any of the last five years" is a maximum *after* the factors are applied:
    an older, smaller year can win once it is brought to current rupees.
    """
    scored = [e for e in (escalate(v, y, table) for v, y in values) if e is not None]
    return max(scored, key=lambda e: e.value) if scored else None
