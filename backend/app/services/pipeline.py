"""Orchestration: file storage, background extraction jobs, evaluation runs and read models."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.adapters.llm import get_llm
from app.adapters.pdf_parser import UnreadablePDF, parse_pdf
from app.config import settings
from app.db import SessionLocal
from app.adapters.datalab_ocr import get_ocr
from app.models import (Bidder, BidderCriterionVerdict, BidderDocument, BidderExtractedValue, Criterion, Tender,
                        TenderDocument)
from app.services import tender_processing as tp
from app.services.bidder_extraction import Claim, CriterionSpec, extract_bidder
from app.services.bundle import expand
from app.services.evaluation import evaluate_criterion, overall_status
from app.services.ranking import BidderStanding, compute_ranking
from app.services.values import format_inr

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Storage with per-tender / per-bidder isolation (NFR5)
# --------------------------------------------------------------------------------------

class CrossBidderPathLeak(Exception):
    """A resolved path escaped its bidder's folder. Hard fail, never retried (FSD 7.2)."""


def tender_dir(tender_id: str) -> Path:
    return settings.UPLOAD_DIR / "tenders" / tender_id


def bidder_dir(tender_id: str, bidder_id: str) -> Path:
    return tender_dir(tender_id) / "bidders" / bidder_id


def guarded_bidder_path(tender_id: str, bidder_id: str, file_path: str) -> Path:
    root = bidder_dir(tender_id, bidder_id).resolve()
    path = Path(file_path).resolve()
    if root not in path.parents:
        raise CrossBidderPathLeak(f"Path {path} is outside bidder folder {root}")
    return path


def remove_tender_files(tender_id: str) -> None:
    shutil.rmtree(tender_dir(tender_id), ignore_errors=True)


def remove_bidder_files(tender_id: str, bidder_id: str) -> None:
    shutil.rmtree(bidder_dir(tender_id, bidder_id), ignore_errors=True)


# --------------------------------------------------------------------------------------
# Background jobs
# --------------------------------------------------------------------------------------

def uploads_dir(tender_id: str) -> Path:
    return tender_dir(tender_id) / "uploads"


def _replace_criteria(session: Session, tender: Tender, result, doc_ids: dict[str, str]) -> None:
    """Swap in a fresh registry (the OCR pass re-extracts). Verdicts cascade: re-evaluate after OCR."""
    session.execute(delete(Criterion).where(Criterion.tender_id == tender.id))
    for order, draft in enumerate(result.criteria):
        src = draft.source
        session.add(Criterion(
            tender_id=tender.id, key=draft.key, label=draft.label, bucket=draft.bucket, tier=draft.tier,
            datatype=draft.datatype, operator=draft.operator, threshold_value=draft.threshold_value,
            threshold_unit=draft.threshold_unit, threshold_display=draft.threshold_display, params=draft.params,
            gating=draft.gating, source_page=src.page if src else None, source_quote=(src.text or None) if src else None,
            source_clause=src.clause if src else None, source_section=src.section if src else None,
            source_document_id=doc_ids.get(draft.source_document_id or "", draft.source_document_id),
            extraction_method=draft.extraction_method, status=draft.status, derivation=draft.derivation,
            notes=draft.notes, display_order=order,
        ))


def _apply_result(session: Session, tender: Tender, bundle) -> None:
    result = bundle.extraction
    tender.title = result.title or tender.title
    tender.tender_ref_no = result.ref_no
    tender.authority_name = result.authority
    tender.estimated_value = result.estimated_value
    tender.issue_date = result.issue_date
    tender.award_mechanism = result.award_mechanism
    tender.award_mechanism_detail = result.award_detail
    tender.facts = result.facts
    tender.warnings = list(dict.fromkeys(result.warnings + bundle.warnings))
    tender.page_count = sum(len(d.scans) for d in bundle.docs)
    docs = {d.document_id: d for d in bundle.docs}
    for row in tender.documents:
        d = docs.get(row.id)
        if d and d.parsed:
            row.page_kinds = [p.quality.kind for p in d.parsed.pages]
            row.ocr_modes = {str(k): v for k, v in d.ocr_modes.items()}
    _replace_criteria(session, tender, result, {})


