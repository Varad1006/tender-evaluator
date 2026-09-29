"""PMC works eligibility: the standard qualification criteria, located, stated or derived.

Every PMC works tender above Rs 2 lakh carries the same qualification template (clause 2.9 of
the PMC tender conditions; Annexure B "Qualification criteria" in the road volumes; the
"Parameters for Qualification Criteria" data sheet in drainage bundles). Thresholds come from the
tender's own estimated cost E and completion period:

    turnover        max annual turnover in any of the last 5 years >= 0.75 x E / (period in years)
    similar works   3 works >= 40% of E, or 2 works >= 50%, or 1 work >= 80% (last 5 years)
    bid capacity    (A x N x 1.5) - B  >= E
    key quantities  30% of the major BOQ quantities executed in any one of the last 5 years
    registration    compulsory up to Rs 1.5 Cr (PMC/PCMC/PWD/MSRDC/CPWD/NHAI or other Govt.)
    JV              not allowed

What the corpus taught us, and how it is handled:
  * The clauses are often only in scanned / legacy-font / Marathi pages. After OCR they are
    located and quoted verbatim (Marathi quotes stay Marathi); before OCR, a lossy Marathi text
    layer still lets us cite the page by Devanagari-digit signatures (४०%, ५०%, ८०%).
  * Printed numbers are unreliable: "Rs.423647 Lakhs (Rs. Four Lakhs ...)" is a template unit
    error; road volumes leave "Rs. .......... Lakhs" blank; one drainage tender computes on the
    GST-inclusive cost. The tender's own stated figure governs when it is legible and sane; the
    formula is a cross-check whose disagreement is surfaced, never silently overridden.
  * Similar-work "nature" is set per tender, sometimes by striking out the printed text and
    writing a replacement by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.adapters.pdf_parser import ParsedDocument, Sentence
from app.adapters.readability import ENGLISH_KINDS
from app.services.criteria_extraction import CriterionDraft
from app.services.pmc_facts import Fact
from app.services.values import format_inr, format_inr_short, parse_amounts, words_to_amount

TWO_LAKH = 200_000
ONE_POINT_FIVE_CR = 15_000_000


@dataclass
class DocView:
    document_id: str
    name: str
    doc_type: str
    parsed: ParsedDocument | None


@dataclass
class Located:
    doc: DocView
    page: int
    quote: str | None  # None when the only text is a lossy Marathi layer: cite, never quote
    clause: str | None = None
    section: str | None = None
    language: str = "en"
    method: str = "text"  # text | ocr


@dataclass
class PmcResult:
    applies: bool
    reason: str
    criteria: list[CriterionDraft] = field(default_factory=list)
    claimed: set[tuple[str, int, str]] = field(default_factory=set)  # (doc id, page, sentence) used here
    warnings: list[str] = field(default_factory=list)


def _kind(doc: DocView, page: int) -> str:
    return doc.parsed.pages[page - 1].quality.kind if doc.parsed and 0 < page <= len(doc.parsed.pages) else ""


def _sentences(docs: list[DocView], kinds: set[str]):
    for d in docs:
        if d.parsed is None:
            continue
        for s in d.parsed.sentences:
            if _kind(d, s.page) in kinds:
                yield d, s


def _loc(d: DocView, s: Sentence, language: str = "en") -> Located:
    return Located(d, s.page, s.text, s.clause, s.section, language, "ocr" if _kind(d, s.page).startswith("ocr") else "text")


def find_en(docs: list[DocView], pattern: str) -> Located | None:
    rx = re.compile(pattern, re.I | re.S)
    for d, s in _sentences(docs, ENGLISH_KINDS):
        if rx.search(s.text):
            return _loc(d, s)
    return None


def _squash(text: str) -> str:
    """pdfium/pdfminer Devanagari carries private-use glyph codes and splits words ("पंजीकृ त")."""
    return re.sub(r"[\s\ue000-\uf8ff]", "", text or "")


def find_mr(docs: list[DocView], *signatures: str) -> Located | None:
    """Marathi clause, most precise first:
      1. one OCR sentence or one OCR table row holds every signature  -> quoted
      2. one OCR page holds them (a clause split over list items)       -> quoted: the first matching sentence
      3. a lossy text layer holds them (page-level)                      -> cited, not quoted
    Signatures are chosen to survive lossy extraction (Devanagari digits such as ४०%)."""
    ocr_kinds = {"ocr_mr", "ocr_en"}
    for d, s in _sentences(docs, ocr_kinds):
        text = " ".join(s.row) if s.in_table and s.row else s.text
        if all(sig in text for sig in signatures):
            quote = text if s.in_table and s.row else s.text
            return Located(d, s.page, quote, s.clause, s.section, "mr", "ocr")
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            if p.quality.kind in ocr_kinds and all(sig in p.text for sig in signatures):
                first = next((s for s in d.parsed.sentences if s.page == p.number and signatures[0] in s.text), None)
                return Located(d, p.number, first.text if first else None, language="mr", method="ocr")
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            # lossy Devanagari layers, including mixed English/Marathi pages
            if p.quality.kind in ("marathi", "english") and all(_squash(sig) in _squash(p.raw_text) for sig in signatures):
                return Located(d, p.number, None, language="mr")
    return None


# --------------------------------------------------------------------------------------
# The drainage "Parameters for Qualification Criteria" sheet (handwritten figures)
# --------------------------------------------------------------------------------------

@dataclass
class QualificationSheet:
    doc: DocView
    page: int
    turnover: float | None = None
    similar: list[float] = field(default_factory=list)
    bid_capacity: float | None = None
    nature: str | None = None
    struck_nature: str | None = None
    rows: dict[str, str] = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)
    items_page: int | None = None


def _lakh_amounts(text: str) -> list[float]:
    out = [a.value for a in parse_amounts(text)]
    if not out:  # "Rs. 52.5 Lacs" with the unit separated by a filled blank
        out = [float(x) * 100_000 for x in re.findall(r"Rs\.?\s*(\d+(?:\.\d+)?)\s*(?:lacs?|lakhs?)", text, re.I)]
    return out


def read_qualification_sheet(docs: list[DocView]) -> QualificationSheet | None:
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            if "Parameters for Qualification Criteria" not in p.text:
                continue
            sheet = QualificationSheet(d, p.number)
            for rows in p.tables:
                for row in rows:
                    joined = " | ".join(row)
                    last = row[-1] if row else ""
                    if re.search(r"Maximum Turnover", joined, re.I):
                        vals = _lakh_amounts(last)
                        sheet.turnover = vals[0] if vals else None
                        sheet.rows["turnover"] = joined
                    elif re.search(r"similar works", joined, re.I):
                        sheet.similar = _lakh_amounts(last)
                        sheet.rows["similar"] = joined
                    elif re.search(r"Bid Capacity", joined, re.I):
                        vals = _lakh_amounts(last)
                        sheet.bid_capacity = vals[0] if vals else None
                        sheet.rows["bid_capacity"] = joined
            hand = getattr(p, "handwritten", [])
            struck = getattr(p, "struck", [])
            if hand:
                sheet.nature = " / ".join(dict.fromkeys(h.strip(" ,.") for h in hand))
            if struck:
                sheet.struck_nature = struck[0]
            if not sheet.nature:
                m = re.search(r"completed works\s*\(([^)]{5,120})\)", sheet.rows.get("similar", ""), re.I)
                sheet.nature = m.group(1).strip() if m else None
            # Key quantities: a "Work Description | Qty | Unit" table on this page or the next.
            for q in d.parsed.pages[p.number - 1: p.number + 1]:
                for rows in q.tables:
                    head_idx = next((i for i, r in enumerate(rows[:3])
                                     if "qty" in " ".join(r).lower() and re.search(r"unit|description", " ".join(r), re.I)), None)
                    if head_idx is not None:
                        header = [c.lower() for c in rows[head_idx]]
                        qcol = next(i for i, c in enumerate(header) if "qty" in c)
                        dcol = next((i for i, c in enumerate(header) if "description" in c or "item" in c), 1)
                        ucol = next((i for i, c in enumerate(header) if "unit" in c), None)
                        for row in rows[head_idx + 1:]:
                            qty = row[qcol].replace(",", "") if qcol < len(row) else ""
                            name = row[dcol] if dcol < len(row) else ""
                            if re.fullmatch(r"\d+(?:\.\d+)?", qty) and re.search(r"[A-Za-z]{3}", name):
                                sheet.items.append({"item": name, "qty": float(qty),
                                                    "unit": (row[ucol] if ucol is not None and ucol < len(row) else "").lower()})
                        if sheet.items:
                            sheet.items_page = q.number
                # handwritten lines under the table: "... stainless steel gate ... = 1 nos"
                for line in q.lines:
                    m = re.search(r"([A-Za-z][^=|]{5,120}?)\s*=\s*(\d+(?:\.\d+)?)\s*" + _UNIT + r"\b", line, re.I)
                    if m and not any(m.group(1).strip().lower().startswith(i["item"].lower()[:12]) for i in sheet.items):
                        name = re.sub(r"^\d+\)\s*", "", m.group(1)).strip()
                        sheet.items.append({"item": name[:80], "qty": float(m.group(2)), "unit": _unit(m.group(3))})
                        sheet.items_page = sheet.items_page or q.number
            return sheet
    return None


# --------------------------------------------------------------------------------------

def _draft(key: str, label: str, bucket: str, datatype: str, operator: str, tier: str, display: str,
           where: Located | None, fallback_doc: DocView | None, value: float | None = None, unit: str | None = None,
           params: dict | None = None, derivation: dict | None = None, notes: list[str] | None = None) -> CriterionDraft:
    notes = list(notes or [])
    if where is None:
        status = "derived"
        notes.insert(0, "Standard PMC works criterion, not found in any readable page of this bundle"
                        + (f" (likely in '{fallback_doc.name}', which could not be read)" if fallback_doc else "")
                        + ". Confirm against the tender before relying on it.")
    elif where.quote is None:
        status = "located_marathi"
        notes.insert(0, f"Clause located in Marathi on p.{where.page} of {where.doc.name}. The PDF's text layer "
                        "drops Devanagari conjuncts, so the page is cited but not quoted — read the page itself.")
    else:
        status = "located"
        if where.method == "ocr":
            notes.insert(0, f"Read by OCR (Chandra) from a scanned / non-text page ({where.doc.name} p.{where.page}).")
    sentence = Sentence(page=where.page, text=where.quote or "", section=where.section or "", clause=where.clause) if where else None
    derivation = dict(derivation or {})
    if where:
        derivation.update({"source_language": where.language, "source_method": where.method})
    return CriterionDraft(
        key=key, label=label, bucket=bucket, datatype=datatype, operator=operator, tier=tier,
        threshold_display=display, source=sentence, threshold_value=value, threshold_unit=unit,
        params=params or {}, extraction_method="pmc_standard", status=status, derivation=derivation, notes=notes,
        source_document_id=(where.doc.document_id if where else (fallback_doc.document_id if fallback_doc else None)),
    )


def _close(a: float | None, b: float | None, tol: float = 0.015) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(2.0, tol * abs(b))


@dataclass
class StatedAmount:
    digits: float | None  # the number as printed
    unit: float  # multiplier of the printed unit word (1 when none)
    words: float | None  # the amount in words, when given
    blank: bool
    printed: str

    @property
    def literal(self) -> float | None:
        return self.digits * self.unit if self.digits is not None else None


def _stated_turnover(sentence: str) -> StatedAmount:
    """'... i.e.,Rs.423647 Lakhs (Rs.Four Lakhs ... Rupees .)' -> digits, unit, words, blank."""
    blank = bool(re.search(r"Rs\.?\s*[.…]{4,}", sentence))
    m = re.search(r"i\.?\s*e\.?\s*,?\s*Rs\.?\s*([\d,]+(?:\.\d+)?)\s*(Lakhs?|Lacs?|Crores?|Cr\.?)?", sentence, re.I)
    digits = float(m.group(1).replace(",", "")) if m and m.group(1) else None
    unit_word = (m.group(2) or "").lower() if m else ""
    unit = 10_000_000 if unit_word.startswith("cr") else 100_000 if unit_word else 1
    w = re.search(r"\(\s*Rs\.?\s*([A-Za-z][A-Za-z\s]+?(?:Rupees|Paise)[A-Za-z\s]*?)\s*\.?\s*\)", sentence)
    return StatedAmount(digits, unit, words_to_amount(w.group(1)) if w else None, blank, m.group(0) if m else "")


def _reconcile(stated: float | None, e_put: float | None, e_total: float | None, fn, what: str) -> tuple[float | None, list[str], str]:
    """Returns (threshold to use, notes, outcome). The tender's stated figure governs when present."""
    f_put = fn(e_put) if e_put else None
    f_total = fn(e_total) if e_total else None
    if stated is None:
        return f_put, [], "formula"
    if _close(stated, f_put):
        return stated, [f"The tender's figure {format_inr(stated)} matches the formula on the estimated cost put to tender."], "match"
    if _close(stated, f_total):
        return stated, [f"The tender states {format_inr(stated)} for {what}, computed on the total estimated cost incl. GST "
                        f"({format_inr(e_total)}) rather than the cost put to tender ({format_inr(e_put)}), which would give "
                        f"{format_inr(f_put)}. The tender's figure is used; the committee should confirm the base."], "base_total"
    note = f"The tender states {format_inr(stated)} for {what}"
    if f_put:
        note += f", but the standard formula gives {format_inr(f_put)}"
    return stated, [note + ". The tender's figure is used; evaluator to confirm."], "mismatch"


