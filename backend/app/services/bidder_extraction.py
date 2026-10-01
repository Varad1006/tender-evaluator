"""Bidder-side extraction (FR2/FR3): pull each bidder's claims from its own submission.

There is no company profile to fall back on: every value comes from the bidder's PDF, with the
page and verbatim sentence it was found in. A finder returns *every* candidate sentence, not just
the first, so contradictions inside one submission stay visible to the verdict engine.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.adapters.llm import LLMError
from app.adapters.pdf_parser import ParsedDocument, Sentence, quote_on_page
from app.services.year_tables import (
    DERIVED_LABEL,
    TURNOVER_LABEL,
    WORK_DONE_LABEL,
    cell_amount,
    read_year_series,
)
from app.services.values import (
    GSTIN,
    format_inr,
    format_inr_short,
    parse_amounts,
    parse_durations,
    parse_financial_years,
    parse_years,
)
from app.services.values import corrupt_amounts  # noqa: E402  (grouped with the other value helpers)

log = logging.getLogger(__name__)


@dataclass
class Claim:
    criteria_key: str
    value: str
    page: int
    quote: str
    section: str
    value_numeric: float | None = None
    tier: str = "deterministic"
    detail: dict = field(default_factory=dict)


@dataclass
class CriterionSpec:
    """The slice of a registry row the finders need (decoupled from the ORM)."""

    key: str
    label: str
    operator: str
    params: dict
    threshold_value: float | None = None
    threshold_unit: str | None = None
    extraction_method: str = "deterministic"
    # Tender facts the check needs beyond its own threshold: estimated cost, completion period,
    # reference number (an EMD receipt must be for this tender; a bid capacity for its period).
    tender: dict = field(default_factory=dict)


@dataclass
class BidderExtraction:
    bidder_name: str
    claims: list[Claim]
    warnings: list[str] = field(default_factory=list)


def _claim(key: str, s: Sentence, value: str, numeric: float | None = None, **detail) -> Claim:
    return Claim(criteria_key=key, value=value, value_numeric=numeric, page=s.page, quote=s.text,
                 section=s.section, detail=detail)


# --------------------------------------------------------------------------------------
# Finders: (doc, spec) -> list[Claim]
# --------------------------------------------------------------------------------------

def find_turnover(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    candidates = [s for s in doc.sentences if "turnover" in s.text.lower() and parse_amounts(s.text)]
    if spec.params.get("average"):
        # Year-wise listings are a different claim from the stated average; prefer the latter.
        averages = [s for s in candidates if "average" in s.text.lower()]
        candidates = averages or candidates
    out = []
    for s in candidates:
        amount = parse_amounts(s.text)[0].value
        out.append(_claim(spec.key, s, format_inr(amount), amount))
    return out


# A chartered accountant's turnover certificate lists one figure per financial year, as a ruled
# table ("2020-2021 | 3,43,19,907/-"), a bulleted list ("Financial Year 2019-20 Rs. 1,87,87,157/-")
# or a numbered one ("1 | 2022-23 | 0"). Each year is its own claim, because the tender asks for the
# maximum over five years and that maximum is decided only after the year's escalation factor is
# applied — an older, smaller year routinely wins.
_TURNOVER_CONTEXT = re.compile(r"turn\s?over", re.IGNORECASE)


def find_turnover_series(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """Year-stamped turnover figures, so the verdict can index them to current price level."""
    pages = {s.page for s in doc.sentences if _TURNOVER_CONTEXT.search(s.text)}
    pages |= {p.number for p in doc.pages if _TURNOVER_CONTEXT.search(p.text or "")}
    if not pages:
        return []
    out: list[Claim] = []
    seen: set[tuple[int, float]] = set()

    # Most certificates state the series as a grid, where the year and the amount are in different
    # cells and the currency is only in the column header — so neither a sentence scan nor
    # `parse_amounts` sees a thing.
    types = getattr(doc, "page_types", None) or []
    for page in sorted(pages):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        if p is None or not p.tables:
            continue
        # On the CA's turnover certificate itself every year-and-amount row is the turnover, whatever
        # the column is called ("Total contract amount received") or if it is not called anything.
        own = page - 1 < len(types) and types[page - 1] == "turnover_certificate"
        for yv in read_year_series(p.tables, want=None if own else TURNOVER_LABEL, avoid=DERIVED_LABEL):
            if (yv.year, yv.value) in seen:
                continue
            seen.add((yv.year, yv.value))
            s = Sentence(page=page, text=f"{yv.label or 'Turnover'} — FY {yv.year}-{str(yv.year + 1)[-2:]}: {yv.cell}",
                         section=yv.label or "Turnover certificate", in_table=True)
            out.append(_claim(spec.key, s, f"FY {yv.year}-{str(yv.year + 1)[-2:]}: {format_inr(yv.value)}",
                              yv.value, year=yv.year, source="table"))

    for s in doc.sentences:
        if s.page not in pages:
            continue
        # A row of a bid-capacity working is not a turnover claim even on the same page: it states
        # escalated values, and reading those as the bidder's turnover double-counts the factor.
        if re.search(r"updat|factor|bid\s*capacity|work\s*in\s*hand", s.text, re.IGNORECASE):
            continue
        years = parse_financial_years(s.text)
        amounts = parse_amounts(s.text)
        if len(years) != 1:
            continue
        year = years[0]
        if not amounts:
            # "2022-23 | 0" — a stated nil year is evidence, and dropping it would let the bidder
            # look as though they never reported that year at all.
            if re.search(r"(?<![\d,.])0(?![\d,.])", s.text) and len(s.text) < 60:
                key = (year, 0.0)
                if key not in seen:
                    seen.add(key)
                    out.append(_claim(spec.key, s, f"FY {year}-{str(year + 1)[-2:]}: nil", 0.0, year=year))
            continue
        amount = amounts[0].value
        if (year, amount) in seen:
            continue
        seen.add((year, amount))
        out.append(_claim(spec.key, s, f"FY {year}-{str(year + 1)[-2:]}: {format_inr(amount)}", amount, year=year))

    # If figures on the same certificate were refused as corrupt, the years we did read are not the
    # whole series — and a maximum taken over part of a series is not the maximum. One bidder's
    # certificate states 0 for its first year and two crore-scale years the text layer mangled;
    # without this, the readable nil becomes their turnover and fails them.
    for page in sorted(pages):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        bad = corrupt_amounts(p.text or "") if p else []
        if not bad:
            continue
        for c in out:
            if c.page == page:
                c.detail["series_incomplete"] = True
                c.detail["unreadable"] = bad[:4]
    return out


# The bid-capacity certificate is the one piece of bidder evidence that is itself a calculation, so
# it can disagree with the tender in ways a plain figure cannot: a different formula, a different
# completion period, a different price-level base, or an age that puts it out of validity. Two of
# the nine Warje bidders' certificates are computed on the PWD formula (A x N x 2 - B) and addressed
# to the Public Works Department, with an explicit "shall not be used for any other purpose".
_BID_CAPACITY_CONTEXT = re.compile(r"bid(?:ding)?\s*capacity", re.IGNORECASE)
_BC_TOTAL = re.compile(
    r"(?:total\s*bid\s*capacity|assessed\s*available\s*bid\s*capacity|bid\s*capacity)"
    r"[^\n\d]{0,60}?(?P<amt>(?:rs\.?|inr|₹)?\s*\d[\d,\s]*(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?)?)",
    re.IGNORECASE)
_BC_FORMULA = re.compile(r"\(?\s*A\s*[*x×]\s*N\s*[*x×]\s*(?P<k>[\d.]+)\s*\)?\s*[-–]\s*B", re.IGNORECASE)
_BC_FORMULA_ALT = re.compile(r"\(?\s*A\s*[+*x×]\s*N\s*[*x×]\s*(?P<k>[\d.]+)\s*[-–]\s*B\s*\)?", re.IGNORECASE)
_UDIN = re.compile(r"\bUDIN\b\s*[:\-]?\s*([0-9A-Z]{10,25})", re.IGNORECASE)
_TENTATIVE = re.compile(r"\btentative\b|\bprovisional\b|\bestimated\s+bid\s+capacity\b", re.IGNORECASE)
_N_VALUE = re.compile(r"\bN\s*[=:]\s*(?P<n>[\d.]+)\s*(?P<unit>year|yr|month|mth)", re.IGNORECASE)


def find_bid_capacity(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """The bidder's assessed bid capacity, with whatever makes it non-comparable recorded on it."""
    pages = sorted({s.page for s in doc.sentences if _BID_CAPACITY_CONTEXT.search(s.text)})
    if not pages:
        return []
    want_k = float(spec.params.get("formula_multiplier") or 1.5)
    want_years = spec.params.get("completion_years")
    out: list[Claim] = []
    for page in pages:
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        # A page's text keeps table rows intact ("Total Bid Capacity (In Rs.) | 6,78,75,525"),
        # where the label and the figure are separate cells and so separate sentences.
        lines = (p.text or "").splitlines() if p else [s.text for s in doc.sentences if s.page == page]
        blob = " ".join(lines)
        total, quote = None, None
        for line in lines:
            if not _BC_TOTAL.search(line):
                continue
            amounts = parse_amounts(line)
            cells = [cell_amount(c) for c in line.split("|")]
            value = next((c for c in reversed(cells) if c is not None), None)
            if value is None and amounts:
                value = amounts[-1].value
            if value is not None:
                # A certificate states its conclusion last; earlier lines are the working.
                total, quote = value, line.strip()
        if total is None:
            continue
        best = Sentence(page=page, text=quote[:400], section="Bid capacity certificate", in_table=True)

        defects: list[str] = []
        fm = _BC_FORMULA.search(blob) or _BC_FORMULA_ALT.search(blob)
        if fm:
            k = float(fm.group("k"))
            if abs(k - want_k) > 1e-6:
                defects.append(f"computed as (A × N × {k:g}) − B, but this tender prescribes "
                               f"(A × N × {want_k:g}) − B")
        nm = _N_VALUE.search(blob)
        if nm:
            n = float(nm.group("n"))
            if nm.group("unit").lower().startswith(("month", "mth")):
                n /= 12
            if want_years and abs(n - want_years) > 0.05:
                defects.append(f"uses a completion period of {n:g} year(s); this tender's is {want_years:g}")
        if _TENTATIVE.search(blob):
            defects.append("is marked tentative, which the tender does not accept")
        if not _UDIN.search(blob):
            defects.append("carries no UDIN for the chartered accountant")
        other = re.search(r"(?:issued|given)\s+specifically\s+to\s+([A-Z][^.\n]{4,60}?)\s*(?:and|\.|$)", blob)
        if other and not re.search(r"pune\s+municipal|PMC\b", other.group(1), re.IGNORECASE):
            defects.append(f"is addressed specifically to {other.group(1).strip()} and states it is "
                           "not to be used for any other purpose")

        value = f"{format_inr(total)}"
        out.append(_claim(spec.key, best, value + (" — not comparable" if defects else ""), total,
                          defects=defects, not_comparable=bool(defects), udin=bool(_UDIN.search(blob))))
    return out