def process_tender(tender_id: str) -> None:
    """Hybrid pipeline job: local pass → persist → OCR pass (background) → persist again."""
    with SessionLocal() as session:
        tender = session.get(Tender, tender_id)
        if tender is None:
            return
        tender.extraction_status = "extracting"
        session.commit()
        uploads = [(p.name, p) for p in sorted(uploads_dir(tender_id).iterdir()) if p.is_file()]
        started = time.time()  # local tier: unpack + quick scan + structural parse + extraction
        try:
            files, warnings = expand(uploads, tender_dir(tender_id) / "bundle")
            if not files:
                raise UnreadablePDF("No PDF or BOQ spreadsheet found in the upload. " + " ".join(warnings))
            ids = [str(uuid.uuid4()) for _ in files]
            docs = tp.local_pass(files, ids)
            for pos, d in enumerate(docs):
                session.add(TenderDocument(
                    id=d.document_id, tender_id=tender.id, position=pos, file_name=d.name, origin=d.origin,
                    file_path=str(d.path), file_hash=d.file_hash, doc_type=d.doc_type,
                    page_count=len(d.scans) or None, page_kinds=[s.quality.kind for s in d.scans], error=d.error,
                ))
            session.flush()
            session.refresh(tender)
            bundle = tp.BundleResult(docs, tp.extract(docs), warnings)
            bundle.timings["local"] = time.time() - started
        except UnreadablePDF as exc:
            tender.extraction_status, tender.extraction_error = "failed", str(exc)
            session.commit()
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Tender extraction failed")
            tender.extraction_status, tender.extraction_error = "failed", f"Extraction failed: {exc}"
            session.commit()
            return

        ocr = get_ocr()
        planned = tp.plan_ocr(docs)
        if ocr is None or not planned:
            bundle.warnings.extend(tp.coverage_warnings(docs, ocr_available=ocr is not None))
        _apply_result(session, tender, bundle)
        tender.ocr_status = ("queued" if ocr else "unavailable") if planned else "not_needed"
        tender.ocr_progress = {"done": 0, "total": len(planned), "cached": 0, "errors": []}
        tender.timings = {"local_seconds": round(bundle.timings["local"], 2)}
        tender.extraction_status = "ready"
        session.commit()
        if not planned or ocr is None:
            return

        tender.ocr_status = "running"
        session.commit()

        def progress(done: int, total: int) -> None:
            with SessionLocal() as s2:
                t = s2.get(Tender, tender_id)
                if t is not None:
                    t.ocr_progress = {**(t.ocr_progress or {}), "done": done, "total": total}
                    s2.commit()

        started = time.time()
        try:
            total, cached, errors = tp.run_ocr(docs, ocr, on_progress=progress)
            bundle.extraction = tp.extract(docs)
            bundle.warnings.extend(tp.skipped_by_cap(docs))
            bundle.warnings.extend(tp.coverage_warnings(docs, True, errors))
        except Exception as exc:  # OCR must never undo the local result
            log.exception("OCR pass failed")
            session.refresh(tender)
            tender.ocr_status = "failed"
            tender.ocr_progress = {**(tender.ocr_progress or {}), "errors": [str(exc)[:300]]}
            session.commit()
            return
        session.refresh(tender)
        _apply_result(session, tender, bundle)
        tender.ocr_status = "done" if not errors else ("failed" if len(errors) >= max(1, total) else "done")
        tender.ocr_progress = {"done": total, "total": total, "cached": cached, "errors": errors[:10]}
        tender.timings = {**(tender.timings or {}), "ocr_seconds": round(time.time() - started, 2)}
        session.commit()