def build(docs: list[DocView], facts: dict[str, Fact], authority_is_pmc: bool) -> PmcResult:
    E_fact, total_fact, months_fact = facts["estimated_cost"], facts["total_estimated_cost"], facts["completion_months"]
    E = float(E_fact.value) if E_fact.value is not None else None
    E_total = float(total_fact.value) if total_fact.value is not None else None
    months = int(months_fact.value) if months_fact.value is not None else None
    works = (str(facts["tender_category"].value or "").upper() == "WORKS"
             or str(facts["form_of_contract"].value or "").upper() in ("PERCENTAGE", "ITEM RATE")
             or facts["contract_type"].value is not None
             or find_en(docs, r"Percentage Rate B-?1|B-?1 Form|FINAL Tender Conditions – To All Works") is not None)

    if not authority_is_pmc or not works:
        return PmcResult(False, "Not a PMC works tender; the PMC standard works criteria do not apply.")
    if E is not None and E <= TWO_LAKH:
        return PmcResult(False, f"Estimated cost {format_inr(E)} is below Rs 2 lakh; clause 2.9 criteria do not apply.")

    result = PmcResult(True, "PMC works tender above Rs 2 lakh: standard qualification criteria apply.")
    if E_fact.status == "conflict":
        result.warnings.append("Estimated cost differs across the bundle's documents — formula thresholds are not computed until it is confirmed.")
        E = None
    if months_fact.status == "conflict":
        result.warnings.append("Completion period differs across documents — the turnover formula is not computed.")
        months = None

    unreadable = next((d for d in docs if d.doc_type in ("conditions", "datasheet") and d.parsed is not None
                       and not d.parsed.readable_pages), None)
    sheet = read_qualification_sheet(docs)
    inputs = {"estimated_cost": E, "total_estimated_cost": E_total, "completion_months": months}

    def claim(where: Located | None):
        if where and where.quote:
            result.claimed.add((where.doc.document_id, where.page, where.quote))

    def sheet_loc(row_key: str) -> Located | None:
        return Located(sheet.doc, sheet.page, sheet.rows[row_key], method="ocr" if _kind(sheet.doc, sheet.page).startswith("ocr") else "text") \
            if sheet and row_key in sheet.rows else None

    # 1. Turnover -----------------------------------------------------------------------
    clause = find_en(docs, r"Maximum Annual (?:Financial )?Turnover")
    where = sheet_loc("turnover") or clause or find_mr(docs, "उलाढाल", "७५")
    claim(clause)
    claim(where)
    years = months / 12 if months else None
    fn = (lambda e: round(e * 0.75 / years, 2)) if years else (lambda e: None)
    notes: list[str] = []
    stated = sheet.turnover if sheet and sheet.turnover else None
    clause_value = None
    if clause is not None:
        st = _stated_turnover(clause.quote or "")
        f_put, f_total = (fn(E) if E else None), (fn(E_total) if E_total else None)
        targets = [f for f in (f_put, f_total) if f]
        if st.blank:
            notes.append("The tender leaves this amount blank; the threshold is computed from the formula.")
        elif st.unit > 1 and any(_close(st.literal, f) for f in targets):
            clause_value = st.literal  # "Rs. 10.85 Lakhs" used correctly
        elif st.unit > 1 and st.digits is not None and (any(_close(st.digits, f) or _close(st.words, f) for f in targets)
                                                        or _close(st.words, st.digits)):
            clause_value = st.words if st.words is not None else st.digits
            agree = ", the amount in words" if _close(st.words, st.digits) else ""
            notes.append(f"The tender prints '{st.printed.strip()}' — a template unit error (read literally that is "
                         f"{format_inr_short(st.literal)}). The digits as rupees{agree}"
                         + (f" and the formula agree on {format_inr(clause_value)}." if any(_close(clause_value, f) for f in targets)
                            else f" agree on {format_inr(clause_value)}."))
        elif st.digits is not None or st.words is not None:
            clause_value = st.words if st.words is not None else st.literal
        if stated is not None and clause_value is not None and not _close(stated, clause_value):
            notes.append(f"The qualification sheet says {format_inr(stated)} but the clause on p.{clause.page} of "
                         f"{clause.doc.name} gives {format_inr(clause_value)}; the sheet's figure is used.")
        elif stated is None and clause_value is not None and not any(_close(clause_value, f) for f in targets):
            stated = clause_value  # the tender's own, non-formula figure governs (reconciled below)
    threshold, rec_notes, outcome = _reconcile(stated, E, E_total, fn, "turnover")
    result.criteria.append(_draft(
        "pmc_max_turnover", "Maximum Annual Turnover (any of last 5 years)", "Financial", "currency", ">=",
        "deterministic", f"≥ {format_inr(threshold)}" if threshold else "≥ 75% of annual value (E × 0.75 ÷ years)",
        where, unreadable, threshold, "INR", params={"window_years": 5, "basis": "max_annual"},
        derivation={"formula": "E × 0.75 ÷ (completion months ÷ 12)", **inputs, "stated": stated, "outcome": outcome},
        notes=notes + rec_notes,
    ))

    # 2. Similar works (40 / 50 / 80) -----------------------------------------------------
    where = sheet_loc("similar") or find_en(docs, r"(?:three|3)\s+(?:completed|similar).{0,160}40\s*%") \
        or find_mr(docs, "४०%", "५०%", "८०%") or find_mr(docs, "४०%", "५०%")
    claim(where)
    pcts = (0.4, 0.5, 0.8)
    stated_opts = sheet.similar if sheet and len(sheet.similar) == 3 else None
    notes = []
    if stated_opts:
        mins = stated_opts
        if E and all(_close(s, p * E) for s, p in zip(mins, pcts)):
            notes.append("The tender's figures match 40/50/80% of the estimated cost put to tender.")
        elif E_total and all(_close(s, p * E_total) for s, p in zip(mins, pcts)):
            notes.append(f"The tender's figures are 40/50/80% of the total estimated cost incl. GST ({format_inr(E_total)}), "
                         "not of the cost put to tender. The tender's figures are used; the committee should confirm the base.")
        else:
            notes.append("The tender's figures do not match 40/50/80% of either cost base; evaluator to confirm.")
    else:
        mins = [round(p * E, 2) if E else None for p in pcts]
    options = [{"count": c, "min_value": m, "pct": int(p * 100)} for c, m, p in zip((3, 2, 1), mins, pcts)]
    nature = sheet.nature if sheet and sheet.nature else None
    inferred_nature = None
    if not nature:
        dept = str(facts["department"].value or "").lower()
        category = str(facts.get("product_category").value or "").lower() if facts.get("product_category") else ""
        inferred_nature = next((n for k, n in (("road", "road works"), ("drainage", "drainage / sewer line works"),
                                               ("electrical", "electrical works"), ("water", "water supply works"),
                                               ("building", "building works"), ("bhavan", "building works"))
                                if k in dept or k in category), None)
    if sheet and sheet.struck_nature and nature:
        notes.append(f"Similar-work nature was changed by hand: printed '{sheet.struck_nature}' is struck out and "
                     f"replaced with '{nature}'.")
    display = (" or ".join(f"{o['count']} × ≥ {format_inr_short(o['min_value'])}" for o in options)
               if all(o["min_value"] for o in options) else "3 × ≥ 40% of E, or 2 × ≥ 50%, or 1 × ≥ 80%")
    result.criteria.append(_draft(
        "pmc_similar_works", "Similar Completed Works (last 5 years)", "Technical", "composite", "similar_works_any",
        "llm_judge", display, where, unreadable,
        params={"window_years": 5, "options": options,
                "nature": nature or inferred_nature or "same nature as this work (see tender)",
                "nature_inferred": nature is None},
        derivation={"formula": "3 works ≥ 40% of E, or 2 ≥ 50%, or 1 ≥ 80%", **inputs,
                    "stated": stated_opts},
        notes=notes if nature else notes + ["Similar-work nature is set per tender (in drainage bundles it is "
                                            "handwritten on the qualification-parameters sheet)."],
    ))

    # 2b. Road volumes add a single-large-work condition (last 3 years) that can contradict 2.
    single_hit = find_en(docs, r"single work of similar nature.{0,200}not less than the 50\s*%")
    if single_hit:
        claim(single_hit)
        single = (0.5 * E if E <= 100_000_000 else max(0.6 * E, 60_000_000)) if E else None
        result.criteria.append(_draft(
            "pmc_single_similar_work", "One Similar Work of Similar Magnitude (last 3 years)", "Technical", "composite",
            "similar_works_any", "llm_judge",
            f"1 × ≥ {format_inr_short(single)}" if single else "1 × ≥ 50% of E (1–10 Cr) or max(60%, ₹6 Cr) above 10 Cr",
            single_hit, None, single, "INR",
            params={"window_years": 3, "options": [{"count": 1, "min_value": single, "pct": 50}]},
            derivation={"formula": "50% of E for 1–10 Cr; above 10 Cr, 60% of E or ₹6 Cr whichever is more", **inputs},
            notes=["This volume states both this rule (last 3 years) and the 40/50/80% rule (last 5 years). "
                   "The committee must decide which governs."],
        ))

    # 3. Bid capacity ----------------------------------------------------------------------
    where = sheet_loc("bid_capacity") or find_en(docs, r"\(\s*A\s*[x×]\s*N\s*[x×]\s*1\.?5\s*\)\s*-\s*B|bid capacity") \
        or find_mr(docs, "१.५", "बीड")
    claim(where)
    stated_bc = sheet.bid_capacity if sheet else None
    bc, notes, outcome = _reconcile(stated_bc, E, E_total, lambda e: e, "bid capacity")
    result.criteria.append(_draft(
        "pmc_bid_capacity", "Bid Capacity (A × N × 1.5 − B)", "Financial", "currency", ">=", "deterministic",
        f"≥ {format_inr(bc)}" if bc else "≥ estimated cost put to tender", where, unreadable, bc, "INR",
        params={"certificate": "Chartered Accountant, specific to this tender"},
        derivation={"formula": "(A × N × 1.5) − B ≥ E; A = max annual works value (last 5 yrs), N = years, "
                               "B = existing commitments", **inputs, "stated": stated_bc, "outcome": outcome},
        notes=notes,
    ))

    # 4. Key quantities ----------------------------------------------------------------------
    qty_clause = find_en(docs, r"quantities to be executed|30\s*%\s*of the quantity|30\s*%\s*Quantity of major items")
    items, items_where = [], None
    if sheet and sheet.items:
        items = sheet.items
        items_where = Located(sheet.doc, sheet.items_page or sheet.page,
                              "; ".join(f"{i['item']} {i['qty']:g} {i['unit']}".strip() for i in items),
                              method="ocr" if _kind(sheet.doc, sheet.items_page or sheet.page).startswith("ocr") else "text")
    elif qty_clause:
        items = _key_quantities(docs, qty_clause)
    listed = _listed_quantity_items(qty_clause) if qty_clause and not items else []
    where = items_where or qty_clause or find_mr(docs, "३०%")
    claim(where)
    if items:
        qty_notes = []
    elif listed:
        qty_notes = [f"The tender's key-quantity table (p.{qty_clause.page}) lists {', '.join(listed)} but leaves the "
                     "quantities blank: take 30% of each item's quantity from the BOQ."]
    else:
        qty_notes = ["Item quantities are not stated in any readable page."]
    # The window is 5 years in the drainage/building template, 3 in road volumes ("in any one
    # financial year during last three years"): read it rather than assume.
    qty_years = 5
    wm = re.search(r"last\s*(three|five|\d)\s*(?:financial\s*)?years", (qty_clause.quote or "") if qty_clause else "", re.I)
    if wm:
        qty_years = {"three": 3, "five": 5}.get(wm.group(1).lower()) or int(wm.group(1))
    result.criteria.append(_draft(
        "pmc_key_quantities", f"Key BOQ Quantities Executed (any one year, last {qty_years})", "Technical", "composite",
        "quantities", "deterministic",
        ("; ".join(f"{i['item']} ≥ {i['qty']:g} {i['unit']}".strip() for i in items) if items
         else "30% of major BOQ quantities (3–5 items)"),
        where, unreadable, params={"items": items, "window_years": qty_years, **({"listed_items": listed} if listed else {})},
        derivation={"formula": "30% of the quantity of the 3–5 major items, in any one financial year"},
        notes=qty_notes,
    ))

    # 5. Registration (only up to Rs 1.5 Cr) ------------------------------------------------
    if E is None or E <= ONE_POINT_FIVE_CR:
        where = find_en(docs, r"Registration Certificate\s+up\s*to\s+1\.5|registration compulsory") \
            or find_mr(docs, "नोंदणी", "१.५०") or find_mr(docs, "पंजीकृत", "१.५०")
        claim(where)
        result.criteria.append(_draft(
            "pmc_registration", "Valid Contractor Registration (Govt./Semi-Govt.)", "Eligibility", "boolean", "exists",
            "deterministic", "Required (tenders up to ₹1.5 Cr)", where, unreadable,
            params={"terms": ["registration"], "bodies": ["PMC", "PCMC", "PWD", "MSRDC", "CPWD", "NHAI"]},
            derivation={"rule": "compulsory when E ≤ ₹1.5 Cr", **inputs},
        ))

    # 5b. Earnest money deposit -----------------------------------------------------------------
    emd_fact = facts["emd"]
    emd = float(emd_fact.value) if emd_fact.value is not None else None
    where = find_en(docs, r"earnest\s*money.{0,120}(?:rejected|shall|must|mandatory|payable|paid)|without\s*(?:the\s*)?earnest\s*money") \
        or find_mr(docs, "इसारा") or find_mr(docs, "बयाणा") or find_mr(docs, "बयाना")
    if where is None and emd_fact.sources:
        src = next((x for x in emd_fact.sources if x.page), None)
        doc = next((d for d in docs if src and d.document_id == src.document_id), None)
        if src and doc:
            where = Located(doc, src.page, src.quote, method="ocr" if src.ocr else "text")
    claim(where)
    notes = []
    if emd is not None and E:
        expected = 0.01 * E if E <= ONE_POINT_FIVE_CR else max(0.005 * E, 150_000)
        if not _close(emd, expected, tol=0.02):
            notes.append(f"The tender's EMD {format_inr(emd)} differs from the PMC rule "
                         f"({'1% of E' if E <= ONE_POINT_FIVE_CR else '0.5% of E or ₹1.5 lakh, whichever is more'} = "
                         f"{format_inr(round(expected))}). The tender's figure applies.")
    exemption = find_en(docs, r"exemption\s*certificate\s*will\s*not\s*be\s*accepted")
    if exemption:
        notes.append("No EMD exemption certificate (e.g. MSE/NSIC) is accepted in lieu of payment.")
    if emd is not None or where is not None:
        result.criteria.append(_draft(
            "pmc_emd", "Earnest Money Deposit Paid Online", "Commercial", "currency", ">=", "deterministic",
            f"{format_inr(emd)} via RTGS/NEFT" if emd else "As stated in the tender (online)", where, unreadable,
            emd, "INR", params={"terms": ["EMD", "earnest"]},
            derivation={"rule": "1% of E up to ₹1.5 Cr; above, 0.5% of E or ₹1.5 lakh whichever is more", **inputs},
            notes=notes,
        ))

    # 5c. Bid conditions (not eligibility gates, but they decide L1 in practice) ------------------
    for key, label, en, mr, display in (
        ("bid_rate_analysis", "Rate Analysis Required if Quoting ≥10% Below Estimate",
         r"rate\s*analys[ie]s", ("दरपृथ",), "Item-wise rate analysis with the bid when ≥ 10% below"),
        ("bid_additional_security", "Additional Performance Security if L1 Quotes Below Estimate",
         r"Additional\s*Performance\s*Security", ("Additional Performance Security",), "Deposit within 8 days, else L2 is approached"),
        ("bid_validity", "Bid Validity Period",
         r"valid(?:ity)?\b.{0,80}120\s*days|120\s*days\s*from\s*the\s*last\s*date", ("१२० दिवस",), "120 days"),
    ):
        where = find_en(docs, en) or find_mr(docs, *mr)
        claim(where)
        if where:
            draft = _draft(key, label, "Commercial", "boolean", "exists", "deterministic", display, where, None,
                           params={"terms": [label.split()[0]]})
            draft.gating = False
            result.criteria.append(draft)

    # 6. No joint venture ---------------------------------------------------------------------
    where = find_en(docs, r"Joint\s*venture.{0,60}(?:not\s*allowed|not\s*permitted)|Consortium\s*is\s*not\s*allowed|No\s*consortium") \
        or find_mr(docs, "Joint Venture")
    claim(where)
    if where:
        result.criteria.append(_draft(
            "no_joint_venture", "Sole Bidder — Joint Venture / Consortium Not Allowed", "Eligibility", "boolean",
            "exists", "deterministic", "Sole bidder only", where, None, params={"terms": ["sole"]},
        ))
    return result


