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
from app.services.values import GSTIN, format_inr, format_inr_short, parse_amounts, parse_durations, parse_years

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