def _spec(c: Criterion) -> CriterionSpec:
    return CriterionSpec(key=c.key, label=c.label, operator=c.operator, params=c.params or {},
                         threshold_value=c.threshold_value, threshold_unit=c.threshold_unit,
                         extraction_method=c.extraction_method)


def process_bidder(bidder_id: str) -> None:
    with SessionLocal() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder is None or not bidder.documents:
            return
        bidder.status = "Extracting"
        session.commit()
        document = bidder.documents[0]
        try:
            path = guarded_bidder_path(bidder.tender_id, bidder.id, document.file_path)
            doc = parse_pdf(path)
            criteria = session.scalars(select(Criterion).where(Criterion.tender_id == bidder.tender_id)
                                       .order_by(Criterion.display_order)).all()
            result = extract_bidder(doc, [_spec(c) for c in criteria], document.file_name, llm=get_llm())
        except CrossBidderPathLeak:
            log.critical("Cross-bidder path leak blocked for bidder %s", bidder.id)
            bidder.status, bidder.error = "Failed", "A security check blocked this action. Contact support."
            session.commit()
            return
        except UnreadablePDF as exc:
            bidder.status, bidder.error = "Failed", f"This document needs manual review. {exc}"
            session.commit()
            return

        document.page_count = doc.page_count
        if not bidder.bidder_name:  # unless the committee named the bidder, take it from the document
            bidder.bidder_name = result.bidder_name
        session.execute(delete(BidderExtractedValue).where(BidderExtractedValue.bidder_id == bidder.id))
        for claim in result.claims:
            session.add(BidderExtractedValue(
                bidder_id=bidder.id, criteria_key=claim.criteria_key, document_id=document.id, value=claim.value,
                value_numeric=claim.value_numeric, detail=claim.detail, evidence_page=claim.page,
                evidence_quote=claim.quote, evidence_section=claim.section, extraction_tier=claim.tier,
            ))
        bidder.status = "ClaimsReady"
        bidder.error = "\n".join(result.warnings) or None
        session.commit()


# --------------------------------------------------------------------------------------
# Evaluation run
# --------------------------------------------------------------------------------------

def _reference_date(tender: Tender) -> date:
    if tender.issue_date:
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(tender.issue_date, fmt).date()
            except ValueError:
                continue
    return date.today()


def _claim_from_row(row: BidderExtractedValue) -> Claim:
    return Claim(criteria_key=row.criteria_key, value=row.value, page=row.evidence_page, quote=row.evidence_quote,
                 section=row.evidence_section or "", value_numeric=row.value_numeric, tier=row.extraction_tier,
                 detail={**(row.detail or {}), "document_id": row.document_id})


def evaluate_tender(session: Session, tender: Tender) -> int:
    session.execute(delete(BidderCriterionVerdict).where(BidderCriterionVerdict.tender_id == tender.id))
    llm = get_llm()
    reference = _reference_date(tender)
    count = 0
    for bidder in tender.bidders:
        if bidder.status != "ClaimsReady":
            continue
        rows = session.scalars(select(BidderExtractedValue).where(BidderExtractedValue.bidder_id == bidder.id)).all()
        for criterion in tender.criteria:
            claims = [_claim_from_row(r) for r in rows if r.criteria_key == criterion.key]
            result = evaluate_criterion(_spec(criterion), claims, criterion.source_quote or criterion.label, reference, llm)
            primary = result.evidence[0] if result.evidence else None
            session.add(BidderCriterionVerdict(
                tender_id=tender.id, bidder_id=bidder.id, criterion_id=criterion.id, criteria_key=criterion.key,
                verdict=result.verdict, verdict_tier=result.tier, unknown_kind=result.unknown_kind,
                reason=result.reason, computation=result.computation,
                requirement_tender_page=criterion.source_page, requirement_quote=criterion.source_quote,
                requirement_document_id=criterion.source_document_id,
                evidence_document_id=primary.detail.get("document_id") if primary else None,
                evidence_page=primary.page if primary else None,
                evidence_quote=primary.quote if primary else None,
                evidence_json=[{
                    "document_id": c.detail.get("document_id"), "page": c.page, "quote": c.quote,
                    "section": c.section, "value": c.value, "tier": c.tier,
                } for c in result.evidence],
            ))
            count += 1
    session.commit()
    return count