_UNIT = r"(cum|cu\.?\s*m|sqm|sq\.?\s*m|mt|rmt|r\.?\s*m|nos?|kg)"
_QTY_LINE = re.compile(r"(?:\d+(?:\.\d+)?\s*" + _UNIT + r"?\s*)+", re.I)


def _unit(u: str | None) -> str:
    u = (u or "").lower().replace(" ", "").replace(".", "")
    return {"cum": "cum", "cum3": "cum", "sqm": "sqm", "mt": "MT", "rmt": "rmt", "rm": "rmt", "no": "nos", "nos": "nos",
            "kg": "kg"}.get(u, u)


_HEAD_CELL = re.compile(r"^(.+?)\s*\(\s*" + _UNIT + r"\s*\)\s*$", re.I)
_VALUE_CELL = re.compile(r"^(.*?)[\s-]*(\d+(?:\.\d+)?)\s*" + _UNIT + r"?\s*$", re.I)


def _quantities_from_table(rows: list[list[str]]) -> list[dict]:
    """Column-aware reading of the key-quantity grid. Values sit under their own column header and
    most cells are blank ('| | | 103 |' under 'GSB/WMM(cum)'), and a clerk may type a different
    item name in a row above its value ('600MM RCC PIPE' over '65 RMT')."""
    items: list[dict] = []
    header: list[tuple[str, str] | None] = []
    pending: list[str] = []
    for row in rows:
        cells = [c.strip() for c in row]
        heads = [(_HEAD_CELL.match(c)) for c in cells]
        if sum(1 for h in heads if h) >= 2:
            header = [(h.group(1).strip(), _unit(h.group(2))) if h else None for h in heads]
            pending = []
            continue
        filled = [c for c in cells if c]
        if not filled:
            continue
        values = [_VALUE_CELL.match(c) if c else None for c in cells]
        if all(v for v, c in zip(values, cells) if c):
            for j, (v, c) in enumerate(zip(values, cells)):
                if not c:
                    continue
                prefix, qty, unit = v.group(1).strip(" -"), float(v.group(2)), v.group(3)
                name = pending[j] if j < len(pending) and pending[j] else (header[j][0] if j < len(header) and header[j] else "")
                if prefix and not re.fullmatch(r"\d*", prefix):
                    name = f"{name} ({prefix})" if name and not pending else (name or prefix)
                if not name:
                    continue
                if not unit and j < len(header) and header[j] and not (j < len(pending) and pending[j]):
                    unit = header[j][1]
                items.append({"item": name, "qty": qty, "unit": _unit(unit)})
            pending = []
        elif all(re.search(r"[A-Za-z]{3}", c) for c in filled):
            pending = cells  # custom item names; their values follow in the next row
    return items


