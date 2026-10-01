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

from difflib import SequenceMatcher
from typing import NamedTuple

from app.adapters.pdf_parser import ParsedDocument, _unwrap_rows
from app.services.values import ascii_digits

# --------------------------------------------------------------------------------------------
# Vocabulary. Domain words, not any one bidder's wording — every one of these is a label an
# authority or a contractor might use for the same column.
# --------------------------------------------------------------------------------------------

DESC = re.compile(r"name\s*of\s*(?:the\s*)?work|description|particular|nature\s*of\s*work|project|work\s*name"
                  r"|कामा?चे\s*ना[ंव]?व|कामाचे\s*वर्णन|कामाचा\s*तपशील|कामाचे\s*स्वरूप", re.I)
PLACE = re.compile(r"place|location|country|client|authority|department|employer|organi[sz]ation", re.I)

# Preference order matters: what a contractor was finally paid is the value of the work they did.
VALUE_DONE = re.compile(r"work\s*done|executed|gross\s*(?:amount|value)|final\s*bill|completed\s*(?:cost|value|amount)"
                        # Marathi certificates: the amount the work came to, was billed or was paid.
                        r"|काम\s*प[ुू]र्ण\s*(?:रक्कम|रकम|र\.?\s*रु?)|बिला?(?:ची)?\s*(?:रक्कम|रकम)|अदा\s*केलेली|एकु?ण\s*अदा"
                        r"|प्रत्यक्ष\s*(?:खर्च|झालेल्या\s*कामाची)|अंतिम\s*(?:बिल|देयक)", re.I)
VALUE_CONTRACT = re.compile(r"cost\s*of\s*work|value\s*of\s*work|contract\s*(?:cost|value|amount|price)"
                            r"|accepted\s*tender|tender(?:ed)?\s*(?:cost|amount|value)|agreement\s*amount"
                            r"|work\s*order\s*amount|cost\b|amount\b|value\b"
                            # the accepted tender: what the contract was let for
                            r"|मान्यता\s*र|मान्य\s*र|मंजूर\s*निविदा|स्वीकृत|ठेकेदाराची\s*रक्कम|ठेकेदार\s*(?:निविदा|टेंडर)\s*र|करारनामा", re.I)
# "टेंडर र." / "निविदा रक्कम": the amount put to tender in some offices, the accepted amount in others.
# Used only when a certificate states nothing better.
VALUE_TENDER = re.compile(r"टेंडर\s*र(?:क्कम|कम|\.|\s|$)|टे\.?\s*र\.|निविदा\s*र(?:क्कम|कम|\.)", re.I)
VALUE_NOT = re.compile(r"administrat\w*\s*approval|estimated\s*cost|sanction|remaining|balance|deposit|emd"
                       # the estimate or the budget provision: what the authority expected, not what was built
                       r"|प\s*ु?\s*\.?\s*ग\s*\.?\s*प|प[ूु]र्वगण|तरतूद|अंदाज|मु[ळल]\s*निविदा|शेष", re.I)

DATE_ACTUAL = re.compile(r"actual\s*date|date\s*of\s*(?:work\s*)?completion|completed\s*on|actual\s*completion"
                         r"|date\s*of\s*complet|काम\s*प[ुू]र्ण\s*(?:झाल्याचा\s*)?(?:दि|तारीख)|प[ूु]र्णत्वाचा\s*(?:दि|तारीख)", re.I)
DATE_STIPULATED = re.compile(r"stipulat|schedul|due\s*date|as\s*per\s*agreement|anticipated"
                             r"|काम\s*प[ुू]र्ण\s*करण्याची", re.I)
DATE_START = re.compile(r"start|commenc|date\s*of\s*work\s*start|वर्क\s*ऑ?आ?र्डर|वर्कऑर्डर|कार्या?ं?रंभ"
                        r"|काम\s*(?:चालू|सुरु)\s*(?:करण्याची\s*)?(?:दि|तारीख)", re.I)