# --------------------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------------------

def load_tender(session: Session, tender_id: str) -> Tender | None:
    return session.scalars(
        select(Tender).where(Tender.id == tender_id).options(
            selectinload(Tender.criteria),
            selectinload(Tender.documents),
            selectinload(Tender.bidders).selectinload(Bidder.documents),
        )
    ).first()


def serialize_criterion(c: Criterion, doc_names: dict[str, str] | None = None) -> dict:
    return {
        "id": c.id, "key": c.key, "label": c.label, "bucket": c.bucket, "tier": c.tier, "datatype": c.datatype,
        "operator": c.operator, "threshold_display": c.threshold_display, "gating": c.gating,
        "source_page": c.source_page, "source_quote": c.source_quote, "source_clause": c.source_clause,
        "source_section": c.source_section, "extraction_method": c.extraction_method,
        "source_document_id": c.source_document_id,
        "source_document_name": (doc_names or {}).get(c.source_document_id or ""),
        "status": c.status, "notes": c.notes or [], "derivation": c.derivation or {},
    }


def serialize_document(d: TenderDocument) -> dict:
    kinds = d.page_kinds or []
    return {
        "id": d.id, "file_name": d.file_name, "origin": d.origin, "doc_type": d.doc_type, "page_count": d.page_count,
        "page_kinds": kinds, "error": d.error, "ocr_pages": len(d.ocr_modes or {}),
        "summary": {k: kinds.count(k) for k in dict.fromkeys(kinds)},
    }


def serialize_bidder(session: Session, b: Bidder) -> dict:
    doc = b.documents[0] if b.documents else None
    prices = session.scalars(select(BidderExtractedValue).where(
        BidderExtractedValue.bidder_id == b.id, BidderExtractedValue.criteria_key == "quoted_price")).all()
    distinct = {p.value_numeric for p in prices}
    price = prices[0] if len(distinct) == 1 else None
    covered = set(session.scalars(select(BidderExtractedValue.criteria_key).where(
        BidderExtractedValue.bidder_id == b.id, BidderExtractedValue.criteria_key != "quoted_price")).all())
    return {
        "id": b.id, "name": b.bidder_name, "status": b.status, "error": b.error,
        "criteria_with_evidence": len(covered),
        "document": {"id": doc.id, "file_name": doc.file_name, "page_count": doc.page_count} if doc else None,
        "price": price.value_numeric if price else None,
        "price_display": format_inr(price.value_numeric) if price else None,
        "price_citation": {"document_id": price.document_id, "page": price.evidence_page, "quote": price.evidence_quote}
        if price else None,
        "price_conflict": len(distinct) > 1,
    }


def _real_doc_id(t: Tender, doc_id: str | None) -> str | None:
    ids = {d.id for d in t.documents}
    return doc_id if doc_id in ids else None