_HEAD_LOOSE = re.compile(r"^([A-Za-z][A-Za-z/ &.-]{1,40}?)\s*\(?\s*" + _UNIT + r"\s*\)?\s*$", re.I)


def _listed_quantity_items(hit: Located) -> list[str]:
    """Item names of a key-quantity grid printed without any quantities (a template left blank):
    'BC (cum) | DBM (cum) | GSB/ WMM (cum)' / 'Concrete Cum | Paver Blocks Sqm | Steel (MT)'."""
    page = hit.doc.parsed.pages[hit.page - 1]
    for rows in page.tables:
        cells = [c.strip() for r in rows for c in r if c and c.strip()]
        heads = [_HEAD_LOOSE.match(c) for c in cells]
        if len(cells) >= 3 and all(heads):
            return [_item_name(h.group(1)) for h in heads]
    return []


# Frequent OCR misreads of BOQ item names in the key-quantity grid.
_ITEM_FIXES = [(r"\bMMM\b", "WMM"), (r"\bPever\b", "Paver"), (r"\bMurrum\b", "Murum"), (r"\bG\.?S\.?B\b", "GSB")]


def _item_name(name: str) -> str:
    name = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", name).strip())
    for pat, fix in _ITEM_FIXES:
        name = re.sub(pat, fix, name, flags=re.I)
    return name