# A grid of work the bidder has NOT finished. Same shape as the completed-works statement.
IN_HAND = re.compile(r"work[s]?\s*in\s*hand|tendered\s*for|anticipated\s*date|cost\s*of\s*remaining"
                     r"|balance\s*work|in\s*progress|ongoing\s*work|हातातील\s*काम", re.I)
# A column, or a certificate row, that says whether the work is finished.
# "काम पूर्ण" / "काम चालू" alone as a row's label: the form's status line, its value the one that applies.
STATUS = re.compile(r"^\s*(?:remarks?|status|शेरा|स्थिती|सद्यस्थिती|काम\s*(?:प[ुू]र्ण|चालू))\s*$|काम\s*चालू\s*/|काम\s*प[ुू]र्ण\s*/", re.I)
YEAR_COL = re.compile(r"^\s*(?:सन|वर्ष|आर्थिक\s*वर्ष|F\.?\s*Y\.?|year|financial\s*year)\s*$", re.I)
TENDER_NO = re.compile(r"टेंडर\s*(?:क्र|नं|क्रमांक)|निविदा\s*(?:क्र|क्रमांक)|tender\s*(?:no|number|ref)", re.I)
CONTRACTOR = re.compile(r"ठेकेदाराचे\s*नाव|contractor'?s?\s*name|name\s*of\s*(?:the\s*)?contractor", re.I)

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
    r"|works?\s*of\s*similar|similar\s*type\s*and\s*magnitude|form\s*-?\s*(?:II|2)\b|statement\s*-?\s*(?:no\.?\s*)?(?:II|2)\b"
    r"|कामाचा\s*दाखला|प[ुू]?र्णत्वाचा\s*दाखला|प[ुू]र्ण\s*केले|समाधान\S*\s*(?:रित्या|रीत्या|पूर्वक|पणे|कारक)?\s*प[ुू]र्ण",
    re.I)
# Not finished, whatever the certificate's boilerplate says: "काम चालू (७५% काम पूर्ण)", "1st running bill".
IN_PROGRESS = re.compile(r"चालू|सुरु|सुरू|प्रगतीपथ|अपू?र्ण|बाकी|प्रलंबित|in\s*progress|under\s*progress|ongoing|partial"
                         r"|पार्टली|running\s*bill|रनिंग\s*बिल|pending", re.I)

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
_FY = re.compile(r"\b((?:19|20)\d{2})\s*[-–]\s*(?:(?:19|20)\d{2}|\d{2})\b")
_SHORT_FY = re.compile(r"(?<![\d/.])(\d{2})\s*-\s*(\d{2})(?![\d/.])")


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
    contractor: str | None = None  # whose name the certificate is issued in, as printed
    tender_no: str | None = None
    foreign: bool = False  # issued to somebody other than the firm the rest of the bid's certificates name
    # When the completion year is known only within limits: the tender year is a floor, the date the
    # certificate was issued a ceiling, a financial year spans two calendar years.
    year_min: int | None = None
    year_max: int | None = None

    @property
    def usable(self) -> bool:
        return self.value is not None and bool(self.description)


_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+$")


def _well_grouped(raw: str) -> bool:
    """"31,62,57.40" is not a number: in either grouping the last group before the paise has three
    digits. OCR drops a digit, and the result must be refused rather than read as ₹2.05 lakh."""
    whole = re.sub(r"\s", "", raw).split(".")[0]
    return "," not in whole or bool(_INDIAN.match(whole) or _WESTERN.match(whole))


