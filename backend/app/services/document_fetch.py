"""Fetching a bidder's documents by what the tender requires.

A tender states what it requires; a bid is one or more merged scans of many documents in no fixed
order. This module joins the two, so every requirement points at the documents in the bid that
answer it, and the evidence readers read those documents first:

    plan(specs)                each requirement -> the kinds of document that prove it
    inventory(files)           each bid -> its documents: kind, file, pages, title, date, firm named
    fetch(needs, inv, files)   each requirement -> the bid's documents of those kinds, ranked, why

Nothing here knows a particular tender, bidder or issuer. A document's kind is recognised by how
documents of that kind are headed (`bidder_documents.PAGE_TYPES`), a requirement by the criterion
it was extracted as, by what it is about (`concepts.resolve`) or — for a requirement nothing covers —
by the tender's own words for the document it asks for. A requirement that matches no document is
reported as such: nothing is guessed in its place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date

from app.adapters.pdf_parser import ParsedDocument
from app.services.bidder_documents import LABELS, page_type
from app.services.values import ascii_digits

PRIMARY, HEADING, SUPPORTING = "primary", "heading", "supporting"
_ROLE_ORDER = {PRIMARY: 0, HEADING: 1, SUPPORTING: 2}

# What proves a requirement, by the criterion the tender-side extraction produced: the documents
# that state the fact, then documents that only corroborate it.
BY_KEY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pmc_emd": (("emd_receipt",), ()),
    "emd": (("emd_receipt",), ()),
    "pmc_registration": (("registration",), ()),
    "pmc_bid_capacity": (("bid_capacity",), ("turnover_certificate", "works_in_hand")),
    "pmc_max_turnover": (("turnover_certificate", "balance_sheet"), ("itr",)),
    "avg_turnover": (("turnover_certificate", "balance_sheet"), ("itr",)),
    "pmc_similar_works": (("completion_certificate", "completed_works"), ("boq_certificate", "work_order")),
    "min_similar_projects": (("completion_certificate", "completed_works", "work_order"), ()),
    "pmc_key_quantities": (("boq_certificate", "completion_certificate"), ("completed_works",)),
    "no_joint_venture": (("jv_agreement", "constitution", "registration", "gst"), ("affidavit",)),
    "gst_registration": (("gst",), ()),
    "epf_esic_registration": (("labour",), ()),
}

# The same, by what a requirement is about, for criteria a tender words its own way.
BY_CONCEPT: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "turnover": (("turnover_certificate", "balance_sheet"), ("itr",)),
    "net_worth": (("balance_sheet", "turnover_certificate"), ()),
    "net_profit": (("balance_sheet",), ("itr",)),
    "solvency": (("solvency",), ("bank",)),
    "staff_strength": (("technical_staff",), ()),
    "key_roles": (("technical_staff",), ()),
    "pan": (("pan",), ("itr",)),
    "gst": (("gst",), ()),
    "msme_udyam": (("constitution",), ()),
    "legal_entity": (("constitution",), ()),
    "firm_age": (("constitution", "registration"), ()),
    "non_blacklisting": (("affidavit",), ()),
    "not_insolvent": (("affidavit",), ("solvency",)),
    "anti_collusion": (("affidavit",), ()),
    "non_disclosure": (("affidavit",), ()),
    "no_deviation": (("affidavit",), ()),
    "past_performance": (("affidavit", "completion_certificate"), ()),
    "no_joint_venture": (("jv_agreement", "constitution", "registration", "gst"), ("affidavit",)),
    "audited_accounts": (("balance_sheet",), ("itr",)),
    "tender_fee": (("emd_receipt",), ()),
    "provident_fund": (("labour",), ()),
    "employee_insurance": (("labour",), ()),
    "work_order": (("work_order",), ()),
    "completion_certificate": (("completion_certificate",), ("completed_works",)),
    "project_experience": (("completion_certificate", "completed_works", "work_order"), ()),
    "machinery": (("machinery",), ()),
}

# Conditions of the bid (validity, rate analysis, additional security) ask nothing of the technical
# envelope; the committee applies them once prices are open.
NOTHING_TO_FETCH = {"bid_validity", "bid_rate_analysis", "bid_additional_security"}

# Requirements that are answered by an *absence* — no joint-venture agreement anywhere in the bid.
# Their documents are fetched and shown, but reading is never narrowed to them: what must be absent
# can be on any page, and stopping at the documents fetched would pass a bid that encloses it.
WHOLE_BID = {"no_joint_venture"}

# Words that say what a document is about, not which one: dropped before the tender's own wording
# is used to find a document nothing else covers.
_STOP = set("""a an and any as at be by copy copies documents document duly enclosed for from has have
in is of on or per proof self should submit submitted the their this to under upload uploaded valid
with bidder bidders tenderer contractor firm company certificate certified attested shall must""".split())

_DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b")
# The firm a document is issued to or by: "M/s" and a run of capitalised words, or "मे." up to the
# word that ends a Marathi name.
_FIRM = re.compile(r"\b[Mm]/[Ss]\.?[ \t]*(?P<en>(?:(?!(?:Propriet|Partner|Prop\b|Through|Represented|Address))"
                   r"(?:[A-Z][\w.'-]*|&|and)[ \t]?){1,7})"
                   r"|मे\.\s*(?P<mr>[^\n,|:;()]{3,60}?)(?=\s*(?:,|\(|\||;|\n|$|यांनी|यांचे|हे\s|तर्फे))", re.M)


@dataclass(frozen=True)
class Need:
    """What one requirement needs from a bid."""

    key: str
    label: str
    primary: tuple[str, ...] = ()
    supporting: tuple[str, ...] = ()
    headings: re.Pattern | None = None  # the concept's document headings, for kinds no page type covers
    terms: tuple[str, ...] = ()  # the tender's own words for the document, when nothing else covers it
    basis: str = ""  # criterion | concept | wording | none
    whole_bid: bool = False  # answered by an absence: read the whole bid


@dataclass
class BidDocument:
    """One document inside a bid: a run of pages of one kind in one uploaded file."""

    kind: str
    label: str
    document_id: str | None
    file_name: str
    first: int
    last: int
    title: str = ""
    dated: str | None = None
    firm: str | None = None

    @property
    def pages(self) -> set[int]:
        return set(range(self.first, self.last + 1))

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "document_id": self.document_id, "file_name": self.file_name,
                "from": self.first, "to": self.last, "title": self.title, "dated": self.dated, "firm": self.firm}


@dataclass
class Fetched:
    doc: BidDocument
    role: str  # primary | heading | supporting
    why: str


@dataclass
class RequirementDocs:
    """The documents a bid holds for one requirement, best first."""

    key: str
    label: str
    status: str  # found | supporting_only | not_found | not_a_gate
    basis: str
    documents: list[Fetched] = field(default_factory=list)
    wanted: tuple[str, ...] = ()
    whole_bid: bool = False

    def scope(self, document_id: str | None, tier: str) -> set[int]:
        """Pages of one file to read for this requirement at a tier: "documents" (what states the
        fact) or "supporting" (that, plus what corroborates it). Empty for a requirement answered by
        an absence, which is always read across the whole bid."""
        if self.whole_bid:
            return set()
        roles = {PRIMARY, HEADING} if tier == "documents" else {PRIMARY, HEADING, SUPPORTING}
        pages: set[int] = set()
        for f in self.documents:
            if f.role in roles and f.doc.document_id == document_id:
                pages |= f.doc.pages
        return pages

    def holding(self, document_id: str | None, page: int) -> Fetched | None:
        return next((f for f in self.documents if f.doc.document_id == document_id and f.doc.first <= page <= f.doc.last), None)

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status, "basis": self.basis,
                "reads": "whole_bid" if self.whole_bid else "documents_first",
                "wanted": [LABELS.get(k, k) for k in self.wanted],
                "documents": [{**f.doc.as_dict(), "role": f.role, "why": f.why} for f in self.documents]}


# --------------------------------------------------------------------------------------------
# 1. The requirement plan
# --------------------------------------------------------------------------------------------

def _terms(text: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}|[ऀ-ॿ]{3,}", text or "")
    return tuple(dict.fromkeys(w.lower() for w in words if w.lower() not in _STOP))[:6]


def plan(specs) -> list[Need]:
    """Each requirement, and the kinds of document that answer it."""
    from app.services.concepts import resolve

    needs: list[Need] = []
    for s in specs:
        if s.key in NOTHING_TO_FETCH:
            needs.append(Need(s.key, s.label, basis="none"))
            continue
        if s.key in BY_KEY:
            primary, supporting = BY_KEY[s.key]
            needs.append(Need(s.key, s.label, primary, supporting, basis="criterion", whole_bid=s.key in WHOLE_BID))
            continue
        if s.key.startswith("personnel_"):
            needs.append(Need(s.key, s.label, ("technical_staff",), (), basis="criterion"))
            continue
        concept = resolve(s.requirement or s.label, label=s.label,
                          datatype="count" if (s.threshold_unit or "INR") != "INR" else "currency")
        if concept is not None:
            primary, supporting = BY_CONCEPT.get(concept.key, ((), ()))
            needs.append(Need(s.key, s.label, primary, supporting, headings=concept.documents, basis="concept",
                              whole_bid=concept.key in WHOLE_BID))
            continue
        words = _terms(" ".join(s.params.get("terms") or []) or s.label)
        needs.append(Need(s.key, s.label, terms=words, basis="wording" if words else "none"))
    return needs


# --------------------------------------------------------------------------------------------
# 2. The bid's inventory
# --------------------------------------------------------------------------------------------

def _lines(page) -> list[str]:
    return [ln.strip() for ln in (page.lines or (page.text or "").splitlines()) if ln.strip()]


def _title(page, kind: str) -> str:
    from app.services.bidder_documents import LABELS_BY_KEY

    lines = _lines(page)[:12]
    pattern = LABELS_BY_KEY.get(kind)
    hit = next((ln for ln in lines if pattern is not None and pattern.search(ln)), None)
    return re.sub(r"[#*|_]+", " ", hit or (lines[0] if lines else "")).strip()[:120]


def _dated(page) -> str | None:
    best = None
    for m in _DATE.finditer(ascii_digits(page.text or "")):
        d, mth, y = (int(x) for x in m.groups())
        try:
            when = date(y, mth, d)
        except ValueError:
            continue
        if when.year <= date.today().year + 1 and (best is None or when > best):
            best = when
    return best.isoformat() if best else None


def _firm(page) -> str | None:
    m = _FIRM.search(page.text or "")
    return (m.group("en") or m.group("mr") or "").strip(" .,-") or None if m else None


def inventory(files: list[tuple[str | None, str, ParsedDocument, list]]) -> list[BidDocument]:
    """Every typed document in a bid. Consecutive pages of one kind are one document unless a page
    carries its own heading of that kind, which starts the next one (two certificates back to back
    are two documents)."""
    out: list[BidDocument] = []
    for document_id, name, parsed, types in files:
        run: BidDocument | None = None
        for i, kind in enumerate(types or [], start=1):
            if kind is None or i > len(parsed.pages):
                run = None
                continue
            page = parsed.pages[i - 1]
            own_heading = page_type(page.text or page.raw_text or "") == kind
            if run is not None and run.kind == kind and run.last == i - 1 and not own_heading:
                run.last = i
                continue
            run = BidDocument(kind, LABELS.get(kind, kind), document_id, name, i, i,
                              title=_title(page, kind), dated=_dated(page), firm=_firm(page))
            out.append(run)
    return out


# --------------------------------------------------------------------------------------------
# 3. Fetch
# --------------------------------------------------------------------------------------------

def _by_heading(need: Need, files, covered: set[tuple[str | None, int]]) -> list[BidDocument]:
    """Pages whose heading is the document this requirement asks for, when no page type covers it:
    the concept's document headings, or the tender's own words for the document."""
    from app.services.concepts import CHECKLIST

    out: list[BidDocument] = []
    for document_id, name, parsed, _types in files:
        for page in parsed.pages:
            if (document_id, page.number) in covered:
                continue
            head = _lines(page)[:6]
            if not head or any(CHECKLIST.search(ln) for ln in head):
                continue  # a contents list names every document; it is none of them
            text = " ".join(head).lower()
            if need.headings is not None:
                hit = any(need.headings.search(ln) for ln in head)
            else:
                hit = len(need.terms) >= 2 and sum(t in text for t in need.terms) >= max(2, (len(need.terms) + 1) // 2)
            if hit:
                out.append(BidDocument(f"heading:{need.key}", need.label, document_id, name, page.number, page.number,
                                       title=re.sub(r"[#*|_]+", " ", head[0]).strip()[:120],
                                       dated=_dated(page), firm=_firm(page)))
    return out


def fetch(needs: list[Need], docs: list[BidDocument], files) -> list[RequirementDocs]:
    """Each requirement's documents in this bid, primary first, with the reason each was fetched.

    Whose name a document carries is not judged here: the firm on a page may be its issuer (a
    chartered accountant), a stamp vendor or the bidder, and a warning that is usually wrong would
    mislead the committee. Work certificates issued to another contractor are flagged where the
    certificate's own wording says who it was issued to (work_proof)."""
    order = {doc_id: n for n, (doc_id, *_rest) in enumerate(files)}
    out: list[RequirementDocs] = []
    for need in needs:
        wanted = need.primary + need.supporting
        if need.basis == "none" and not wanted:
            out.append(RequirementDocs(need.key, need.label, "not_a_gate" if need.key in NOTHING_TO_FETCH else "not_found",
                                       need.basis))
            continue
        got: list[Fetched] = []
        for d in docs:
            if d.kind in need.primary:
                got.append(Fetched(d, PRIMARY, f"a {d.label.lower()} is what states this"))
            elif d.kind in need.supporting:
                got.append(Fetched(d, SUPPORTING, f"a {d.label.lower()} corroborates it"))
        if need.headings is not None or need.terms:
            covered = {(f.doc.document_id, p) for f in got for p in f.doc.pages}
            for d in _by_heading(need, files, covered):
                got.append(Fetched(d, HEADING, "its heading names the document this requirement asks for"))
        got.sort(key=lambda f: (_ROLE_ORDER[f.role], order.get(f.doc.document_id, 0), f.doc.first))
        status = ("found" if any(f.role in (PRIMARY, HEADING) for f in got)
                  else "supporting_only" if got else "not_found")
        out.append(RequirementDocs(need.key, need.label, status, need.basis, got, wanted, whole_bid=need.whole_bid))
    return out


def masked(parsed: ParsedDocument, pages: set[int]) -> ParsedDocument:
    """The same document with only `pages` readable: every other page is blanked (its number kept,
    so page indexes and citations stay right) and its sentences are dropped."""
    shown = [p if p.number in pages else replace(p, text="", raw_text="", lines=[], tables=[], headings=[])
             for p in parsed.pages]
    out = ParsedDocument(pages=shown, sentences=[s for s in parsed.sentences if s.page in pages])
    if hasattr(parsed, "page_types"):
        out.page_types = parsed.page_types  # type: ignore[attr-defined]
    return out
