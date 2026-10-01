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
    grid_from_lines,
    TURNOVER_LABEL,
    WORK_DONE_LABEL,
    cell_amount,
    read_year_series,
)
from app.services.values import (
    GSTIN,
    Amount,
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
    # The clause as the tender printed it. A finder is chosen from this rather than from `key`,
    # because the key is a name this system invented and the clause is what the tender said.
    requirement: str = ""
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
# table ("2022-2023 | 1,23,45,000/-"), a bulleted list ("Financial Year 2021-22 Rs. 98,76,500/-")
# or a numbered one ("1 | 2023-24 | 0"). Each year is its own claim, because the tender asks for the
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
        tables = _tables_of(p) if p is not None else []
        if not tables:
            continue
        # On the CA's turnover certificate itself every year-and-amount row is the turnover, whatever
        # the column is called ("Total contract amount received") or if it is not called anything.
        own = page - 1 < len(types) and types[page - 1] == "turnover_certificate"
        for yv in read_year_series(tables, want=None if own else TURNOVER_LABEL, avoid=DERIVED_LABEL):
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
# completion period, a different price-level base, or an age that puts it out of validity. Some
# certificates are computed on the PWD formula (A x N x 2 - B) and addressed to the Public Works
# Department, with an explicit statement that they may not be used for any other purpose.
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
        # A page's text keeps table rows intact ("Total Bid Capacity (In Rs.) | 5,43,21,000"),
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


# A finder indexed by concept rather than by criterion key. The keys this system gives criteria are
# generated from the clause it read them in, so the same requirement arrives as `avg_turnover` in
# one tender, `positive_net_worth` in another and `min_staff_strength` in a third — and a registry
# keyed by name finds a finder for none of them on a tender nobody has seen yet. The concept is
# stable: whatever the clause is called, it is still asking about turnover.
CONCEPT_FINDERS: dict[str, Callable[[ParsedDocument, CriterionSpec], list[Claim]]] = {}


def _finder_for(spec: CriterionSpec):
    """The finder for a criterion: by key where one is registered, else by what the clause is about.

    A concept finder reads a *figure*, so it is only used for a criterion that measures one. "Turn
    Over and Net Worth Certificate" in a list of documents to enclose resolves to the turnover
    concept perfectly well, and is still asking whether a document is in the envelope — answering it
    with a turnover figure found on some other page is the right verdict reached for the wrong
    reason, and the wrong verdict as soon as the figure is somewhere the certificate is not.
    """
    by_key = FINDERS.get(spec.key)
    if by_key is not None:
        return by_key
    from app.services.concepts import resolve

    measures = spec.threshold_value is not None and spec.operator in (">=", "<=")
    concept = resolve(spec.requirement or spec.label, label=spec.label,
                      datatype="count" if spec.threshold_unit not in (None, "INR") else "currency")
    if concept is not None:
        # A criterion that measures gets a reader that returns a figure; one that only asks whether
        # something was submitted gets the presence reader, which never invents a value. Giving a
        # figure reader to "9. Turn Over and Net Worth Certificate" answers a question about the
        # envelope with a number from somewhere else in it.
        if measures and concept.key in CONCEPT_FINDERS:
            return CONCEPT_FINDERS[concept.key]
        if not measures:
            return find_concept_presence
    return find_generic_document if spec.key.startswith("doc_") else None


def claims_for(doc: ParsedDocument, spec: CriterionSpec, llm=None) -> tuple[list[Claim], list[str]]:
    """The evidence for one criterion in `doc` (which may be a view showing only the documents
    fetched for it), and any warning. One criterion's failure never blocks the others (NFR6)."""
    finder = _finder_for(spec)
    try:
        if finder:
            return list(finder(doc, spec)), []
        if llm is not None:
            return list(llm_find_evidence(llm, doc, spec)), []
        if spec.params.get("terms"):
            return list(find_generic_document(doc, spec)), []
    except Exception as exc:
        log.exception("Extraction failed for %s", spec.key)
        return [], [f"Extraction failed for '{spec.label}': {exc}"]
    return [], []


def dedupe(claims: list[Claim]) -> list[Claim]:
    seen, unique = set(), []
    for c in claims:
        k = (c.criteria_key, c.page, c.quote, c.detail.get("part"))
        if k not in seen:
            seen.add(k)
            unique.append(c)
    return unique


def extract_bidder(doc: ParsedDocument, specs: list[CriterionSpec], filename: str, llm=None) -> BidderExtraction:
    claims: list[Claim] = []
    warnings: list[str] = []
    for spec in specs:
        found, warned = claims_for(doc, spec, llm)
        claims += found
        warnings += warned
    claims.extend(find_quoted_price(doc))
    return BidderExtraction(bidder_name=find_bidder_name(doc, filename), claims=dedupe(claims), warnings=warnings)


# PMC works criteria read from a bid's documents (EMD receipt, registration, bid-capacity working,
# firm constitution, executed quantities) register themselves into FINDERS when this module loads.
import app.services.bidder_evidence  # noqa: E402,F401


# --------------------------------------------------------------------------------------
# Concept-led finders: the fact is in the bid, under the bidder's own name for it
# --------------------------------------------------------------------------------------

def _tables_of(page) -> list[list[list[str]]]:
    """Every grid on a page: the ones pdfplumber ruled, and the ones it could not see.

    A chartered accountant's certificate aligns columns with spaces, so `page.tables` is empty on
    exactly the pages that carry the turnover, net worth and profit series. Those are rebuilt from
    the text lines, which is also the only shape an OCR'd page ever has.
    """
    return list(page.tables or []) + grid_from_lines(page.lines or [])


def _concept_pages(doc: ParsedDocument, concept) -> set[int]:
    """Pages where this concept is named — by a cue in a sentence, or by the heading of the
    document that carries it. Headings matter because a bid is one merged scan: the grid on a page
    is anonymous, and the only thing identifying it is the certificate title above it."""
    pages: set[int] = set()
    for s in doc.sentences:
        if concept.hit(s.text):
            pages.add(s.page)
    from app.services.concepts import CHECKLIST

    for p in doc.pages:
        lines = p.lines or [l for l in (p.text or "").splitlines() if l.strip()]
        # The bid's own contents page names every document it encloses — "16 Manpower Declaration
        # Y Page No.75-77" — so matching a heading anywhere on the page makes the index a candidate
        # for every concept at once. The heading has to be on a line that is not a checklist row.
        if concept.documents is not None and any(
                concept.documents.search(line) and not CHECKLIST.search(line) for line in lines):
            pages.add(p.number)
        elif any(concept.hit(line) == "anchor" for line in lines):
            pages.add(p.number)
    return pages


def _series_claims(doc: ParsedDocument, spec: CriterionSpec, concept, label_rx) -> list[Claim]:
    """Year-stamped figures for one concept, read out of the grids on the pages that name it."""
    out: list[Claim] = []
    seen: set[tuple[int, float]] = set()
    for page in sorted(_concept_pages(doc, concept)):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        tables = _tables_of(p) if p is not None else []
        if not tables:
            continue
        for yv in read_year_series(tables, want=label_rx, avoid=DERIVED_LABEL):
            if (yv.year, yv.value) in seen:
                continue
            seen.add((yv.year, yv.value))
            s = Sentence(page=page, section=yv.label or concept.label, in_table=True,
                         text=f"{yv.label or concept.label} — FY {yv.year}-{str(yv.year + 1)[-2:]}: {yv.cell}")
            out.append(_claim(spec.key, s, f"FY {yv.year}-{str(yv.year + 1)[-2:]}: {format_inr(yv.value)}",
                              yv.value, year=yv.year, source="table", concept=concept.key))
    return out


def find_net_worth(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """The bidder's net worth, year by year where the certificate states it that way.

    Two shapes carry it. A CA's statement labels the row — "2 | Net-Worth | 255 | 293 | 332" — and
    that is read here. A balance sheet states only the components, and the figure has to be derived
    ("Net-worth is determined by subtracting total liabilities from total assets", DBT bid p.13);
    those pages are recorded as derivation candidates rather than guessed at, so the reading stage
    can resolve them against the actual captions instead of this module inventing a subtraction.
    """
    from app.services.concepts import NET_WORTH
    from app.services.year_tables import NETWORTH_LABEL

    out = _series_claims(doc, spec, NET_WORTH, NETWORTH_LABEL)

    # Prose: "the net worth of M/s X as on 31.03.2025 is Rs. 2,10,00,000/-".
    for s in doc.sentences:
        if NET_WORTH.hit(s.text) != "anchor":
            continue
        amounts = parse_amounts(s.text)
        if not amounts:
            continue
        years = parse_financial_years(s.text) or parse_years(s.text)
        value = amounts[0].value
        if any(c.value_numeric == value for c in out):
            continue
        out.append(_claim(spec.key, s, format_inr(value), value,
                          year=years[0] if years else None, concept=NET_WORTH.key))

    # Pages that hold the components but not the total.
    for page in sorted(_concept_pages(doc, NET_WORTH)):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        if p is None or any(c.page == page for c in out):
            continue
        text = (p.text or "").lower()
        for parts in NET_WORTH.derived_from:
            if all(part in text for part in parts):
                s = Sentence(page=page, section="Balance sheet",
                             text=f"Balance sheet stating {' and '.join(parts)}; net worth not stated directly.")
                out.append(_claim(spec.key, s, "derivable from balance sheet", None,
                                  concept=NET_WORTH.key, derive_from=list(parts), needs_reading=True))
                break
    return out


def find_turnover_concept(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """Turnover under any of the names a bid gives it, with the invoice boilerplate refused.

    `find_turnover_series` reads the certificates; this adds the shapes that carry no year grid —
    an audited P&L's "Revenue from Operations", a 26AS "Gross receipt", a prose certificate — and
    drops the GST declaration every tax invoice in a bid repeats, which puts the word "turnover"
    beside invoice amounts while stating nobody's turnover.
    """
    from app.services.concepts import TURNOVER
    from app.services.year_tables import TURNOVER_LABEL

    out = _series_claims(doc, spec, TURNOVER, TURNOVER_LABEL)
    known = {(c.detail.get("year"), c.value_numeric) for c in out}
    for s in doc.sentences:
        cue = TURNOVER.hit(s.text)
        if cue is None:
            continue
        amounts = parse_amounts(s.text)
        if not amounts:
            continue
        years = parse_financial_years(s.text)
        year = years[0] if len(years) == 1 else None
        value = amounts[0].value
        if (year, value) in known:
            continue
        known.add((year, value))
        out.append(_claim(spec.key, s, format_inr(value), value, year=year,
                          concept=TURNOVER.key, cue=cue))
    return out


# A roster is numbered, and its highest serial is the count the bidder is asserting. Counting rows
# instead breaks on the two things that always happen to a 60-name roster in a merged scan: it is
# split across pages (DBT bid pp.75-77), and pdfplumber drops or merges rows inside it. The serials
# survive both. They are only trusted as a count when they run from 1 without a large gap, so a
# stray "48" in a column of experience years cannot become a staff strength of 48.
_SERIAL = re.compile(r"^\s*(\d{1,3})\s*[.)]?\s*$")
_NAME_CELL = re.compile(r"^[A-Z][A-Za-z.\-]+(?:\s+[A-Za-z.\-]+){0,3}$")
_QUALIFICATION = re.compile(
    r"\b(?:B\.?E\.?|B\.?Tech|M\.?Tech|MCA|BCA|BCS|MCS|B\.?Sc|M\.?Sc|BSC|MSC|B\.?Com|M\.?Com|MBA|"
    r"diploma|bachelor|master|graduat|engineer|civil|computer\s+science)\b", re.IGNORECASE)

# "a minimum of 30 IT Professionals" is the requirement, not the bidder's strength. Bids reproduce
# the RFP clause verbatim, and reading its number as the claim would hand every bidder exactly the
# threshold — a pass on the tender's own words.
_REQUIREMENT_ECHO = re.compile(
    r"\b(?:minimum|at\s+least|not\s+less\s+than|must\s+have|should\s+have|shall\s+have|"
    r"required\s+to\s+have|as\s+per\s+(?:the\s+)?(?:RFP|tender|clause))\b", re.IGNORECASE)
_DECLARED = re.compile(
    r"(?:has|have|having|employs?|employing|strength\s+of|total\s+of|comprising\s+of|consists?\s+of)?"
    r"\s*(?P<n>\d{1,4})\s*\+?\s*(?:nos?\.?\s*)?"
    r"(?:[A-Za-z()/,&.\-]+\s+){0,4}?"
    r"(?:IT\s+)?(?:technically\s+qualified\s+)?"
    r"(?P<noun>professionals?|personnel|employees?|staff|engineers?|technicians?|experts?)\b",
    re.IGNORECASE)


def find_staff_strength(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """How many people the bidder has, by every route the submission offers.

    The requirement counts "IT professionals on an on-roll staff strength"; a bid answers with
    "qualified engineers on our payroll", a declared "40+", and a numbered roster of names. All three are returned as separate claims — the
    declared figure because it is what the bidder asserts, the roster because it is the only one
    that can be counted, and the tabulated staff statement because works bids use that instead.
    """
    from app.services.concepts import STAFF_STRENGTH

    pages = _concept_pages(doc, STAFF_STRENGTH)
    if not pages:
        return []
    out: list[Claim] = []

    for s in doc.sentences:
        if s.page not in pages or STAFF_STRENGTH.hit(s.text) is None:
            continue
        if _REQUIREMENT_ECHO.search(s.text):
            continue
        m = _DECLARED.search(s.text)
        if not m:
            continue
        count = int(m.group("n"))
        if not 1 <= count <= 5000:
            continue
        out.append(_claim(spec.key, s, f"{count} {m.group('noun').lower()}", float(count),
                          concept=STAFF_STRENGTH.key, source="declared",
                          approximate="+" in m.group(0)))

    # The roster itself, counted by its serials across however many pages it runs.
    # Serials are collected per page and only then assembled into a roster. A single dict keyed by
    # serial would be first-come-first-served, and the bid's own index page — scanned first, and
    # numbering its contents 1, 2, 3 … — takes the roster's low numbers for itself. The roster then
    # appears to start at 5 and is discarded. Which page a serial sits on is the whole question
    # here, so nothing may be deduplicated before the pages are grouped.
    per_page: dict[int, set[int]] = {}
    for page in sorted(pages | {p + 1 for p in pages} | {p + 2 for p in pages}):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        if p is None:
            continue
        here: set[int] = set()
        for rows in p.tables:
            for row in rows:
                cells = [(c or "").strip() for c in row]
                serial = next((int(m.group(1)) for c in cells[:2] if (m := _SERIAL.match(c))), None)
                if serial is None:
                    continue
                looks_like_a_person = any(_NAME_CELL.match(c) for c in cells[1:3]) or \
                    any(_QUALIFICATION.search(c) for c in cells[1:])
                if looks_like_a_person:
                    here.add(serial)
        # The roster is usually not ruled either — "1 Aashwini Bankar MCA 20" is a line of text.
        # A degree on the line is what separates a person from a bill-of-quantities item, which has
        # the same shape ("1 Excavation in ordinary soil 2500") and must not be counted as staff.
        for line in (p.lines or []):
            tokens = line.split()
            if len(tokens) < 3 or not _SERIAL.match(tokens[0]):
                continue
            if not _QUALIFICATION.search(" ".join(tokens[1:])):
                continue
            here.add(int(_SERIAL.match(tokens[0]).group(1)))
        if here:
            per_page[page] = here

    serials: dict[int, int] = {}  # serial -> page it was seen on
    if per_page:
        # A roster runs over consecutive pages, numbering upward. Starting from the page holding
        # the highest serial, each preceding page joins while it continues that numbering from
        # below — which admits the roster's own pages and excludes an index that happens to count
        # its rows somewhere else in the submission.
        last_page = max(per_page, key=lambda pg: max(per_page[pg]))
        block = [last_page]
        while last_page - 1 in per_page and min(per_page[last_page - 1]) < min(per_page[last_page]):
            last_page -= 1
            block.append(last_page)
        for page in sorted(block):
            for n in sorted(per_page[page]):
                serials.setdefault(n, page)
    if serials:
        top = max(serials)
        rows_read = sum(1 for n in range(1, top + 1) if n in serials)
        # The top serial has to be the end of a list, not a number that happens to be large. A lone
        # "66" on a page is a quantity, an age or a page number; a 66 with 63, 64 and 65 around it
        # is the bottom of a roster. Neighbours rather than an unbroken run, because a single row
        # dropped by the table reader sits at the end as often as anywhere else and would otherwise
        # discard the whole roster — which is what happened here: serial 66 was not read, and a
        # 67-name roster counted as nothing.
        #
        # Coverage is allowed to be partial for the same reason, so both figures are carried: the
        # top serial as what the bidder's own numbering asserts, and `rows_read` as what was
        # actually counted, which is the floor a verdict can pass on without trusting the top.
        neighbours = sum(1 for n in range(max(1, top - 4), top) if n in serials)
        if 1 in serials and top >= 3 and neighbours >= 2 and rows_read / top >= 0.5:
            first, last = serials[1], serials[top]
            s = Sentence(page=first, section="Roster",
                         text=f"Numbered roster of personnel, serials 1–{top} ({rows_read} rows read) "
                              f"on pages {first}–{last}.")
            out.append(_claim(spec.key, s, f"{top} named personnel", float(top),
                              concept=STAFF_STRENGTH.key, source="roster",
                              rows_read=rows_read, floor=rows_read,
                              first_page=first, last_page=last))
    return out


CONCEPT_FINDERS["turnover"] = find_turnover_concept
CONCEPT_FINDERS["net_worth"] = find_net_worth
CONCEPT_FINDERS["staff_strength"] = find_staff_strength

# The original turnover finder matched any sentence containing the word, which includes a bid's own
# contents checklist: a row naming the audited turnover statement next to its page numbers was
# read as a turnover of two thousand rupees, and disqualified a bidder whose certificate states
# crores. The concept-aware reader refuses checklists.
FINDERS["avg_turnover"] = find_turnover_concept


# A bid opens with a list of its own contents, one row per enclosure. Where the list is ruled, each
# row is a bare title — "Anti-Collusion Certificate" — with the page range in a separate cell, so
# the row carries none of the marks a checklist line usually has and `CHECKLIST` cannot see it. The
# page as a whole is unmistakable: almost every line on it is an entry in a list. Evidence may never
# come from it, because a bid asserting that it contains a certificate is not the certificate.
_CONTENTS_HEADING = re.compile(
    r"checklist|table\s+of\s+contents|\bindex\b|list\s+of\s+(?:documents|enclosures|annexures)"
    r"|compliance\s*\(?\s*yes\s*/\s*no|page\s*no\.?\s*(?:and|&)\s*section", re.IGNORECASE)


def _is_contents_page(page) -> bool:
    """Is this the page where the bid lists what it contains?

    The rows of such a list are usually a ruled table, so `page.lines` holds only the heading and a
    letterhead footer — a few lines on a bid's first page, which no ratio of listed lines can
    detect. The heading is the reliable signal and it is right there: a "checklist of documents"
    heading, usually with a compliance (yes/no) column beside it.
    """
    text = page.text or ""
    header = " ".join(" ".join(r[:1] + r[-1:]) for rows in (page.tables or []) for r in rows[:2])
    if _CONTENTS_HEADING.search(text[:600]) or _CONTENTS_HEADING.search(header):
        return True
    from app.services.concepts import CHECKLIST

    lines = [l.strip() for l in (page.lines or []) if len(l.strip()) >= 4]
    if len(lines) < 6:
        return False
    listed = sum(1 for l in lines if CHECKLIST.search(l) or _CONTENTS_ROW.match(l))
    return listed / len(lines) >= 0.4


# "21. Anti Blacklisting 98-99", "16 Manpower Declaration Y Page No.75-77", and the bare-title form
# a ruled contents table produces: a short title with no verb, under thirty characters of words.
_CONTENTS_ROW = re.compile(
    r"^\s*(?:\d{1,2}[.)]?\s+)?[A-Z][A-Za-z/&,’'\-. ]{3,46}\s*(?:\d{1,3}\s*[-–]\s*\d{1,3})?\s*$")

# Words too common in procurement language to tell one requirement from another.
_UNDISTINCTIVE = frozenset("""
the of and to in a is be for shall on by with as or any this that are will from at not an all
bidder bidders tender tenders document documents documentary evidence copy copies submit submitted
required requirement should must have has been their its our we us i as per section clause annexure
appendix format given mentioned above below following each last three financial year years date
details detail name names such other others along with respectively case form forms page no nos
certificate certificates declaration declarations self undertaking statement statements letter
registration registered valid proof copies signed duly stamped attached enclosed authorised
authorized terms accordance condition conditions entire scope work works service services part
new one two four five all our ours company companies firm firms agency agencies entity india
""".split())


# Marathi procurement filler: the words every clause contains, which therefore separate none of
# them. "निविदाधारकाने वैध नोंदणी प्रमाणपत्र सादर करणे आवश्यक आहे" is almost entirely made of these.
_MR_UNDISTINCTIVE = frozenset("""
आहे आणि व या चे ची चा चे साठी करणे सादर करावी करावे कराव्या लागेल राहील आवश्यक असणे अनिवार्य
निविदा निविदेसोबत निविदाधारक निविदाधारकाने निविदाधारकाची कंत्राटदार कंत्राटदाराने ठेकेदार ठेकेदाराने
प्रमाणपत्र दाखला कागदपत्रे प्रत प्रती यांनी यांचे तसेच सर्व इतर नुसार बाबत मध्ये वरील खालील
सोबत जोडणे जोडलेली असल्यास केलेल्या केले अथवा किंवा तथापि मात्र
""".split())


def _distinctive(spec: CriterionSpec) -> set[str]:
    """The words that make this requirement a different requirement from its neighbours.

    A concept says what *kind* of fact a clause is about; it cannot tell "Project Lead" from
    "Database Administrator", or an anti-collusion declaration from an insolvency one, because both
    pairs are the same kind of fact. Without this, one roster row reading "Diploma in Graphic
    Designer" was cited as evidence of a Database Administrator, and a GST undertaking as evidence
    of solvency — each a pass on a page that does not say what the committee would be told it says.
    """
    text = f"{spec.label} {spec.requirement}".lower()
    words: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text):
        # "Anti-Collusion" has to compare equal to a page that writes "Anti Collusion".
        words.update([w] + [part for part in w.split("-") if len(part) >= 3])
    words = {w for w in words if w not in _UNDISTINCTIVE and len(w) >= 3}
    # The same test in Devanagari. Without it a Marathi clause yields no distinctive words at all,
    # the gate is skipped, and a Marathi requirement gets no instance-level precision even against
    # a Marathi bid — "Project Lead" and "Database Administrator" become indistinguishable again.
    # Marathi inflects, so a word is kept with its stem as well as whole.
    for w in re.findall(r"[ऀ-ॿ]{3,}", text):
        if w in _MR_UNDISTINCTIVE:
            continue
        words.add(w)
        if len(w) > 4:
            words.add(w[:4])
    return words


def find_concept_presence(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """Evidence that a document or registration the tender asks for is in the envelope.

    `find_generic_document` searches for whatever words the requirement happened to use. That finds
    nothing when the bid calls the thing something else, and it finds the wrong page when the words
    appear in the bid's own contents list or in a clause quoting the tender back. A concept carries
    the bidder's vocabulary instead, together with the two things that must stop a match: a veto for
    text that is not about the fact, and a negation for text that is about it and says no.

    A structured identifier — a PAN, a GSTIN, a Udyam number — is reported first, because a number
    only one issuer can mint is stronger evidence than any sentence around it.
    """
    from app.services.concepts import resolve

    concept = resolve(spec.requirement or spec.label, label=spec.label,
                      datatype="count" if spec.threshold_unit not in (None, "INR") else "currency")
    if concept is None:
        return find_generic_document(doc, spec)

    from app.services.concepts import script_of

    terms = _distinctive(spec)
    requirement_script = script_of(f"{spec.label} {spec.requirement}")
    contents = {p.number for p in doc.pages if _is_contents_page(p)}
    page_script = {pg.number: script_of(pg.text or "") for pg in doc.pages}
    page_words = {}
    for pg in doc.pages:
        found: set[str] = set()
        low = (pg.text or "").lower()
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", low):
            found.update([w] + [part for part in w.split("-") if len(part) >= 3])
        for w in re.findall(r"[ऀ-ॿ]{3,}", low):
            found.add(w)
            if len(w) > 4:
                found.add(w[:4])
        page_words[pg.number] = found
    # Three grades of evidence, and the citation shown is the best one found. The page a bid states a
    # fact on usually carries several lines of the right kind — a covering letter answers four
    # separate clauses of the RFP in four paragraphs — so quoting the first is how three criteria end
    # up citing one sentence that addresses none of them.
    exact: list[Claim] = []   # the quoted line itself names what the clause asked for
    on_page: list[Claim] = []  # something else on the page does
    weak: list[Claim] = []     # the right kind of page, naming nothing the clause asked for
    seen: set[tuple[int, str]] = set()

    def add(page: int, text: str, cue: str, section: str = "") -> None:
        if page in contents:
            return
        key = (page, re.sub(r"\s+", " ", text).strip().lower()[:90])
        if key in seen:
            return
        seen.add(key)
        line_words: set[str] = set()
        low = text.lower()
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", low):
            line_words.update([w] + [part for part in w.split("-") if len(part) >= 3])
        for w in re.findall(r"[ऀ-ॿ]{3,}", low):
            line_words.add(w)
            if len(w) > 4:
                line_words.add(w[:4])
        # A Marathi tender's words will never appear on an English certificate, and the reverse is
        # just as common: a PMC works tender is written in Marathi and answered by bids whose
        # balance sheets, GST certificates and work orders are in English. Across alphabets the
        # distinctive-term test cannot run at all — applied anyway it rejects every page, and the
        # criterion comes back unknown on a submission that answers it. The concept is the bridge
        # that still holds, because it carries both vocabularies, so a concept match stands on its
        # own and the claim records that this is why.
        other_script = (requirement_script != page_script.get(page, "latin")
                        and "mixed" not in (requirement_script, page_script.get(page, "latin")))
        if cue == "identifier" or not terms:
            # A number only one issuer can mint needs no corroborating word near it. Demoting the
            # GSTIN on the bidder's own GST certificate because the page never spells out "GST"
            # handed the criterion to a page that merely mentioned GST returns in passing.
            bucket = exact
        elif terms & line_words:
            bucket = exact
        elif terms & page_words.get(page, set()):
            bucket = on_page
        elif other_script:
            bucket = on_page
        else:
            bucket = weak
        if len(bucket) >= 6:
            return
        s = Sentence(page=page, text=text.strip()[:400], section=section or concept.label)
        value = concept.label if cue != "identifier" else f"{concept.label} stated"
        bucket.append(_claim(spec.key, s, value, None, concept=concept.key, cue=cue,
                             weak=bucket is weak,
                             cross_language=other_script and bucket is on_page))

    for cue_wanted in ("identifier", "anchor"):
        for s in doc.sentences:
            if concept.hit(s.text) == cue_wanted:
                add(s.page, s.text, cue_wanted, s.section)
        for p in doc.pages:
            for line in (p.lines or []):
                if len(line.strip()) >= 4 and concept.hit(line) == cue_wanted:
                    add(p.number, line, cue_wanted)
        if exact or on_page:
            break
    # Evidence of the right kind on a page that names nothing the clause asked for is shown, so the
    # committee can look, but it is not allowed to pass the criterion on its own.
    return (exact + on_page) or weak


# A date the firm came into being, and the years since: the date of incorporation or registration
# on a Udyam certificate, the date of commencement of business on a portal profile — the
# requirement (proof that the firm has existed for some years) is
# answered by a date, and the comparison needs the number of years it implies.
_DATE = re.compile(r"\b(\d{1,2})[./\-](\d{1,2})[./\-]((?:19|20)\d{2})\b")
_MONTH_DATE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+((?:19|20)\d{2})\b",
    re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def find_firm_age(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """How long the bidder has existed, in years, from the date its own documents state."""
    from datetime import date

    from app.services.concepts import FIRM_AGE

    today = date.today()
    out: list[Claim] = []
    seen: set[int] = set()
    for page in sorted(_concept_pages(doc, FIRM_AGE)):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        if p is None:
            continue
        for line in (p.lines or []) + [s.text for s in doc.sentences if s.page == page]:
            if FIRM_AGE.hit(line) is None:
                continue
            found = None
            m = _DATE.search(line)
            if m:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    found = date(y, mo, d)
            if found is None and (m := _MONTH_DATE.search(line)):
                found = date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
            if found is None or found > today:
                continue
            years = (today - found).days / 365.25
            if years in seen or years > 120:
                continue
            seen.add(years)
            s = Sentence(page=page, text=line.strip()[:300], section="Constitution")
            out.append(_claim(spec.key, s, f"{years:.1f} years (since {found.isoformat()})",
                              round(years, 1), concept=FIRM_AGE.key, since=found.isoformat()))
    return out


def find_tender_fee(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """The tender fee the bidder paid — a separate payment from the EMD, on the same receipt."""
    from app.services.concepts import TENDER_FEE

    out: list[Claim] = []
    for page in sorted(_concept_pages(doc, TENDER_FEE)):
        p = doc.pages[page - 1] if 0 < page <= len(doc.pages) else None
        if p is None:
            continue
        for line in (p.lines or []) + [s.text for s in doc.sentences if s.page == page]:
            if TENDER_FEE.hit(line) is None:
                continue
            # "Tender Fee in ₹ : 6,579" — the currency marker is a symbol in the label, so the
            # amount beside it carries none of its own.
            amounts = parse_amounts(line) or [
                Amount(value=float(m.group(1).replace(",", "")), raw=m.group(1))
                for m in re.finditer(r"(?<![\d,.])(\d[\d,]{2,})(?![\d,.])", line)]
            if not amounts:
                continue
            value = amounts[0].value
            if not 100 <= value <= 10_000_000 or any(c.value_numeric == value for c in out):
                continue
            s = Sentence(page=page, text=line.strip()[:300], section="Tender fee")
            out.append(_claim(spec.key, s, format_inr(value), value, concept=TENDER_FEE.key))
    return out


CONCEPT_FINDERS["firm_age"] = find_firm_age
CONCEPT_FINDERS["tender_fee"] = find_tender_fee