def _amounts(text: str) -> list[float]:
    out = []
    # "६,१२,३४५/-" and "३,२१,४५०=७५" (rupees = paise) are Marathi certificates' way of writing money.
    t = re.sub(r"(\d)\s*=\s*(\d{2})\b", r"\1.\2", ascii_digits(text or ""))
    for m in _AMOUNT.finditer(t):
        if not _well_grouped(m.group(1)):
            continue
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
    t = ascii_digits(text or "").strip()
    if not t:
        return None, None, True
    m = _DMY.search(t)
    fy = _FY.search(t)
    if fy and not m:
        # "2023-24" in a year column, "सन २०२३-२४" in a certificate: the financial year the work was done in.
        return int(fy.group(1)), fy.group(0), True
    short = _SHORT_FY.search(t)
    if short and not m and int(short.group(2)) == (int(short.group(1)) + 1) % 100:
        return 2000 + int(short.group(1)), short.group(0), True
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
    if VALUE_DONE.search(label) and re.search(r"[\u0900-\u097F]", label):
        return "value_done"  # "काम पूर्ण रक्कम": a Marathi done-amount label also contains "काम पूर्ण"
    if STATUS.search(label):
        return "status"
    if VALUE_NOT.search(label):
        return "value_other"
    if VALUE_DONE.search(label):
        return "value_done"
    if DESC.search(label):
        return "description"
    if TENDER_NO.search(label) and not VALUE_TENDER.search(label):
        return "tender_no"
    if CONTRACTOR.search(label):
        return "contractor"
    if YEAR_COL.search(label):
        return "year"
    if PLACE.search(label):
        return "place"
    if VALUE_CONTRACT.search(label):
        return "value_contract"
    if VALUE_TENDER.search(label):
        return "value_tender"
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


@dataclass
class PageContext:
    """What a certificate says around its table: the year the works were done ("सन २०२३-२४ या
    कालावधी मध्ये"), the date it was issued, and whom it was issued to."""

    completed: bool = False  # the page is about completed work (a certificate, Form II)
    fy: int | None = None
    fy_certain: bool = True
    issued_year: int | None = None
    contractor: str | None = None


_PREAMBLE_FY = re.compile(r"सन\s*((?:19|20)\d{2})\s*[-–]\s*(\d{2,4})")
_ISSUED = re.compile(r"(?:दिनांक|दि\.?|Date)[^\n]{0,30}?(\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4})", re.I)
_ISSUED_TO = re.compile(r"(?:^|\s)(?:मे\.?|M/s\.?)\s*/?\s*(?P<n>[^\s,.:;|()][^,:;|()\n]{2,40}?)\s*(?:,|\(|पत्ता|राहणार|यांनी|यांचे|हे\s|Proprietor|$)", re.M)


def page_context(text: str) -> PageContext:
    t = ascii_digits(text or "")
    body = "\n".join(line for line in t.splitlines() if line.count("|") < 2)  # the prose, not the grid
    ctx = PageContext(completed=bool(COMPLETED_MARKER.search(t)))
    fys = sorted({int(m.group(1)) for m in _PREAMBLE_FY.finditer(body)})
    if fys:
        ctx.fy, ctx.fy_certain = fys[-1], len(fys) == 1
    m = _ISSUED.search(body[:500])
    if m:
        ctx.issued_year = _year_of(m.group(1))[0]
    m = _ISSUED_TO.search(body)
    if m:
        ctx.contractor = m.group("n").strip(" .-")
    return ctx


def in_progress(status: str) -> bool:
    """"काम चालू (७५% काम पूर्ण)", "1 ले रनिंग बिल", "70% completed": not a completed work, whatever
    else the certificate says. "काम पूर्ण" and "100 % completed" are."""
    t = ascii_digits(status or "")
    if IN_PROGRESS.search(t):
        return True
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", t)
    return bool(m and float(m.group(1)) < 100)


_TENDER_YEAR = re.compile(r"(?:(?:19|20)\d{2})")


_TENDER_NO_YEAR = re.compile(r"(?<![\d/])\d{1,4}\s*(?:Ex\.?)?\s*[-–/.]\s*((?:19|20)\d{2})(?!\d)|/((?:19|20)\d{2})/\d+", re.I)


def _tender_year(text: str) -> int | None:
    """A tender number carries the year it was let ("१२७-२०२२", "PMC/DRAINAGE/2025/50"): the work was
    finished in that year or later."""
    t = ascii_digits(text or "")
    m = _TENDER_NO_YEAR.search(t)
    if m:
        return int(m.group(1) or m.group(2))
    m = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", t)
    return int(m[-1]) if m else None