def find_completed_works_claims(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """The bidder's completed works, for the tender's "3 of these, or 2 of those" ladder.

    The nature of work the tender asks for is carried in on the criterion, so the description each
    record was read from can be matched against it here and judged properly in the verdict engine.
    """
    from app.services.criteria_extraction import GENERIC_NATURE_WORDS
    from app.services.work_proof import find_completed_works

    nature = str(spec.params.get("nature") or "")
    terms = [w for w in re.split(r"[^\w]+", nature.lower())
             if len(w) > 2 and w not in GENERIC_NATURE_WORDS]
    out: list[Claim] = []
    for r in find_completed_works(doc):
        hits = [t for t in terms if t in r.description.lower()]
        s = Sentence(page=r.page, text=r.quote, section=r.shape, in_table=r.shape == "statement_table")
        out.append(_claim(
            spec.key, s, f"{format_inr_short(r.value)} ({r.date or r.year or 'year not stated'})", r.value,
            year=r.year, year_certain=r.year_certain, year_min=r.year_min, year_max=r.year_max, year_note=r.date,
            nature_terms=terms, nature_hits=hits, description=r.description, shape=r.shape,
            value_label=r.value_label, other_amounts=r.other_amounts, issues=r.issues,
            contractor=r.contractor, foreign=r.foreign, tender_no=r.tender_no,
        ))
    return out


def find_experience(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    terms = spec.params.get("nature_terms", [])
    out = []
    for s in doc.sentences:
        t = s.text.lower()
        if not re.search(r"\b(completed|executed|commissioned|installed|delivered)\b", t):
            continue
        amounts, years = parse_amounts(s.text), parse_years(s.text)
        if not amounts or not years:
            continue
        amount, year = amounts[0].value, max(years)
        hits = [term for term in terms if term in t]
        out.append(_claim(spec.key, s, f"{format_inr_short(amount)} ({year})", amount,
                          year=year, nature_hits=hits, nature_terms=terms))
    return out


def find_gst(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out = []
    for s in doc.sentences:
        if not re.search(r"\bGST", s.text):
            continue
        gstin = GSTIN.search(s.text)
        documented = re.search(r"certificate|registration", s.text, re.I) and re.search(r"enclosed|attached|annexure|submitted", s.text, re.I)
        if gstin or documented:
            out.append(_claim(spec.key, s, f"GSTIN {gstin.group(0)}" if gstin else "Certificate enclosed"))
    return out


_REG_NO = re.compile(r"(?:No\.?|Number|Code)\s*:?\s*([A-Z0-9][A-Z0-9/\-]{4,})")


def find_labour_registrations(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    from app.services.criteria_extraction import LABOUR_PARTS

    out = []
    for part in spec.params.get("parts", []):
        pattern = LABOUR_PARTS[part]
        for s in doc.sentences:
            m = re.search(pattern, s.text, re.IGNORECASE)
            if not m or not re.search(r"registration|reg\.?\s*no|code|certificate|enclosed", s.text, re.IGNORECASE):
                continue
            reg = _REG_NO.search(s.text, m.end())
            # Don't attribute the *other* scheme's number to this part.
            other = [p for p in LABOUR_PARTS if p != part]
            if reg and any(re.search(LABOUR_PARTS[o], s.text[m.end():reg.start()], re.I) for o in other):
                reg = None
            value = f"{part} No. {reg.group(1)}" if reg else f"{part} certificate enclosed"
            out.append(_claim(spec.key, s, value, part=part))
    return out


def find_manufacturer(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out = []
    for s in doc.sentences:
        if not re.search(r"authori[sz]ation|\bMAF\b", s.text, re.IGNORECASE) or "manufacturer" not in s.text.lower():
            continue
        name = re.search(r"(?:manufacturer|from)\s+([A-Z][\w&.]*(?:\s+[A-Z][\w&.]*){0,5})", s.text)
        out.append(_claim(spec.key, s, name.group(1) if name else "Authorization enclosed"))
    return out


def find_warranty(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out = []
    for s in doc.sentences:
        if "warranty" not in s.text.lower():
            continue
        years = parse_durations(s.text, unit="years")
        if years:
            out.append(_claim(spec.key, s, f"{years[0].value} years", years[0].value))
    return out


def find_completion(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out = []
    for s in doc.sentences:
        t = s.text.lower()
        if "within" not in t or not re.search(r"commission|complet|deliver|execut|install", t):
            continue
        days = parse_durations(s.text, unit="days")
        if days:
            out.append(_claim(spec.key, s, f"{days[0].value} days", days[0].value))
    return out


def find_site_engineer(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    discipline = spec.params.get("discipline", "")
    out = []
    for s in doc.sentences:
        t = s.text.lower()
        if "engineer" not in t or (discipline and f"{discipline} engineer" not in t):
            continue
        if not re.search(r"certified|licen[cs]e|qualified|registered|chartered", t) or not re.search(r"deploy|site|remain|station", t):
            continue
        person = re.search(r"\b(?:Mr|Ms|Mrs|Dr)\.\s+([A-Z]\w+(?:\s+[A-Z]\w+)?)", s.text)
        licence = re.search(r"Licen[cs]e No\.?\s*([A-Z0-9/\-]+)", s.text)
        value = person.group(1) if person else "Engineer committed"
        if licence:
            value += f" (Licence {licence.group(1)})"
        out.append(_claim(spec.key, s, value))
    return out


def find_generic_document(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    terms = [t.lower() for t in spec.params.get("terms", []) if t]
    if not terms:
        return []
    out = []
    for s in doc.sentences:
        t = s.text.lower()
        if all(term in t for term in terms) and re.search(r"enclosed|attached|annexure|certificate|registration|no\.", t):
            out.append(_claim(spec.key, s, "Document enclosed"))
    return out


FINDERS: dict[str, Callable[[ParsedDocument, CriterionSpec], list[Claim]]] = {
    "avg_turnover": find_turnover,
    "pmc_max_turnover": find_turnover_series,
    "pmc_bid_capacity": find_bid_capacity,
    "pmc_similar_works": find_completed_works_claims,
    "min_similar_projects": find_completed_works_claims,
    "similar_experience": find_experience,
    "gst_registration": find_gst,
    "epf_esic_registration": find_labour_registrations,
    "manufacturer_authorization": find_manufacturer,
    "warranty_years": find_warranty,
    "completion_days": find_completion,
    "site_engineer": find_site_engineer,
}


# --------------------------------------------------------------------------------------
# Price and identity (not criteria, but needed for ranking and display)
# --------------------------------------------------------------------------------------

def find_quoted_price(doc: ParsedDocument) -> list[Claim]:
    out = []
    for s in doc.sentences:
        t = s.text.lower()
        if not re.search(r"quoted|bid amount|bid value|total price|price quoted", t):
            continue
        amounts = parse_amounts(s.text)
        if amounts and re.search(r"\btotal\b|quoted", t):
            out.append(_claim("quoted_price", s, format_inr(amounts[0].value), amounts[0].value))
    return out


def find_bidder_name(doc: ParsedDocument, filename: str) -> str:
    for page in doc.pages[:2]:
        for line in page.text.split("\n"):
            m = re.match(r"\s*(?:Bidder|Name of (?:the )?Bidder|Submitted by|Name of (?:the )?Firm)\s*:\s*(.+)", line, re.I)
            if m:
                return m.group(1).strip().rstrip(".")
    stem = Path(filename).stem
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Unnamed bidder"


# --------------------------------------------------------------------------------------
# LLM evidence search for criteria the deterministic finders don't know (LLM-extracted ones)
# --------------------------------------------------------------------------------------

LLM_EVIDENCE_PROMPT = """A tender requires: "{label}" ({requirement}).
Find every sentence in this bidder's submission that states the bidder's claim for this requirement.
Return a JSON array; each item: {{"page": page number, "quote": exact sentence copied verbatim,
"value": short value as stated, "value_numeric": number or null}}. Return [] if absent.

BIDDER SUBMISSION:
{pages}"""


def llm_find_evidence(llm, doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    from app.services.criteria_extraction import _pages_block

    prompt = LLM_EVIDENCE_PROMPT.format(
        label=spec.label, requirement=f"{spec.operator} {spec.threshold_value or ''} {spec.threshold_unit or ''}".strip(),
        pages=_pages_block(doc),
    )
    try:
        items = llm.generate_json("extraction_default", prompt)
    except LLMError as exc:
        log.warning("LLM evidence search failed for %s: %s", spec.key, exc)
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        try:
            page, quote = int(item["page"]), str(item["quote"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not quote_on_page(quote, doc, page):
            continue  # never cite a sentence we cannot find on that page
        numeric = item.get("value_numeric")
        section = next((s.section for s in doc.sentences if s.page == page), "")
        out.append(Claim(criteria_key=spec.key, value=str(item.get("value") or "Stated")[:120], page=page,
                         quote=quote, section=section, tier="llm",
                         value_numeric=float(numeric) if isinstance(numeric, (int, float)) else None))
    return out


def extract_bidder(doc: ParsedDocument, specs: list[CriterionSpec], filename: str, llm=None) -> BidderExtraction:
    claims: list[Claim] = []
    warnings: list[str] = []
    for spec in specs:
        finder = FINDERS.get(spec.key) or (find_generic_document if spec.key.startswith("doc_") else None)
        try:
            if finder:
                claims.extend(finder(doc, spec))
            elif llm is not None:
                claims.extend(llm_find_evidence(llm, doc, spec))
            elif spec.params.get("terms"):
                claims.extend(find_generic_document(doc, spec))
        except Exception as exc:  # one criterion's failure must never block the others (NFR6)
            log.exception("Extraction failed for %s", spec.key)
            warnings.append(f"Extraction failed for '{spec.label}': {exc}")
    claims.extend(find_quoted_price(doc))

    seen, unique = set(), []
    for c in claims:
        k = (c.criteria_key, c.page, c.quote, c.detail.get("part"))
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return BidderExtraction(bidder_name=find_bidder_name(doc, filename), claims=unique, warnings=warnings)


# PMC works criteria read from a bid's documents (EMD receipt, registration, bid-capacity working,
# firm constitution, executed quantities) register themselves into FINDERS when this module loads.
import app.services.bidder_evidence  # noqa: E402,F401
