"""Hybrid pipeline for one bid (the bidder-side twin of tender_processing).

    envelope (zip / rar / PDFs) ─► expand ─► per document: pdfium quick scan + structural parse
        ─► page types (bidder_documents) ─► claims per criterion (bidder_extraction)  ← local result
        ─► OCR: every unreadable page, plus scans whose embedded text layer mangled its figures
        ─► page types + claims again                                                  ← final result

Nothing here knows any particular bidder: identity, figures and documents are all read from the
upload in front of it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.adapters.datalab_ocr import DatalabOcr
from app.config import settings
from app.services import tender_processing as tp
from app.services.bidder_documents import classify_pages, contents, page_type
from app.services.bidder_extraction import Claim, CriterionSpec, extract_bidder
from app.services.values import ascii_digits, corrupt_amounts

# MahaTender names the technical envelope after the bid: Fee_PreQual_Technical_1234567.zip
BID_REF = re.compile(r"(?:Fee_)?(?:PreQual|Technical|Fee)[_\- ]+(?:Technical[_\- ]+)?(\d{6,9})", re.I)


def bid_ref_from_names(names: list[str]) -> str | None:
    for n in names:
        m = BID_REF.search(n)
        if m:
            return m.group(1)
    return None


# --------------------------------------------------------------------------------------
# OCR plan
# --------------------------------------------------------------------------------------

def plan_ocr_all(docs: list[tp.ProcessedDoc]) -> list[tuple[tp.ProcessedDoc, int, str]]:
    plan: list[tuple[tp.ProcessedDoc, int, str]] = []
    for d in docs:
        if not d.scans:
            continue
        wanted = dict(tp.ocr_pages(d))
        # A scan that arrived with somebody else's OCR layer reads as English, yet states its figures
        # as "L,35,20 ,614" and "3, L4 ,56,08 2". Refusing those protects the verdict; reading the
        # image again is what recovers them.
        for s in d.scans:
            if s.number in wanted or not s.scanned or s.quality.kind != "english":
                continue
            # Chandra reads the grid the embedded layer flattened into prose ("2020-20031 4.52 PMC &
            # other Contract 2021-2022 7.82 ..."): worth it for the pages every verdict leans on.
            if corrupt_amounts(s.text) or page_type(s.text) in ("turnover_certificate", "bid_capacity"):
                wanted[s.number] = settings.OCR_MODE_SCANNED
        plan += [(d, page, mode) for page, mode in sorted(wanted.items())]
    return plan


def plan_ocr(docs: list[tp.ProcessedDoc]) -> list[tuple[tp.ProcessedDoc, int, str]]:
    return plan_ocr_all(docs)[: settings.OCR_MAX_PAGES_PER_BIDDER]


def skipped_by_cap(docs: list[tp.ProcessedDoc]) -> list[str]:
    extra = plan_ocr_all(docs)[settings.OCR_MAX_PAGES_PER_BIDDER:]
    if not extra:
        return []
    return [f"{len(extra)} unreadable page(s) were not sent to OCR (per-bid cap of {settings.OCR_MAX_PAGES_PER_BIDDER}; "
            "raise TE_OCR_MAX_PAGES_PER_BIDDER to include them)."]


def run_ocr(docs: list[tp.ProcessedDoc], ocr: DatalabOcr, on_progress=None) -> tuple[int, int, list[str]]:
    return tp.run_ocr(docs, ocr, on_progress=on_progress, plan=plan_ocr(docs))


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------

_FIRM_PATTERNS = [
    # (pattern, weight): a bid that says who it is from outranks everything; then the GST and
    # registration certificates, which name the firm formally.
    (re.compile(r"^\s*(?:Bidder|Name\s*of\s*(?:the\s*)?(?:Bidder|Tenderer)|Submitted\s*by)\s*:\s*(?:M/?s\.?\s*)?"
                r"(?P<n>[A-Z][A-Za-z0-9.&' ]{2,60}?)\s*$", re.I | re.M), 8),
    (re.compile(r"Trade\s*Name,?\s*(?:if\s*any)?\s*[:|]?\s*(?P<n>[A-Z][A-Za-z0-9.&' ]{2,60}?)\s*(?:\n|\||Details|Constitution|$)"), 5),
    (re.compile(r"Registration\s+to\s+(?P<n>[A-Z][A-Z0-9.&' ]{2,60}?)\s+(?:Propriet|Partner|Director|in\s+CLASS)", re.I), 5),
    (re.compile(r"Contractors?\s*Name\s*(?:and|&)\s*Address\s*:?\s*(?P<n>[A-Z][A-Z0-9.&' ]{2,60}?)\s+\d"), 4),
    (re.compile(r"Name\s*of\s*(?:the\s*)?(?:Bidder|Tenderer|Firm|Contractor)\s*[:|]\s*(?:M/?s\.?\s*)?(?P<n>[A-Z][A-Za-z0-9.&' ]{2,60}?)\s*(?:\n|\||,|$)", re.I), 4),
    (re.compile(r"\bM/s\.?\s*(?P<n>[A-Z][A-Za-z0-9.&' ]{2,50}?)(?=\s*(?:\n|,|\||Civil|Address|having|Proprietor|PROPRIETOR|through|represented|Pune|$))"), 1),
]
_CA_CONTEXT = re.compile(r"chartered\s*accountant|\bFRN\b|\bUDIN\b|\bM\.\s*No\.|auditor", re.I)

_CONSTITUTION = [
    ("Proprietorship", re.compile(r"\bproprietor(?:ship)?\b|propriet[eo]r|प्रोप्रा|मालक", re.I)),
    ("Partnership", re.compile(r"\bpartnership\b|\bpartners?\b|भागीदार", re.I)),
    ("Private Limited Company", re.compile(r"private\s+limited|pvt\.?\s*ltd", re.I)),
    ("Limited Company", re.compile(r"\blimited\b|\bltd\b", re.I)),
    ("LLP", re.compile(r"\bLLP\b|limited\s+liability\s+partnership", re.I)),
]


def _norm_firm(name: str) -> str:
    n = re.sub(r"^(?:M/?S\.?|MESSRS\.?)\s*", "", name.strip(" .,:-|"), flags=re.I)
    n = re.sub(r"\s+", " ", n).strip(" .,")
    return n


def identify(docs: list[tp.ProcessedDoc], types_by_doc: dict[str, list]) -> dict:
    """The bidder's firm and constitution, as the bid's own documents state them."""
    votes: Counter = Counter()
    first_seen: dict[str, tuple[str, int, str]] = {}
    constitution: Counter = Counter()
    for d in docs:
        if d.parsed is None:
            continue
        types = types_by_doc.get(d.document_id) or []
        for p in d.parsed.pages:
            text = p.text or ""
            if not text.strip() or not p.quality.quotable:
                continue
            kind = types[p.number - 1] if p.number - 1 < len(types) else None
            boost = 2 if kind in ("gst", "registration", "turnover_certificate", "bid_capacity") else 1
            for pattern, weight in _FIRM_PATTERNS:
                for m in pattern.finditer(text):
                    name = _norm_firm(m.group("n"))
                    if len(name) < 3 or re.fullmatch(r"[\d .]+", name):
                        continue
                    # The auditor who signs a turnover certificate is named on it too, as a firm.
                    around = text[max(0, m.start() - 120): m.end() + 160]
                    if _CA_CONTEXT.search(name) or (weight < 8 and _CA_CONTEXT.search(around)
                                                    and not re.search(r"certify\s+that|turn\s?over\s+of", around, re.I)):
                        continue
                    key = re.sub(r"[^A-Z0-9]", "", name.upper())
                    votes[key] += weight * boost
                    first_seen.setdefault(key, (name, p.number, d.document_id))
            if kind in ("gst", "registration", "turnover_certificate", "constitution"):
                for label, pattern in _CONSTITUTION:
                    if pattern.search(text):
                        constitution[label] += 1
                        break
    out: dict = {}
    if not votes:
        return out
    if votes:
        key, _ = votes.most_common(1)[0]
        name, page, doc_id = first_seen[key]
        out["firm_name"] = {"value": name.title() if name.isupper() else name, "page": page, "document_id": doc_id}
    if constitution:
        out["constitution"] = {"value": constitution.most_common(1)[0][0]}
    return out


# --------------------------------------------------------------------------------------
# Extraction over the bundle
# --------------------------------------------------------------------------------------

@dataclass
class BidExtraction:
    claims: list[Claim]
    identity: dict
    types_by_doc: dict[str, list]
    contents_by_doc: dict[str, list]
    warnings: list[str] = field(default_factory=list)


def extract(docs: list[tp.ProcessedDoc], specs: list[CriterionSpec], llm=None) -> BidExtraction:
    claims: list[Claim] = []
    warnings: list[str] = []
    types_by_doc: dict[str, list] = {}
    fallback_names: list[str] = []
    for d in sorted(docs, key=lambda x: -(len(x.scans) or 0)):
        if d.parsed is None:
            continue
        types = classify_pages(d.parsed)
        types_by_doc[d.document_id] = types
        d.parsed.page_types = types  # type: ignore[attr-defined]  (finders look at the right pages)
        result = extract_bidder(d.parsed, specs, d.name, llm=llm)
        for c in result.claims:
            c.detail["document_id"] = d.document_id
            c.detail.setdefault("page_type", types[c.page - 1] if 0 < c.page <= len(types) else None)
        claims += result.claims
        warnings += result.warnings
        if result.bidder_name and not BID_REF.search(result.bidder_name.replace(" ", "_")):
            fallback_names.append(result.bidder_name)
    identity = identify(docs, types_by_doc)
    # The portal stamps the payment receipt when the bid is made: the date validity is judged on.
    paid = sorted(c.detail["paid_on"] for c in claims if c.criteria_key in ("pmc_emd", "emd") and c.detail.get("paid_on"))
    if paid:
        identity["bid_date"] = paid[-1]
    if "firm_name" not in identity and fallback_names:
        identity["firm_name"] = {"value": fallback_names[0], "page": None, "document_id": None, "guessed": True}
    return BidExtraction(claims=claims, identity=identity, types_by_doc=types_by_doc,
                         contents_by_doc={k: contents(v) for k, v in types_by_doc.items()}, warnings=warnings)


def coverage_warnings(docs: list[tp.ProcessedDoc], ocr_available: bool, ocr_errors: list[str] | None = None) -> list[str]:
    out = []
    for d in docs:
        if d.error:
            out.append(f"{d.name}: {d.error}")
        if d.parsed is None:
            continue
        scans = {s.number: s for s in d.scans}
        unread = [p.number for p in d.parsed.pages if p.quality.kind in ("image", "garbled")
                  and (scans.get(p.number) is None or scans[p.number].scanned or len(scans[p.number].text.strip()) >= 150)]
        if unread:
            why = ("OCR failed for some of them" if ocr_errors else "OCR returned no text for them") if ocr_available \
                else "OCR is not configured (set DATALAB_API_KEY)"
            out.append(f"{len(unread)} page(s) of {d.name} could not be read (p. {tp._ranges(unread)}) — {why}. "
                       "Evidence on those pages is not considered.")
    return out


def tender_context(tender) -> dict:
    """The tender facts a bid is checked against beyond the criterion itself."""
    facts = {f.get("key"): f.get("value") for f in (tender.facts or []) if isinstance(f, dict)}
    months = facts.get("completion_months")
    return {
        "ref_no": tender.tender_ref_no or facts.get("ref_no"),
        "title": tender.title,
        "estimated_cost": tender.estimated_value or facts.get("estimated_cost"),
        "emd": facts.get("emd"),
        "completion_months": months,
        "completion_years": (float(months) / 12) if months else None,
    }


def normalise_for_match(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ascii_digits(text or "").upper())