def _bounds(printed: str | None, year: int | None, certain: bool, floor: int | None,
            ceiling: int | None) -> tuple[int | None, int | None]:
    """(earliest, latest) calendar year the work can have been completed in."""
    if year is None:
        return floor, ceiling
    if printed and (printed.startswith("सन") or re.fullmatch(r"\s*(?:19|20)\d{2}\s*[-–]\s*\d{2,4}\s*", ascii_digits(printed))
                    or _SHORT_FY.fullmatch(ascii_digits(printed).strip())):
        return year, year + 1  # a financial year runs April to March
    if certain:
        return year, year
    lo = floor if floor and (ceiling is None or floor <= ceiling) else None
    hi = ceiling if ceiling and (floor is None or ceiling >= floor) else None
    return lo, hi


def _pick_value(cells: list[str], header: list[str], by_role: dict[str, list[int]],
                extra_done: list[float]) -> tuple[float | None, str | None, list, list[str]]:
    """Precedence: what the work came to > what the contract was let for > the tender amount when a
    certificate states nothing else. The estimate is never the value of the work."""
    issues: list[str] = []
    others: list[tuple[str, float]] = []
    chosen: tuple[float, str, str] | None = None  # value, label, role
    for role in ("value_done", "value_contract", "value_tender"):
        for j in by_role.get(role, []):
            if j >= len(cells):
                continue
            found = _amounts(cells[j])
            label = ((header[j] if j < len(header) else "") or role).strip()
            if len(found) > 1:
                issues.append(f"more than one figure in the {label!r} cell: {cells[j][:60]!r}")
                continue
            if not found:
                if re.search(r"\d", ascii_digits(cells[j])) and len(cells[j]) > 12:
                    issues.append(f"the {label!r} cell could not be read: {cells[j][:60]!r}")
                continue
            if chosen is None:
                chosen = (found[0], label, role)
            else:
                others.append((label, found[0]))
        if role == "value_done" and chosen is None and extra_done:
            chosen = (extra_done[0], "काम पूर्ण रक्कम (शेरा)", role)
        if chosen is not None:
            break
        if role == "value_contract" and by_role.get("value_contract") and issues:
            # The certificate states the accepted amount but it cannot be read. The tender amount
            # beside it is a different figure (often the estimate): no value rather than that one.
            break
    if chosen and chosen[2] == "value_tender":
        issues.append("only the tender amount is stated; it is taken as the value of the contract")
    return (chosen[0] if chosen else None), (chosen[1] if chosen else None), others, issues


def _from_table(rows: list[list[str]], page: int, ctx: PageContext | None = None) -> list[WorkRecord]:
    rows = _unwrap_rows(rows)
    if not rows:
        return []
    ctx = ctx or PageContext()
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
        #
        # Marathi work certificates (कामाचा दाखला) say it differently: a remarks column reading
        # "काम पूर्ण" (or a year column), on a page that certifies the works as completed.
        has_value = any(r.startswith("value") and r != "value_other" for r in kinds)
        dated = bool(kinds & {"date_actual", "date_stipulated"})
        certified = ctx.completed and bool(kinds & {"status", "year"})
        if "description" in kinds and has_value and (dated or certified):
            head_idx, roles = i, found
            break
    if head_idx is None:
        return []

    header = [(c or "").strip() for c in rows[head_idx]]
    out: list[WorkRecord] = []
    for row in rows[head_idx + 1:]:
        cells = [(c or "").strip() for c in row]
        by_role = _align(rows[head_idx], cells, roles)
        desc_cols = by_role.get("description", [])
        desc = next((cells[j] for j in desc_cols if j < len(cells) and len(cells[j]) > 8), "")
        if not desc:
            continue
        status = " ".join(cells[j] for j in by_role.get("status", []) if j < len(cells))
        if status and in_progress(status):
            continue  # "काम चालू": a certificate for work still running is not a completed work
        extra_done = _amounts(status) if status else []
        value, label, others, issues = _pick_value(cells, header, by_role, extra_done)
        year, printed, certain = None, None, True
        actual_cols = by_role.get("date_actual", [])
        stip_cols = by_role.get("date_stipulated", [])
        for j in actual_cols or stip_cols:
            if j < len(cells):
                year, printed, certain = _year_of(cells[j])
                if year:
                    break
        if not actual_cols and stip_cols and year:
            issues.append("completion date taken from the stipulated column; no actual date was stated")
        if year is None:
            for j in by_role.get("year", []):
                if j < len(cells):
                    year, printed, certain = _year_of(cells[j])
                    if year:
                        break
        if year is None and ctx.fy:
            year, printed, certain = ctx.fy, f"सन {ctx.fy}-{str(ctx.fy + 1)[-2:]}", ctx.fy_certain
        tender_cells = " ".join(cells[j] for j in by_role.get("tender_no", []) if j < len(cells))
        ty = _tender_year(tender_cells) or next((y for y in (_tender_year(c) for c in cells if _TENDER_NO_YEAR.search(ascii_digits(c))) if y), None)
        if year is None and ty:
            # A lower bound only (the year the work was tendered): never used to exclude a work.
            year, printed, certain = ty, f"tendered {ty}", False
        if year is None and ctx.issued_year and not (ty and ctx.issued_year < ty):
            year, printed, certain = ctx.issued_year, f"certificate issued {ctx.issued_year}", False
        lo, hi = _bounds(printed, year, certain, ty, ctx.issued_year)
        out.append(WorkRecord(description=re.sub(r"\s+", " ", desc)[:300], value=value, value_label=label,
                              year=year, date=printed, page=page,
                              quote=" | ".join(c for c in cells if c)[:400], shape="statement_table",
                              year_certain=certain, other_amounts=others, issues=issues,
                              contractor=ctx.contractor, tender_no=tender_cells or None, year_min=lo, year_max=hi))
    return out


