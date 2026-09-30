"""Finding a bidder's completed works, whatever shape they arrived in.

No two bidders prove the same thing the same way. Across eleven real submissions the evidence for
"works of similar type completed in the last five years" turned up as all of:

  * a **prescribed statement** — "FORM-II, Details of works of similar type and magnitude carried
    out by the Contractor", a ruled grid whose header the bidder wrote themselves;
  * a **completion certificate** from the client authority — a letter with the facts as labelled
    lines, and typically three different amounts on it (accepted tender amount, administrative
    approval, work done amount) that mean different things;
  * a **work order**, an agreement, or a plain sentence in a covering letter.

So this module does not look for a schema. It looks for a *record*: a description of work, a sum
of money, and a date it finished. Where those three sit, and what they are called, is read from
the bidder's own labels each time.

Three things it refuses, because each one credits a bidder with work they have not done:

  * **Work in hand is not completed work.** Form I and Form II are the same grid with different
    headings, and the difference is the whole point. A table whose header says "work in hand",
    "tendered for" or "anticipated date of completion" is skipped.
  * **A stipulated date is not an actual date.** Both appear as columns in the same statement.
  * **Income-tax acknowledgements, registration receipts and bank statements are not work proof.**
    They carry money and dates and sit in the same bundle; one submission's ITR shows a total
    income of ₹14,75,310 next to a Pune address, which reads exactly like a small completed work.

Amounts must be whole and plausible. A cell where three columns have collided into one
("18,0 6 , 7 3 , 3 7 , 1 5 3 1 8 , 0 3 , 3 4 , 1 8 1") states no number anybody can defend, and is
reported as unreadable rather than resolved into a figure the page never contained.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.adapters.pdf_parser import ParsedDocument, _unwrap_rows

# --------------------------------------------------------------------------------------------
# Vocabulary. Domain words, not any one bidder's wording — every one of these is a label an
# authority or a contractor might use for the same column.
# --------------------------------------------------------------------------------------------

DESC = re.compile(r"name\s*of\s*(?:the\s*)?work|description|particular|nature\s*of\s*work|project|work\s*name", re.I)
PLACE = re.compile(r"place|location|country|client|authority|department|employer|organi[sz]ation", re.I)

# Preference order matters: what a contractor was finally paid is the value of the work they did.
VALUE_DONE = re.compile(r"work\s*done|executed|gross\s*(?:amount|value)|final\s*bill|completed\s*(?:cost|value|amount)", re.I)
VALUE_CONTRACT = re.compile(r"cost\s*of\s*work|value\s*of\s*work|contract\s*(?:cost|value|amount|price)"
                            r"|accepted\s*tender|tender(?:ed)?\s*(?:cost|amount|value)|agreement\s*amount"
                            r"|work\s*order\s*amount|cost\b|amount\b|value\b", re.I)
VALUE_NOT = re.compile(r"administrat\w*\s*approval|estimated\s*cost|sanction|remaining|balance|deposit|emd", re.I)

DATE_ACTUAL = re.compile(r"actual\s*date|date\s*of\s*(?:work\s*)?completion|completed\s*on|actual\s*completion"
                         r"|date\s*of\s*complet", re.I)
DATE_STIPULATED = re.compile(r"stipulat|schedul|due\s*date|as\s*per\s*agreement|anticipated", re.I)
DATE_START = re.compile(r"start|commenc|date\s*of\s*work\s*start", re.I)

# A grid of work the bidder has NOT finished. Same shape as the completed-works statement.
IN_HAND = re.compile(r"work[s]?\s*in\s*hand|tendered\s*for|anticipated\s*date|cost\s*of\s*remaining"
                     r"|balance\s*work|in\s*progress|ongoing\s*work", re.I)

# Documents that are not work proof at all, however much money and how many dates they carry.
NOT_WORK_PROOF = re.compile(
    r"income\s*tax\s*return|acknowledgement\s*number|assessment\s*year|\bITR-?\d?\b|form\s*26as"
    r"|gstr-?\d|leave\s+and\s+licen|registration\s*fee|stamp\s*duty|document\s*handling"
    r"|bank\s*statement|passbook|balance\s*sheet|profit\s*(?:&|and)\s*loss|provident\s*fund"
    r"|bid\s*capacity|turnover\s*certificate|solvency"
    # A purchase invoice for plant: money, a description and a date, but nothing was built.
    r"|\b[CSI]GST\b|tax\s*invoice|invoice\s*(?:no|value|date)|total\s*value\s*of\s*supply"
    r"|engine\s*no|chassis|\bHSN\b|place\s*of\s*supply", re.I)

# What makes a page *about* completed work, when nothing else does.
COMPLETED_MARKER = re.compile(
    r"completion\s*certificate|work\s*done\s*certificate|satisfactor\w+\s*(?:work|service|complet)"
    r"|(?:has|have)\s*(?:been\s*)?completed|completed\s*(?:the\s*)?(?:said\s*)?work"
    r"|works?\s*of\s*similar|similar\s*type\s*and\s*magnitude|form\s*-?\s*(?:II|2)\b|statement\s*-?\s*(?:no\.?\s*)?(?:II|2)\b",
    re.I)

# A works contract below a lakh is not evidence for a tender, and above ten thousand crore is a
# reading error. The bounds only reject the impossible; they never adjust a figure.
MIN_VALUE = 100_000.0
MAX_VALUE = 100_000_000_000.0

_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}

# A whole, well-formed amount. Spaces are tolerated only where a comma already separates groups,
# so collided columns do not silently fuse into one number.
_AMOUNT = re.compile(r"(?<![\d.])(?:rs\.?|inr|₹)?\s*(\d{1,3}(?:\s*,\s*\d{2,3})+(?:\.\d{1,2})?|\d{5,}(?:\.\d{1,2})?)(?![\d])",
                     re.IGNORECASE)
_DMY = re.compile(r"\b(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})\b")
_MON_YEAR = re.compile(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s*[-–]?\s*(\d{2,4})\b", re.I)
_BARE_YEAR = re.compile(r"\b(20\d{2}|19\d{2})\b")


@dataclass
class WorkRecord:
    """One completed work a bidder claims, with where each part of it was read from."""

    description: str
    value: float | None
    value_label: str | None
    year: int | None
    date: str | None
    page: int
    quote: str
    shape: str  # statement_table | certificate | sentence
    year_certain: bool = True
    other_amounts: list[tuple[str, float]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.value is not None and bool(self.description)


def _amounts(text: str) -> list[float]:
    out = []
    for m in _AMOUNT.finditer(text or ""):
        try:
            v = float(re.sub(r"[\s,]", "", m.group(1)))
        except ValueError:
            continue
        if MIN_VALUE <= v <= MAX_VALUE:
            out.append(v)
    return out


def _year_of(text: str) -> tuple[int | None, str | None, bool]:
    """(year, the date as printed, whether the year is certain).

    A completion column often carries only "March -25" or "Sep 23". In a date column the two
    digits are a year, but that reading is not certain enough to reject a bidder's work on, so it
    is flagged and the window check is told to be lenient rather than silently strict.
    """
    t = (text or "").strip()
    if not t:
        return None, None, True
    m = _DMY.search(t)
    if m:
        y = int(m.group(3))
        y += 2000 if y < 100 and y < 70 else (1900 if y < 100 else 0)
        return y, m.group(0), True
    m = _MON_YEAR.search(t)
    if m:
        y = int(m.group(2))
        # "March -25" is a year in a completion column, but only a four-digit one is beyond doubt.
        return (y + 2000 if y < 100 else y), m.group(0), y >= 100
    m = _BARE_YEAR.search(t)
    if m:
        return int(m.group(1)), m.group(0), True
    return None, None, True


def _role_of(label: str) -> str | None:
    """What a column heading is for. Order encodes precedence between overlapping vocabularies."""
    if not label:
        return None
    if DATE_STIPULATED.search(label):
        return "date_stipulated"
    if DATE_START.search(label):
        return "date_started"
    if DATE_ACTUAL.search(label):
        return "date_actual"
    if VALUE_NOT.search(label):
        return "value_other"
    if VALUE_DONE.search(label):
        return "value_done"
    if DESC.search(label):
        return "description"
    if PLACE.search(label):
        return "place"
    if VALUE_CONTRACT.search(label):
        return "value_contract"
    return None


_DRIFT = 2  # columns


def _align(header: list[str], cells: list[str], roles: dict[int, str]) -> dict[str, list[int]]:
    """Which data cell belongs to which heading, when the two do not line up exactly.

    A ruled grid does not always put a heading in the same column as the data beneath it: in one
    bidder's Form II the "Stipulated date of completion" and "Actual date of completion" headings
    land one column right of their own figures, so exact-index lookup reads both dates as blank and
    every work loses its completion year. Each heading therefore takes the nearest non-empty cell
    within a couple of columns, left to right, and claims it so two headings cannot take the same
    one.
    """
    out: dict[str, list[int]] = {}
    taken: set[int] = set()
    for j in sorted(roles):
        role = roles[j]
        for offset in range(0, _DRIFT + 1):
            hit = next((k for k in (j + offset, j - offset)
                        if 0 <= k < len(cells) and k not in taken and cells[k]), None)
            if hit is not None:
                taken.add(hit)
                out.setdefault(role, []).append(hit)
                break
    return out


def _from_table(rows: list[list[str]], page: int) -> list[WorkRecord]:
    rows = _unwrap_rows(rows)
    if not rows:
        return []
    flat = " ".join(c or "" for r in rows[: min(4, len(rows))] for c in r)
    if IN_HAND.search(flat):
        return []  # Form I: work the bidder has not finished

    head_idx, roles = None, {}
    for i, row in enumerate(rows[: min(5, len(rows))]):
        found = {}
        for j, cell in enumerate(row):
            role = _role_of((cell or "").strip())
            if role and j not in found:
                found[j] = role
        kinds = set(found.values())
        # A description beside a money column is not enough: that is also every bill of quantities
        # ("Item | Description | Executed Qty | Amount") and every machinery invoice ("Description
        # of Goods | Value"). One bidder's work order yielded nine "completed works", one per BOQ
        # line, and another's yielded a tandem compactor and a line of SGST.
        #
        # What a completed-works statement has and those do not is a column for the date the work
        # finished. A statement with no completion date anywhere could not be windowed against the
        # tender's five years in any case, so requiring one costs nothing that was usable.
        if (kinds & {"date_actual", "date_stipulated"}
                and "description" in kinds
                and any(r.startswith("value") for r in kinds)):
            head_idx, roles = i, found
            break
    if head_idx is None:
        return []

    out: list[WorkRecord] = []
    for row in rows[head_idx + 1:]:
        cells = [(c or "").strip() for c in row]
        by_role = _align(rows[head_idx], cells, roles)
        desc_cols = by_role.get("description", [])
        value_cols = sorted(by_role.get("value_done", []) + by_role.get("value_contract", []),
                            key=lambda j: 0 if roles.get(j) == "value_done" else 1)
        actual_cols = by_role.get("date_actual", [])
        stip_cols = by_role.get("date_stipulated", [])
        desc = next((cells[j] for j in desc_cols if j < len(cells) and len(cells[j]) > 8), "")
        if not desc:
            continue
        value, label, others, issues = None, None, [], []
        for j in value_cols:
            if j >= len(cells):
                continue
            found = _amounts(cells[j])
            if len(found) > 1:
                issues.append(f"more than one figure in the {rows[head_idx][j]!r} cell: {cells[j][:60]!r}")
                continue
            if not found:
                if re.search(r"\d", cells[j]) and len(cells[j]) > 12:
                    issues.append(f"the {rows[head_idx][j] or 'cost'!r} cell could not be read: {cells[j][:60]!r}")
                continue
            if value is None:
                value, label = found[0], (rows[head_idx][j] or "").strip() or None
            else:
                others.append(((rows[head_idx][j] or "").strip(), found[0]))
        year, printed, certain = None, None, True
        for j in actual_cols or stip_cols:
            if j < len(cells):
                year, printed, certain = _year_of(cells[j])
                if year:
                    break
        if not actual_cols and stip_cols and year:
            issues.append("completion date taken from the stipulated column; no actual date was stated")
        out.append(WorkRecord(description=re.sub(r"\s+", " ", desc)[:300], value=value, value_label=label,
                              year=year, date=printed, page=page,
                              quote=" | ".join(c for c in cells if c)[:400], shape="statement_table",
                              year_certain=certain, other_amounts=others, issues=issues))
    return out


def _from_certificate(lines: list[str], page: int) -> list[WorkRecord]:
    """A completion certificate states its facts as labelled lines rather than a grid."""
    blob = "\n".join(lines)
    if not COMPLETED_MARKER.search(blob):
        return []
    desc, value, label, others, year, printed, certain = "", None, None, [], None, None, True
    for line in lines:
        head, _, tail = re.split(r"(:|\|)", line, maxsplit=1) + ["", ""] if False else (line, "", line)
        m = re.match(r"\s*(?P<label>[A-Za-z][A-Za-z .()/&-]{3,44}?)\s*[:\-–|]\s*(?P<rest>.+)$", line)
        if m:
            head, tail = m.group("label"), m.group("rest")
        else:
            head, tail = line, line
        role = _role_of(head)
        if role == "description" and len(tail.strip()) > 8 and not desc:
            desc = tail.strip()
        elif role in ("value_done", "value_contract"):
            found = _amounts(tail)
            if found:
                if value is None or role == "value_done":
                    if value is not None:
                        others.append((label or "", value))
                    value, label = found[0], head.strip()
                else:
                    others.append((head.strip(), found[0]))
        elif role == "date_actual" and year is None:
            year, printed, certain = _year_of(tail)
    if not desc:
        m = re.search(r"(?:name\s*of\s*work|work\s*of|completed\s*the\s*work\s*of)\s*[:\-–]?\s*(.{10,200})", blob, re.I)
        desc = m.group(1).strip() if m else ""
    if value is None or not desc:
        return []
    return [WorkRecord(description=re.sub(r"\s+", " ", desc)[:300], value=value, value_label=label, year=year,
                       date=printed, page=page, quote=re.sub(r"\s+", " ", blob)[:400], shape="certificate",
                       year_certain=certain, other_amounts=others)]


_SENTENCE = re.compile(
    r"(?:completed|executed|carried\s*out)\b.{0,160}?"
    r"(?:rs\.?|inr|₹)\s*\d[\d,\s.]{4,}", re.IGNORECASE | re.DOTALL)


def _from_sentence(text: str, page: int) -> list[WorkRecord]:
    if not COMPLETED_MARKER.search(text) or not _SENTENCE.search(text):
        return []
    values = _amounts(text)
    year, printed, certain = _year_of(text)
    if not values:
        return []
    desc = re.sub(r"\s+", " ", text)[:300]
    return [WorkRecord(description=desc, value=values[0], value_label=None, year=year, date=printed,
                       page=page, quote=desc[:400], shape="sentence", year_certain=certain)]


def find_completed_works(doc: ParsedDocument, pages: set[int] | None = None) -> list[WorkRecord]:
    """Every completed work the submission evidences, from whichever shape carries it."""
    out: list[WorkRecord] = []
    for p in doc.pages:
        if pages is not None and p.number not in pages:
            continue
        text = p.text or ""
        if not text.strip():
            continue
        # A page whose text layer is not trustworthy states no figure worth citing. Saying nothing
        # sends it to OCR; parsing it anyway puts "2t7.t2" in front of a committee as a number.
        if not p.quality.quotable:
            continue
        if NOT_WORK_PROOF.search(text):
            continue  # an ITR, a registration receipt, a bank page: money and dates, no work
        for rows in p.tables:
            out += _from_table(rows, p.number)
        if not any(r.page == p.number for r in out):
            out += _from_certificate(text.splitlines(), p.number)
        if not any(r.page == p.number for r in out):
            for s in doc.sentences:
                if s.page == p.number:
                    out += _from_sentence(s.text, p.number)

    # One record per (page, value, description): a certificate is often summarised in a statement.
    seen, unique = set(), []
    for r in out:
        key = (r.value, r.description[:60].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return [r for r in unique if r.usable]
