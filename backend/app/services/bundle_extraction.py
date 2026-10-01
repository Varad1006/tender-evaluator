"""Tender bundle -> criteria registry + key facts. The extraction half of the hybrid pipeline.

Order matters and encodes what the PMC corpus taught us:
  1. Key facts from the structured portal forms (reconciled across every document)
  2. PMC standard works criteria (located / stated / derived) — thresholds need the facts
  3. Sentence rules on readable English pages, skipping sentences step 2 already used
  4. Enumerated checklists (documentary requirements with no "shall" of their own)
  5. Award mechanism and metadata
The same function runs twice per tender: once on the local text layer (seconds), and again after
OCR has filled in the pages the local pass could not read.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from app.adapters.pdf_parser import ParsedDocument, Sentence
from app.adapters.readability import ENGLISH_KINDS
from app.services import checklist_extraction, experience_ladder, indexation, pmc_eligibility
from app.services.bundle import NON_ELIGIBILITY_TYPES
from app.services.criteria_extraction import (
    RULES,
    MULTI,
    CriterionDraft,
    TenderExtraction,
    _metadata,
    llm_supplement,
    rule_generic_document,
)
from app.services.pmc_eligibility import DocView
from app.services.pmc_facts import FactCollector
from app.services.values import ascii_digits

PMC_SKIP_RULES = {"rule_completion", "rule_warranty", "rule_manufacturer", "rule_site_engineer", "rule_experience"}


def _tender_fy(ref_no: str | None, docs: list[DocView]) -> int | None:
    """The financial year the tender belongs to: from its reference ("…/2026-27/…"), else from the
    latest date its documents print (an abstract's footer, a notice's date). It is the current
    price level a stated escalation rule brings older figures up to."""
    m = re.search(r"\b(20\d{2})\s*-\s*(\d{2})\b", ref_no or "")
    if m and (int(m.group(1)) + 1) % 100 == int(m.group(2)):
        return int(m.group(1))
    latest = None
    for d in docs:
        if d.parsed is None:
            continue
        for p in d.parsed.pages:
            for dm in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b", ascii_digits(p.raw_text or p.text or "")):
                day, month, year = (int(x) for x in dm.groups())
                if 1 <= day <= 31 and 1 <= month <= 12 and year <= date.today().year + 1:
                    latest = max(latest or (0, 0, 0), (year, month, day))
    if latest is None:
        return None
    return latest[0] if latest[1] >= 4 else latest[0] - 1


def _english(doc: DocView):
    if doc.parsed is None:
        return
    kinds = {p.number: p.quality.kind for p in doc.parsed.pages}
    for s in doc.parsed.sentences:
        if kinds.get(s.page) in ENGLISH_KINDS:
            yield s


def _award(docs: list[DocView]) -> tuple[str, dict | None, list[str]]:
    found: list[tuple[str, dict]] = []
    gate: dict | None = None
    for d in docs:
        for s in _english(d):
            t = s.text
            base = {"page": s.page, "quote": s.text, "clause": s.clause, "document_id": d.document_id, "doc_name": d.name}
            g = re.search(r"minimum (?:score )?of (\d{2})\s*%\s*of (?:the )?total marks", t, re.I)
            if g and gate is None:
                gate = {**base, "technical_min_pct": int(g.group(1)), "gate_quote": s.text}
            if re.search(r"\baward", t, re.I):
                if re.search(r"\bQCBS\b|quality\s*(?:and|&|-)\s*cost", t, re.I):
                    w = re.search(r"(\d{2})\s*[:/]\s*(\d{2})", t) or re.search(
                        r"technical\D{0,40}(\d{2})\s*%\D{0,60}financial\D{0,40}(\d{2})\s*%", t, re.I)
                    weights = {"technical": int(w.group(1)), "financial": int(w.group(2))} if w else None
                    found.append(("QCBS", {**base, "weights": weights, "label": "QCBS — Quality and Cost Based Selection"
                                           + (f" ({weights['technical']}:{weights['financial']})" if weights else "")}))
                elif re.search(r"\bL-?1\b|lowest|least cost", t, re.I):
                    if re.search(r"technically\s+(?:qualified|responsive)", t, re.I):
                        label = "L1 — Least Cost Among Technically Qualified Bidders"
                    elif re.search(r"lowest evaluated", t, re.I):
                        label = "L1 — Lowest Evaluated Responsive Bid"
                    else:
                        label = "L1 — Lowest Quoted Amount"
                    found.append(("L1", {**base, "label": label}))
            elif re.search(r"Percentage Rate B-?1", t, re.I):
                found.append(("L1", {**base, "label": "L1 — Lowest Percentage Rate on Estimated Cost (B-1)",
                                     "bid_basis": "percentage", "weak": True}))
    if not found:
        # Marathi conditions (OCR): "प्रथम न्यूनतम देकार (L-१) ..." — the lowest offer is taken
        for d in docs:
            if d.parsed is None:
                continue
            for s in d.parsed.sentences:
                if d.parsed.pages[s.page - 1].quality.kind == "ocr_mr" and re.search(r"L\s*-\s*[1१]|एल\s*-\s*1", s.text) \
                        and re.search(r"न्यूनतम|लघुत्तम|कमी दर", s.text):
                    found.append(("L1", {"page": s.page, "quote": s.text, "clause": s.clause, "document_id": d.document_id,
                                         "doc_name": d.name, "label": "L1 — Lowest Offer (प्रथम न्यूनतम देकार)"}))
                    break
            if found:
                break
    if not found:
        return "unknown", None, ["Award mechanism not found in readable text — ranking will show raw prices only."]
    kinds = {k for k, _ in found}
    if len(kinds) > 1:
        return "unknown", {"label": "Conflicting award mechanisms", "candidates": [d for _, d in found]}, [
            "The tender states more than one award mechanism — confirm with the committee."]
    detail = next((d for _, d in found if not d.get("weak")), found[0][1])
    if any(d.get("bid_basis") == "percentage" for _, d in found):
        detail = {**detail, "bid_basis": "percentage"}
    if gate:
        detail = {**detail, "technical_min_pct": gate["technical_min_pct"], "gate_page": gate["page"],
                  "gate_quote": gate["gate_quote"],
                  "label": f"Technical score ≥ {gate['technical_min_pct']}% of marks, then {detail['label']}"}
    return found[0][0], detail, []


def extract_bundle(docs: list[DocView], boqs: list[tuple[str, str, object]] | None = None, llm=None) -> TenderExtraction:
    # 1. Key facts --------------------------------------------------------------------------
    facts_c = FactCollector()
    for d in docs:
        if d.parsed is None:
            continue
        facts_c.amending = d.doc_type == "corrigendum"
        for p in d.parsed.pages:
            facts_c.ocr = p.quality.kind.startswith("ocr")
            facts_c.text_layer = not facts_c.ocr
            if facts_c.amending:
                facts_c.scan_corrigendum(p.raw_text or p.text, p.tables, d.document_id, d.name, p.number)
                continue
            facts_c.scan_page(p.raw_text or p.text, d.document_id, d.name, p.number)
            facts_c.scan_tables(p.tables, d.document_id, d.name, p.number)
            if p.quality.kind == "ocr_mr":
                facts_c.scan_marathi(p.text, d.document_id, d.name, p.number)
    facts_c.ocr = facts_c.text_layer = facts_c.amending = False
    for doc_id, name, summary in boqs or []:
        facts_c.scan_boq(summary, doc_id, name)
    facts = facts_c.reconcile()

    all_text = " ".join((p.raw_text or "")[:3000] for d in docs if d.parsed for p in d.parsed.pages[:6])
    boq_text = " ".join(f"{b.authority or ''} {b.contract_no or ''}" for _, _, b in boqs or [])
    is_pmc = bool(re.search(r"Pune Municipal Corporation|पुणे महानगरपालिका|PUNE MUNICIPAL|\(PMC\s+(?:ROAD|Drainage|Bhavan)\s*\)",
                            all_text + " " + boq_text, re.I)
                  or str(facts["ref_no"].value or "").startswith("PMC/"))
    eligibility_docs = [d for d in docs if d.doc_type not in NON_ELIGIBILITY_TYPES and d.parsed is not None]

    # 2. PMC standard works criteria ----------------------------------------------------------
    pmc = pmc_eligibility.build(eligibility_docs, facts, is_pmc)
    drafts: dict[str, CriterionDraft] = {c.key: c for c in pmc.criteria}
    claimed = set(pmc.claimed)

    # In an RFP the EMD is still a pass/fail gate; its amount comes from the data sheet.
    emd = facts["emd"]
    if "pmc_emd" not in drafts and emd.value is not None and emd.status not in ("conflict", "lossy") \
            and any(d.doc_type == "rfp" for d in eligibility_docs):
        src = next((x for x in emd.sources if x.page), None)
        drafts["emd"] = CriterionDraft(
            key="emd", label="Earnest Money Deposit", bucket="Commercial", datatype="currency", operator=">=",
            tier="deterministic", threshold_value=float(emd.value), threshold_unit="INR",
            threshold_display=f"≥ {pmc_eligibility.format_inr(float(emd.value))}",
            source=Sentence(page=src.page, text=src.quote, section="Data sheet") if src else None,
            source_document_id=src.document_id if src else None, params={"terms": ["EMD", "earnest"]},
            extraction_method="deterministic", notes=["Amount from the tender's data sheet / notice."],
        )

    # 2b. Price-level indexation ---------------------------------------------------------------
    # PMC works print a multiplying-factor table beside each financial criterion; a bidder's older
    # turnover and older project values are compared only after those factors are applied. The
    # table rides on the criteria it governs so the bidder phase cannot forget it.
    # Without a printed table, a rate stated in prose ("प्रतिवर्षी १०% वाढ") still governs.
    index_table = indexation.merge(indexation.find_index_tables(eligibility_docs)) \
        or indexation.table_from_stated_rule(eligibility_docs, _tender_fy(facts["ref_no"].value, docs))
    if index_table is not None:
        for key in ("pmc_max_turnover", "pmc_similar_works", "pmc_bid_capacity", "pmc_single_similar_work"):
            c = drafts.get(key)
            if c is None:
                continue
            c.params = {**c.params, "index_factors": dict(index_table.factors),
                        "index_rate": index_table.implied_rate, "index_applies": index_table.consistent}
            c.derivation = {**c.derivation, "index_page": index_table.page}
            if index_table.consistent:
                c.notes.append(
                    f"Figures from earlier years are brought to current price level first "
                    f"({', '.join(f'FY {y}-{str(y + 1)[-2:]} × {index_table.factors[y]:g}' for y in sorted(index_table.factors, reverse=True))}"
                    f"; {index_table.doc_name or 'conditions'} p.{index_table.page}).")
            c.notes.extend(index_table.notes)

    # 2c. The experience ladder -----------------------------------------------------------------
    # "3 works ≥ 40%, or 2 ≥ 50%, or 1 ≥ 80%" and its RFP equivalents. The PMC works path builds
    # this from the qualification sheet; everywhere else the rungs sit in separate table cells,
    # sometimes across a page break, and a single-sentence rule reads only the strictest one —
    # which states the requirement three times too strictly.
    if "pmc_similar_works" not in drafts:
        for d in eligibility_docs:
            sents = list(_english(d))
            rungs = experience_ladder.find_rungs(sents)
            e_value = float(facts["estimated_cost"].value) if facts["estimated_cost"].value is not None else None
            ladder = experience_ladder.build_ladder(rungs, e_value) if rungs else None
            if ladder is None:
                continue
            first = next((s for s in sents if s.page == ladder.rungs[0].page
                          and s.text.strip()[:60] in ladder.rungs[0].quote), None)
            source = first or Sentence(page=ladder.rungs[0].page, text=ladder.rungs[0].quote, section="Eligibility")
            window = experience_ladder.find_window_years(sents)
            notes = list(ladder.notes)
            if len(ladder.pages) > 1:
                notes.append(f"The options are printed across p.{'–'.join(str(p) for p in ladder.pages)}; "
                             "all of them are alternatives, and meeting any one qualifies.")
            drafts["min_similar_projects"] = CriterionDraft(
                key="min_similar_projects", label="Similar Completed Projects (any one option)", bucket="Technical",
                datatype="composite", operator="similar_works_any", tier="llm_judge",
                threshold_display=ladder.display() + (f", last {window} yrs" if window else ""),
                source=source, source_document_id=d.document_id, extraction_method="deterministic",
                params={"window_years": window, "options": ladder.options(),
                        "options_consistent": ladder.consistent,
                        "implied_contract_value": ladder.implied_base if ladder.base_inferred else None},
                derivation={"rungs": [{"count": r.count, "page": r.page, "quote": r.quote} for r in ladder.rungs],
                            "implied_base": ladder.implied_base, "base_spread_pct": ladder.base_spread_pct},
                notes=notes,
            )
            claimed.update((d.document_id, r.page, r.quote) for r in ladder.rungs)
            break

    # 3. Sentence rules ----------------------------------------------------------------------
    def take(draft: CriterionDraft, d: DocView, s: Sentence) -> None:
        if draft.key in drafts:
            if draft.key not in MULTI or any(x.source and x.source.text == s.text for x in drafts.values()):
                return
            n = 2
            while f"{draft.key}_{n}" in drafts:
                n += 1
            draft.key = f"{draft.key}_{n}"
        draft.source_document_id = d.document_id
        if draft.key.startswith("personnel_") and d.doc_type == "rfp":
            draft.gating = False
            draft.notes = ["In this RFP key personnel are scored (technical evaluation), not a pass/fail gate."] + draft.notes
        if d.parsed and d.parsed.pages[s.page - 1].quality.kind.startswith("ocr"):
            draft.derivation = {**draft.derivation, "source_method": "ocr"}
            draft.notes = [f"Read by OCR (Chandra) from a scanned / non-text page ({d.name} p.{s.page})."] + draft.notes
        drafts[draft.key] = draft
        claimed.add((d.document_id, s.page, s.text))

    # In PMC works bundles the completion period, warranty and similar are contract terms carried by
    # the key facts and the conditions volume; the demo-era sentence rules for them misfire on the
    # 100+ pages of execution clauses ("… within 10 days …"), so only eligibility rules run there.
    skip_contract_terms = pmc.applies or any(d.doc_type == "rfp" for d in eligibility_docs)
    rules = [r for r in RULES if not (skip_contract_terms and r.__name__ in PMC_SKIP_RULES)]
    for d in eligibility_docs:
        for s in _english(d):
            if (d.document_id, s.page, s.text) in claimed:
                continue
            for rule in rules:
                draft = rule(s)
                if draft:
                    take(draft, d, s)
                    break
    for d in eligibility_docs:
        for s in _english(d):
            if (d.document_id, s.page, s.text) in claimed:
                continue
            draft = rule_generic_document(s)
            if draft:
                take(draft, d, s)

    # 4. Checklists -----------------------------------------------------------------------------
    claimed_texts = [t for (_, _, t) in claimed]
    for d in eligibility_docs:
        for label, bucket, sentence, params in checklist_extraction.extract_checklist(d.parsed, claimed_texts):
            key = "doc_" + re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]
            if key in drafts:
                continue
            drafts[key] = CriterionDraft(
                key=key, label=label, bucket=bucket, datatype="boolean", operator="exists", tier="deterministic",
                threshold_display="Document required", source=sentence, params=params, extraction_method="checklist",
                source_document_id=d.document_id,
                notes=[f"Item {params['checklist_no']} of the tender's submission checklist"
                       + (f"; evidence: {params['evidence']}" if params.get("evidence") else "")
                       + (f" (form {params['form_ref']})" if params.get("form_ref") else "") + "."],
            )

    # A checklist item that is the evidence for a standard criterion is folded into that criterion.
    merge_map = [(r"earnest\s*money|\bEMD\b", "pmc_emd"), (r"earnest\s*money|\bEMD\b", "emd"), (r"bid\s*capacity", "pmc_bid_capacity"),
                 (r"registration\s*certificate\s*(?:up\s*to|upto)\s*1\.?5", "pmc_registration"),
                 (r"^(?:\(?[ivx]{1,4}\)|\d{1,2}[.)])?\s*(?:contractor'?s\s*)?maximum\s*(?:annual\s*)?turnover|turnover.{0,40}(?:x|×)\s*0\.75", "pmc_max_turnover"),
                 (r"30\s*%.{0,40}quantit", "pmc_key_quantities"),
                 (r"single\s*work\s*of\s*similar\s*nature", "pmc_single_similar_work"),
                 (r"^registration\b.{0,80}(?:govt|1\.5)", "pmc_registration"),
                 (r"^staff\b", "personnel_*"), (r"^machinery\b", "plant_machinery")]
    for key in [k for k, c in drafts.items() if c.extraction_method == "checklist"]:
        text = drafts[key].source.text if drafts[key].source else ""
        # A summary-table row names the criterion ("Registration") and puts the rule in its evidence
        # column ("… Registration Certificate upto 1.5 cr"): match on both.
        probe = f"{text} {drafts[key].params.get('evidence') or ''}"
        target = None
        for pat, t in merge_map:
            if re.search(pat, probe, re.I):
                target = next((k for k in drafts if k.startswith(t[:-1]) and drafts[k].extraction_method != "checklist"), None) \
                    if t.endswith("*") else (t if t in drafts else None)
                if target:
                    break
        if target:
            item = drafts.pop(key)
            drafts[target].notes.append(f"Evidence: checklist item {item.params.get('checklist_no')} — “{text[:160]}” "
                                        f"({item.source.page and 'p.' + str(item.source.page)}).")

    # Submission lists restate the criteria as documents ("Documental proof of GSTN registration",
    # "Valid copy of CMMi3 certificate" after the criteria table asked for CMMi): the document is
    # the evidence for that criterion, not a second requirement.
    topics = [(r"^(?=.{0,80}$).*\bGSTN?\b", ["gst_registration"]),
              (r"staff\s*strength|\bIT\s*Professionals?\b|manpower\s*declaration", ["min_staff_strength"]),
              (r"^copy\s*of\s*(?:the\s*)?audited", ["avg_turnover", "pmc_max_turnover"]),
              (r"work\s*order.{0,40}completion|completion\s*certificate", ["min_similar_projects", "pmc_similar_works"]),
              (r"certificate\s*of\s*incorporation", ["years_in_existence"]),
              (r"\bCMM[Ii]\b|\bISO\s*9001", ["@cmmi"]), (r"blacklist|debar", ["@blacklist"])]
    first_by_topic: dict[str, str] = {}
    for key in [k for k, c in drafts.items() if c.extraction_method == "checklist"]:
        text = drafts[key].source.text if drafts[key].source else ""
        for pat, targets in topics:
            if not re.search(pat, text, re.I):
                continue
            target = next((t for t in targets if t in drafts), None)
            if targets[0].startswith("@"):
                target = first_by_topic.setdefault(targets[0], key)
            if target and target != key:
                item = drafts.pop(key)
                drafts[target].notes.append(f"Evidence: “{text[:160]}” (p.{item.source.page}).")
            break

    order = {d.document_id: i for i, d in enumerate(docs)}
    criteria = sorted(drafts.values(), key=lambda c: (
        {"pmc_standard": 0}.get(c.extraction_method, 1 if c.extraction_method != "checklist" else 2),
        order.get(c.source_document_id, 99), c.source.page if c.source else 0))

    warnings = list(pmc.warnings)
    if llm is not None:
        for d in eligibility_docs:
            extra, llm_warnings = llm_supplement(llm, d.parsed, criteria)
            for c in extra:
                c.source_document_id = d.document_id
            criteria.extend(extra)
            warnings.extend(llm_warnings)

    # 5. Award mechanism + metadata ---------------------------------------------------------------
    mechanism, award_detail, award_warnings = _award(eligibility_docs)
    if mechanism == "unknown" and not award_detail and (
            str(facts["form_of_contract"].value or "").lower() == "percentage" or facts["contract_type"].value == "B1"):
        # PMC percentage-rate (B-1) works are awarded to L1; say so, and say it was inferred.
        mechanism, award_warnings = "L1", []
        award_detail = {"label": "L1 — Lowest Percentage Rate (inferred from the Percentage BoQ / B-1 form)",
                        "bid_basis": "percentage", "inferred": True}
    warnings.extend(award_warnings)
    if award_detail and award_detail.get("technical_min_pct"):
        gate_doc = next((d for d in eligibility_docs if d.document_id == award_detail.get("document_id")), None)
        criteria.append(CriterionDraft(
            key="technical_score_gate", label="Technical Evaluation Score (qualifying marks)", bucket="Technical",
            datatype="number", operator=">=", tier="deterministic", threshold_value=award_detail["technical_min_pct"],
            threshold_unit="% of marks", threshold_display=f"≥ {award_detail['technical_min_pct']}% of total marks",
            source=Sentence(page=award_detail.get("gate_page") or award_detail["page"], text=award_detail.get("gate_quote") or "",
                            section="Technical evaluation"),
            source_document_id=gate_doc.document_id if gate_doc else None,
            notes=["Bids below the qualifying technical score are not opened financially."],
        ))
    if facts["form_of_contract"].value and str(facts["form_of_contract"].value).lower() == "percentage" and award_detail:
        award_detail = {**award_detail, "bid_basis": "percentage"}

    primary = next((d for d in docs if d.doc_type == "notice" and d.parsed), None) or next((d for d in docs if d.parsed), None)
    meta = _metadata(primary.parsed) if primary and primary.parsed and primary.parsed.pages else {}
    title = ((facts["work_name"].value if facts["work_name"].status not in ("conflict", "lossy") else None)
             or facts["work_name_mr"].value or meta.get("title"))
    ref_no = facts["ref_no"].value or meta.get("ref_no") or facts["ref_no_local"].value
    authority = meta.get("authority")
    if is_pmc and not authority:
        dept = facts["department"].value
        authority = "Pune Municipal Corporation" + (f" — {dept} Department" if dept else "")
    estimated = float(facts["estimated_cost"].value) if facts["estimated_cost"].value is not None else meta.get("estimated_value")

    for f in facts.values():
        if f.status == "conflict":
            warnings.append(f"{f.label} differs across documents: "
                            + "; ".join(f"{s.value} ({s.doc_name}{' p.' + str(s.page) if s.page else ''})" for s in f.sources[:4]))
    if not criteria:
        warnings.append("No criteria could be extracted automatically — manual entry needed.")

    return TenderExtraction(
        title=title, ref_no=ref_no, authority=authority, estimated_value=estimated, issue_date=meta.get("issue_date"),
        award_mechanism=mechanism, award_detail=award_detail, criteria=criteria, warnings=warnings,
        facts=[_fact_dict(f) for f in facts.values() if f.sources],
        documents=[_doc_summary(d) for d in docs],
    )


def _fact_dict(f) -> dict:
    return {"key": f.key, "label": f.label, "kind": f.kind, "value": f.value, "display": f.display, "status": f.status,
            "note": f.note, "sources": [{"document_id": s.document_id, "doc_name": s.doc_name, "page": s.page,
                                         "quote": s.quote, "value": s.value, "method": s.method} for s in f.sources]}


def _doc_summary(d: DocView) -> dict:
    kinds = Counter(p.quality.kind for p in d.parsed.pages) if d.parsed else Counter()
    return {"document_id": d.document_id, "name": d.name, "doc_type": d.doc_type, "pages": sum(kinds.values()),
            "kinds": dict(kinds)}


def extract_single(doc: ParsedDocument, llm=None, name: str = "tender.pdf") -> TenderExtraction:
    """One PDF (the seeded demo tender, or a tender uploaded as a single file)."""
    from app.services.bundle import classify_document

    first = "\n".join(p.raw_text or p.text for p in doc.pages[:2])
    view = DocView("primary", name, classify_document(name, first), doc)
    if view.doc_type in NON_ELIGIBILITY_TYPES:
        view.doc_type = "notice"
    return extract_bundle([view], llm=llm)