# --------------------------------------------------------------------------------------------
# Key-value certificates: "१ | कामाचे नाव | …", "२ | टेंडर क्र. | …", … "८ काम पूर्ण रकम रुपये | …",
# often running over two pages. One certificate, one work.
# --------------------------------------------------------------------------------------------

_KV_LINE = re.compile(r"^\s*(?:[\d०-९]{1,2}\s*[.)|]?\s*)?(?P<label>[^|\d०-९][^|]{2,90}?)\s*(?:\||:|-)\s*(?P<value>.+?)\s*$")


class _Pair(NamedTuple):
    label: str
    value: str
    source: str  # the row or line as printed: what a quote reproduces


def _kv_pairs(page) -> list[_Pair]:
    """(label, value) pairs of one page, from its 2–3 cell table rows and from its lines."""
    pairs: list[_Pair] = []
    for rows in page.tables:
        for row in rows:
            printed = [(c or "").strip() for c in row if (c or "").strip()]
            cells = printed[1:] if len(printed) >= 2 and re.fullmatch(r"[\d०-९]{1,2}\.?", printed[0]) else printed
            if len(cells) == 2 and not _amounts(cells[0]):
                pairs.append(_Pair(cells[0], cells[1], " | ".join(printed)))
    lines = [ln.strip() for ln in (page.text or "").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if line.count("|") >= 3:
            continue
        m = _KV_LINE.match(line)
        if m and (_role_of(m.group("label")) or re.search(r"^\s*बिल\s*$|bill", m.group("label"), re.I)):
            pairs.append(_Pair(m.group("label"), m.group("value"), line))
            continue
        # "८ काम पूर्ण रकम रुपये" on one line, "१८,४६,३२०.५०" on the next
        label = re.sub(r"^\s*[\d०-९]{1,2}\s*[.)]?\s*", "", line)
        if i + 1 < len(lines) and _role_of(label) and not _amounts(label) and len(label) < 90:
            pairs.append(_Pair(label, lines[i + 1], f"{line} {lines[i + 1]}"))
    return pairs


# What a key-value certificate's quote must show, most important first: the amounts and completion
# decide the verdict; the work's name and tender number identify it.
_QUOTE_PRIORITY = ("value_done", "value_contract", "value_tender", "date_actual", "status", "description", "tender_no")
_QUOTE_BUDGET = 600


def _kv_quote(pairs: list[_Pair]) -> str:
    """The certificate's own rows for the facts that decided it, each once (a row is often read both
    from the table and from the text), whole rows in page order, within the budget."""
    facts: list[tuple[int, str, str]] = []  # (position, role, source)
    seen: list[tuple[str, str]] = []  # (value, source), whitespace removed
    for i, pair in enumerate(pairs):
        role = _role_of(pair.label)
        if role not in _QUOTE_PRIORITY:
            continue
        if (role.startswith("value_") and not _amounts(pair.value)) or (role == "date_actual" and not _year_of(pair.value)[0]):
            continue  # a label over the quantity table, not a stated amount or date
        key = re.sub(r"\s+", "", pair.value)
        if any(key == v or (len(key) >= 20 and key[:60] in src) for v, src in seen):
            continue
        seen.append((key, re.sub(r"\s+", "", pair.source)))
        facts.append((i, role, pair.source))
    chosen, used = [], 0
    for i, _, source in sorted(facts, key=lambda f: _QUOTE_PRIORITY.index(f[1])):
        part = source if len(source) <= 160 else source[:160].rstrip() + "…"
        if used + len(part) <= _QUOTE_BUDGET:
            chosen.append((i, part))
            used += len(part) + 3
    return " … ".join(part for _, part in sorted(chosen)) or " … ".join(x.source for x in pairs[:6])[:_QUOTE_BUDGET]


def _from_kv(pairs: list[_Pair], page: int, ctx: PageContext) -> list[WorkRecord]:
    fields: dict[str, list[str]] = {}
    for label, value, _ in pairs:
        role = _role_of(label)
        if role:
            fields.setdefault(role, []).append(value)
    desc = next((v for v in fields.get("description", []) if len(v) > 8), "")
    if not desc:
        return []
    status = " ".join(fields.get("status", []))
    if status and in_progress(status):
        return []
    # A certificate for a running bill ("१ ले रनिंग बिल", "बिल कालावधी") certifies part of a work.
    bill = " ".join(v for label, v, _ in pairs if re.search(r"^\s*बिल\s*$|bill\s*no", label, re.I))
    if bill and in_progress(bill):
        return []
    value, label, issues = None, None, []
    for role in ("value_done", "value_contract", "value_tender"):
        for v in fields.get(role, []):
            found = _amounts(v)
            if found:
                value, label = found[0], role
                break
        if value is not None:
            break
    if label == "value_tender":
        issues.append("only the tender amount is stated; it is taken as the value of the contract")
    year, printed, certain = None, None, True
    for v in fields.get("date_actual", []):
        year, printed, certain = _year_of(v)
        if year:
            break
    if year is None and ctx.fy:
        year, printed, certain = ctx.fy, f"सन {ctx.fy}", ctx.fy_certain
    tender = " ".join(fields.get("tender_no", []))
    if year is None:
        started = " ".join(fields.get("date_started", []))
        lower = _year_of(started)[0] or _tender_year(tender)
        if lower:
            year, printed, certain = lower, f"started/tendered {lower}", False
    lower = _year_of(" ".join(fields.get("date_started", [])))[0] or _tender_year(tender)
    if year is None and ctx.issued_year and not (lower and ctx.issued_year < lower):
        year, printed, certain = ctx.issued_year, f"certificate issued {ctx.issued_year}", False
    lo, hi = _bounds(printed, year, certain, lower, ctx.issued_year)
    contractor = (fields.get("contractor") or [ctx.contractor])[0]
    return [WorkRecord(description=re.sub(r"\s+", " ", desc)[:300], value=value,
                       value_label={"value_done": "काम पूर्ण रक्कम", "value_contract": "निविदा (मान्य) रक्कम",
                                    "value_tender": "टेंडर रक्कम"}.get(label or ""),
                       year=year, date=printed, page=page, quote=_kv_quote(pairs), shape="certificate",
                       year_certain=certain, issues=issues, contractor=contractor, tender_no=tender or None,
                       year_min=lo, year_max=hi)]


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
    parsed = [p for p in doc.pages if (pages is None or p.number in pages)]
    by_number = {p.number: p for p in doc.pages}
    for p in parsed:
        text = p.text or ""
        if not text.strip():
            continue
        # A page whose text layer is not trustworthy states no figure worth citing. Saying nothing
        # sends it to OCR; parsing it anyway puts "2t7.t2" in front of a committee as a number.
        if not p.quality.quotable:
            continue
        if NOT_WORK_PROOF.search(text):
            continue  # an ITR, a registration receipt, a bank page: money and dates, no work
        ctx = page_context(text)
        for rows in p.tables:
            out += _from_table(rows, p.number, ctx)
        if not any(r.page == p.number for r in out):
            pairs = _kv_pairs(p)
            if pairs and any(_role_of(x.label) == "description" for x in pairs):
                # The certificate's amounts and status often sit on the next page.
                nxt = by_number.get(p.number + 1)
                if nxt is not None and nxt.quality.quotable and (nxt.text or "").strip():
                    more = _kv_pairs(nxt)
                    if not any(_role_of(x.label) == "description" for x in more):
                        pairs += more
                        if not ctx.completed and COMPLETED_MARKER.search(nxt.text or ""):
                            ctx.completed = True
                if ctx.completed:
                    out += _from_kv(pairs, p.number, ctx)
                continue  # a key-value certificate is read by its labels only, or not at all
        if re.search(r"[\u0900-\u097F]{20}", text) and not any(r.page == p.number for r in out):
            continue  # the English line reader below would take a Marathi row for a label
        if not any(r.page == p.number for r in out):
            out += _from_certificate(text.splitlines(), p.number)
        if not any(r.page == p.number for r in out):
            for s in doc.sentences:
                if s.page == p.number:
                    out += _from_sentence(s.text, p.number)

    # One record per (value, description): a certificate is often summarised in a statement, and
    # bidders routinely include the same certificate twice.
    seen, unique = set(), []
    for r in out:
        key = (r.value, re.sub(r"\s+", "", r.description)[:60].lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    records = [r for r in unique if r.usable]
    _mark_foreign(records)
    return records


# --------------------------------------------------------------------------------------------
# Whose work is it?
# --------------------------------------------------------------------------------------------

_MATRA = re.compile(r"[\u093E-\u094D\u0900-\u0903\u093C\u0951-\u0957]")  # vowel signs, anusvara, nukta, halant
_SPELLING = str.maketrans({"झ": "ज", "ण": "न", "ळ": "ल", "श": "स", "ष": "स", "ऑ": "अ"})
_TITLES = re.compile(r"^(?:मे|मेसर्स|श्री|m/?s|messrs|mr|shri)\b\.?\s*", re.I)


def name_key(name: str) -> str:
    """A spelling-tolerant key for a firm's name as Marathi and English certificates print it:
    "एस. बी. एंटरप्रायजेस" and "एस.बी. एन्टरप्रायझेस" are the same firm."""
    n = _TITLES.sub("", (name or "").strip()).lower()
    n = _MATRA.sub("", n.translate(_SPELLING))
    return re.sub(r"[^a-z0-9\u0900-\u097F]", "", n)


def _mark_foreign(records: list[WorkRecord]) -> None:
    """A certificate issued to another contractor is not this bidder's experience. The firm named on
    most of the bid's certificates is taken as the bidder; one in a clearly different name (same
    script, little in common) is flagged, never silently counted."""
    keys = [(r, name_key(r.contractor)) for r in records if r.contractor]
    keys = [(r, k) for r, k in keys if len(k) >= 3]
    if len(keys) < 2:
        return
    groups: list[list] = []
    for r, k in keys:
        for g in groups:
            if SequenceMatcher(None, k, g[0][1]).ratio() >= 0.6:
                g.append((r, k))
                break
        else:
            groups.append([(r, k)])
    groups.sort(key=len, reverse=True)
    main = groups[0][0][1]
    main_script_dev = bool(re.search(r"[\u0900-\u097F]", main))
    for g in groups[1:]:
        for r, k in g:
            same_script = bool(re.search(r"[\u0900-\u097F]", k)) == main_script_dev
            if same_script and SequenceMatcher(None, k, main).ratio() < 0.5:
                r.foreign = True
                r.issues.append(f"issued to {r.contractor!r}, not to the firm named on the bid's other certificates")