def _key_quantities(docs: list[DocView], hit: Located) -> list[dict]:
    """Quantities printed under the key-quantity clause. Two shapes seen in the corpus:
    'BC(cum) DBM(cum) GSB/WMM(cum)' over '57 cum 87 cum 291 cum', and 'Paving blocks' over '323'.
    Only the ~20 lines after the clause are read, so clause numbers elsewhere never leak in."""
    page = hit.doc.parsed.pages[hit.page - 1]
    for rows in page.tables:
        if any(sum(1 for c in r if _HEAD_CELL.match(c.strip())) >= 2 for r in rows):
            found = _quantities_from_table(rows)
            if found:
                return found
    lines = [re.sub(r"\s+", " ", ln.replace("|", " ")).strip() for ln in page.lines]
    start = next((i for i, ln in enumerate(lines) if re.search(r"quantit", ln, re.I)), None)
    if start is None:
        return []
    window = lines[start + 1: start + 22]
    items = []
    for i, line in enumerate(window[:-1]):
        nxt = window[i + 1]
        if not _QTY_LINE.fullmatch(nxt):
            continue
        values = re.findall(r"(\d+(?:\.\d+)?)\s*" + _UNIT + "?", nxt, re.I)
        names = re.findall(r"([A-Za-z][A-Za-z/ ]*?)\s*\(\s*" + _UNIT + r"\s*\)", line, re.I)
        if names:
            for (name, unit), (qty, _) in zip(names, values):
                items.append({"item": name.strip(), "qty": float(qty), "unit": _unit(unit)})
        elif re.fullmatch(r"[A-Za-z][A-Za-z /&-]{2,40}", line) and len(values) == 1:
            items.append({"item": line.strip(), "qty": float(values[0][0]), "unit": _unit(values[0][1])})
    return items