def serialize_tender(session: Session, t: Tender) -> dict:
    detail = t.award_mechanism_detail or {}
    evaluated_ids = set(session.scalars(select(BidderCriterionVerdict.bidder_id)
                                        .where(BidderCriterionVerdict.tender_id == t.id).distinct()).all())
    ready_ids = {b.id for b in t.bidders if b.status == "ClaimsReady"}
    doc_names = {d.id: d.file_name for d in t.documents}
    return {
        "id": t.id, "title": t.title, "ref_no": t.tender_ref_no, "authority": t.authority_name,
        "estimated_value": t.estimated_value,
        "estimated_value_display": format_inr(t.estimated_value) if t.estimated_value else None,
        "issue_date": t.issue_date, "doc_name": t.tender_doc_name, "page_count": t.page_count,
        "award_mechanism": t.award_mechanism, "award_mechanism_label": detail.get("label"),
        "award_mechanism_citation": {"page": detail["page"], "quote": detail["quote"], "clause": detail.get("clause"),
                                     "document_id": _real_doc_id(t, detail.get("document_id"))}
        if "page" in detail else None,
        "status": t.status, "extraction_status": t.extraction_status, "extraction_error": t.extraction_error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "criteria": [serialize_criterion(c, doc_names) for c in t.criteria],
        "bidders": [serialize_bidder(session, b) for b in t.bidders],
        "documents": [serialize_document(d) for d in t.documents],
        "primary_document_id": t.documents[0].id if t.documents else None,
        "facts": t.facts or [], "warnings": t.warnings or [],
        "ocr_status": t.ocr_status, "ocr_progress": t.ocr_progress or {}, "timings": t.timings or {},
        "evaluated": bool(evaluated_ids),
        # Bidders were added or removed since the last run: verdicts no longer cover the current set.
        "evaluation_stale": bool(evaluated_ids) and evaluated_ids != ready_ids,
        "llm_enabled": get_llm() is not None,
    }


def build_evaluation(session: Session, tender: Tender, include_ranking: bool) -> dict:
    verdict_rows = session.scalars(
        select(BidderCriterionVerdict).where(BidderCriterionVerdict.tender_id == tender.id)).all()
    docs = {d.id: d for b in tender.bidders for d in b.documents}
    criteria = {c.id: c for c in tender.criteria}
    bidders = [serialize_bidder(session, b) for b in tender.bidders if b.status == "ClaimsReady"]
    evaluated_ids = {v.bidder_id for v in verdict_rows}
    bidders = [b for b in bidders if b["id"] in evaluated_ids]

    verdicts = []
    for v in verdict_rows:
        c = criteria.get(v.criterion_id)
        if c is None:
            continue
        verdicts.append({
            "id": v.id, "criterion_id": v.criterion_id, "bidder_id": v.bidder_id, "verdict": v.verdict,
            "tier": v.verdict_tier, "unknown_kind": v.unknown_kind, "reason": v.reason, "computation": v.computation,
            "requirement": {"page": v.requirement_tender_page, "quote": v.requirement_quote,
                            "clause": c.source_clause, "section": c.source_section,
                            "document_id": v.requirement_document_id, "status": c.status},
            "evidence": [{**e, "document_name": docs[e["document_id"]].file_name if e.get("document_id") in docs else None}
                         for e in (v.evidence_json or [])],
        })

    standings = []
    for b in bidders:
        mine = [v for v in verdicts if v["bidder_id"] == b["id"]]
        status = overall_status([(v["verdict"], criteria[v["criterion_id"]].gating) for v in mine])
        b["overall"] = status
        b["counts"] = {k: sum(1 for v in mine if v["verdict"] == k) for k in ("pass", "fail", "unknown")}
        issues = [{
            "criterion_id": v["criterion_id"], "criterion_label": criteria[v["criterion_id"]].label,
            "verdict": v["verdict"], "reason": v["reason"], "requirement": v["requirement"], "evidence": v["evidence"],
        } for v in mine if v["verdict"] != "pass"]
        issues.sort(key=lambda i: i["verdict"] != "fail")
        b["issues"] = issues
        standings.append(BidderStanding(bidder_id=b["id"], name=b["name"], status=status, price=b["price"],
                                        price_citation=b["price_citation"], issues=issues))

    out = {
        "tender": {k: v for k, v in serialize_tender(session, tender).items() if k not in ("criteria", "bidders")},
        "criteria": [serialize_criterion(c, {d.id: d.file_name for d in tender.documents}) for c in tender.criteria],
        "bidders": bidders,
        "verdicts": verdicts,
        "evaluated": bool(verdict_rows),
        "eligibility": {k: sum(1 for s in standings if s.status == k) for k in ("eligible", "pending", "disqualified")},
    }
    if include_ranking:
        out["ranking"] = compute_ranking(tender.award_mechanism or "unknown",
                                         (tender.award_mechanism_detail or {}).get("label"), standings)
    return out
