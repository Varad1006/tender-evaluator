"""Orchestration: file storage, background extraction jobs, evaluation runs and read models."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, selectinload

from app.adapters.llm import get_llm
from app.adapters.pdf_parser import UnreadablePDF, parse_pdf
from app.config import settings
from app.db import SessionLocal
from app.adapters.datalab_ocr import get_ocr
from app.models import (Bidder, BidderCriterionVerdict, BidderDocument, BidderExtractedValue, Criterion, Meta, Tender,
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
            bundle = tp.BundleResult(docs, tp.extract(docs), warnings)  # before any write: SQLite has one writer
            for pos, d in enumerate(docs):
                session.add(TenderDocument(
                    id=d.document_id, tender_id=tender.id, position=pos, file_name=d.name, origin=d.origin,
                    file_path=str(d.path), file_hash=d.file_hash, doc_type=d.doc_type,
                    page_count=len(d.scans) or None, page_kinds=[s.quality.kind for s in d.scans], error=d.error,
                ))
            session.flush()
            session.refresh(tender)
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
        tender.ocr_progress = {"done": total, "total": total, "cached": cached, "errors": errors[:10], **_usage(ocr)}
        tender.timings = {**(tender.timings or {}), "ocr_seconds": round(time.time() - started, 2)}
        session.commit()


RESUME_LIMIT = 2  # a job that dies every time (a PDF that kills the parser) must not crash-loop the server


def _count_resume(session: Session, key: str) -> int:
    row = session.get(Meta, key)
    attempts = (int(row.value) if row else 0) + 1
    session.merge(Meta(key=key, value=str(attempts)))
    return attempts


def resume_interrupted() -> list[tuple]:
    """Jobs a server stop cut short stay 'extracting' / OCR 'running' / bidder 'Extracting' forever
    otherwise — and a stuck bidder blocks evaluation. Resets them and returns (job, id) pairs to
    re-run from the saved uploads; OCR pages already paid for come back from the cache."""
    jobs: list[tuple] = []
    with SessionLocal() as session:
        stuck = session.scalars(select(Tender).where(or_(Tender.extraction_status.in_(("pending", "extracting")),
                                                          Tender.ocr_status.in_(("queued", "running"))))).all()
        stuck_bids = session.scalars(select(Bidder).where(Bidder.status.in_(("Registered", "Extracting")))).all()
        # A counter tracks consecutive interruptions of one job; drop those of jobs no longer stuck
        # (finished before the stop, or their tender or bid deleted).
        live = {f"rt:{t.id}" for t in stuck} | {f"rb:{b.id}" for b in stuck_bids}
        session.execute(delete(Meta).where(or_(Meta.key.like("rt:%"), Meta.key.like("rb:%")), Meta.key.not_in(live)))
        for t in stuck:
            if not uploads_dir(t.id).is_dir():
                t.extraction_status, t.extraction_error = "failed", "The uploaded files are missing. Upload the tender again."
                t.ocr_status = "not_needed" if t.ocr_status in ("queued", "running") else t.ocr_status
                continue
            if _count_resume(session, f"rt:{t.id}") > RESUME_LIMIT:
                if t.extraction_status == "ready":
                    t.ocr_status = "failed"
                    t.ocr_progress = {**(t.ocr_progress or {}), "errors": ["OCR was interrupted by repeated server restarts."]}
                else:
                    t.extraction_status = "failed"
                    t.extraction_error = "Processing was interrupted by repeated server restarts. Upload the tender again."
                continue
            # Start clean: the half-written registry and unpacked bundle are rebuilt from the uploads.
            session.execute(delete(Criterion).where(Criterion.tender_id == t.id))
            session.execute(delete(TenderDocument).where(TenderDocument.tender_id == t.id))
            shutil.rmtree(tender_dir(t.id) / "bundle", ignore_errors=True)
            t.extraction_status, t.extraction_error = "pending", None
            t.ocr_status, t.ocr_progress = "not_needed", {}
            jobs.append((process_tender, t.id))
        for b in stuck_bids:
            if _count_resume(session, f"rb:{b.id}") > RESUME_LIMIT:
                b.status, b.error = "Failed", "Extraction was interrupted by repeated server restarts. Upload the bid again."
                continue
            b.status = "Registered"
            jobs.append((process_bidder, b.id))
        session.commit()
    return jobs


def _usage(ocr) -> dict:
    """What the OCR service billed this job (pages, cents), when the client reports it."""
    return ocr.usage() if hasattr(ocr, "usage") else {}


def run_jobs(jobs: list[tuple]) -> None:
    """One after another, so a burst of resumed tenders does not exceed the OCR plan's rate limit."""
    for job, arg in jobs:
        try:
            job(arg)
        except Exception:  # pragma: no cover - each job records its own failure
            log.exception("Resumed job %s(%s) failed", job.__name__, arg)
        # It ran to the end: a later interruption is a new one, not the next in a crash loop.
        with SessionLocal() as session:
            session.execute(delete(Meta).where(Meta.key.in_((f"rt:{arg}", f"rb:{arg}"))))
            session.commit()


def _spec(c: Criterion, tender_ctx: dict | None = None) -> CriterionSpec:
    return CriterionSpec(key=c.key, label=c.label, operator=c.operator, params=c.params or {},
                         threshold_value=c.threshold_value, threshold_unit=c.threshold_unit,
                         extraction_method=c.extraction_method, tender=tender_ctx or {})


def bidder_uploads_dir(tender_id: str, bidder_id: str) -> Path:
    return bidder_dir(tender_id, bidder_id) / "uploads"


def _bidder_uploads(bidder: Bidder) -> list[tuple[str, Path]]:
    folder = bidder_uploads_dir(bidder.tender_id, bidder.id)
    if folder.is_dir():
        return [(p.name, p) for p in sorted(folder.iterdir()) if p.is_file()]
    # Bidders uploaded before bundles were supported: one PDF stored beside its document row.
    out = []
    for d in bidder.documents:
        path = guarded_bidder_path(bidder.tender_id, bidder.id, d.file_path)
        if path.exists():
            out.append((d.file_name, path))
    return out


def _save_claims(session: Session, bidder: Bidder, result) -> None:
    session.execute(delete(BidderExtractedValue).where(BidderExtractedValue.bidder_id == bidder.id))
    for claim in result.claims:
        session.add(BidderExtractedValue(
            bidder_id=bidder.id, criteria_key=claim.criteria_key, document_id=claim.detail.get("document_id"),
            value=claim.value[:255], value_numeric=claim.value_numeric,
            detail={k: v for k, v in claim.detail.items() if k != "document_id"}, evidence_page=claim.page,
            evidence_quote=claim.quote, evidence_section=(claim.section or "")[:255] or None,
            extraction_tier=claim.tier,
        ))


def _apply_bid(session: Session, bidder: Bidder, docs, result, name_given: bool) -> None:
    by_id = {d.document_id: d for d in docs}
    for row in bidder.documents:
        d = by_id.get(row.id)
        if d is None or d.parsed is None:
            continue
        row.page_kinds = [p.quality.kind for p in d.parsed.pages]
        row.page_types = result.types_by_doc.get(row.id)
        row.ocr_modes = {str(k): v for k, v in d.ocr_modes.items()}
        types = [t for t in (row.page_types or []) if t]
        row.doc_type = "emd_receipt" if types and set(types) == {"emd_receipt"} else "combined"
    _save_claims(session, bidder, result)
    facts = dict(result.identity)
    facts["contents"] = {row.id: result.contents_by_doc.get(row.id, []) for row in bidder.documents}
    if not name_given:
        # Re-read every pass: before OCR a scanned bid may yield only a supplier on an invoice.
        firm = (result.identity.get("firm_name") or {}).get("value")
        bidder.bidder_name = firm or (f"Bid {bidder.bid_ref}" if bidder.bid_ref else "Unnamed bidder")
        facts["name_derived"] = True
    bidder.facts = facts


def _fail_bidder(bidder_id: str, message: str) -> None:
    with SessionLocal() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder is not None:
            bidder.status, bidder.error = "Failed", message
            session.commit()


def process_bidder(bidder_id: str) -> None:
    """Bid pipeline: expand the envelope → local pass → claims → OCR → claims again.

    The database is touched only in short transactions around the reading, never during it: bids
    are read concurrently and SQLite has one writer."""
    from app.services import bidder_processing as bp

    # 1. What the job needs, and mark the bid as being read.
    with SessionLocal() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder is None:
            return
        tender = load_tender(session, bidder.tender_id)
        tender_id = bidder.tender_id
        name_given = bool(bidder.bidder_name) and not (bidder.facts or {}).get("name_derived")
        try:
            uploads = _bidder_uploads(bidder)
        except CrossBidderPathLeak:
            uploads = None
        ctx = bp.tender_context(tender)
        specs = [_spec(c, ctx) for c in tender.criteria]
        bidder.status, bidder.error = "Extracting", None
        session.commit()
    if uploads is None:
        log.critical("Cross-bidder path leak blocked for bidder %s", bidder_id)
        _fail_bidder(bidder_id, "A security check blocked this action. Contact support.")
        return

    # 2. Read the bid: no transaction open.
    started = time.time()
    try:
        if not uploads:
            raise UnreadablePDF("The uploaded files are missing. Upload the bid again.")
        bundle_dir = bidder_dir(tender_id, bidder_id) / "bundle"
        shutil.rmtree(bundle_dir, ignore_errors=True)
        files, warnings = expand(uploads, bundle_dir)
        if not files:
            raise UnreadablePDF("No PDF found in the upload. " + " ".join(warnings))
        docs = tp.local_pass(files, [str(uuid.uuid4()) for _ in files], role="bidder")
        result = bp.extract(docs, specs, llm=get_llm())
    except UnreadablePDF as exc:
        _fail_bidder(bidder_id, f"This bid needs manual review. {exc}")
        return
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Bid extraction failed")
        _fail_bidder(bidder_id, f"Extraction failed: {exc}")
        return

    ocr = get_ocr()
    planned = bp.plan_ocr(docs)
    local_warnings = warnings + result.warnings
    if not planned or ocr is None:
        local_warnings += bp.coverage_warnings(docs, ocr_available=ocr is not None)

    # 3. Write what was read.
    with SessionLocal() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder is None:
            return  # removed while it was being read
        # Re-runnable: verdicts point at documents, so they go first, then the old document rows.
        session.execute(delete(BidderCriterionVerdict).where(BidderCriterionVerdict.bidder_id == bidder_id))
        session.execute(delete(BidderExtractedValue).where(BidderExtractedValue.bidder_id == bidder_id))
        session.execute(delete(BidderDocument).where(BidderDocument.bidder_id == bidder_id))
        session.flush()
        for pos, d in enumerate(docs):
            session.add(BidderDocument(
                id=d.document_id, bidder_id=bidder_id, position=pos, doc_type="combined", file_name=d.name,
                origin=d.origin, file_path=str(d.path), file_hash=d.file_hash, page_count=len(d.scans) or None,
                page_kinds=[s.quality.kind for s in d.scans], error=d.error,
            ))
        session.flush()
        session.refresh(bidder)
        _apply_bid(session, bidder, docs, result, name_given)
        bidder.warnings = local_warnings
        bidder.ocr_status = ("running" if ocr else "unavailable") if planned else "not_needed"
        bidder.ocr_progress = {"done": 0, "total": len(planned), "cached": 0, "errors": []}
        bidder.timings = {"local_seconds": round(time.time() - started, 2)}
        if not planned or ocr is None:
            if not result.claims and not any(d.parsed and d.parsed.readable_pages for d in docs):
                bidder.status = "Failed"
                bidder.error = "No page of this bid has readable text, and OCR is not configured."
            else:
                bidder.status = "ClaimsReady"
            session.commit()
            return
        session.commit()  # still "Extracting": most of a scanned bid is not read yet

    def progress(done: int, total: int) -> None:
        with SessionLocal() as s2:
            b = s2.get(Bidder, bidder_id)
            if b is not None:
                b.ocr_progress = {**(b.ocr_progress or {}), "done": done, "total": total}
                s2.commit()

    # 4. OCR, then read again: no transaction open.
    t_ocr = time.time()
    try:
        total, cached, errors = bp.run_ocr(docs, ocr, on_progress=progress)
        result = bp.extract(docs, specs, llm=get_llm())
    except Exception as exc:  # OCR must never undo the local result
        log.exception("Bid OCR pass failed")
        with SessionLocal() as session:
            bidder = session.get(Bidder, bidder_id)
            if bidder is not None:
                bidder.ocr_status = "failed"
                bidder.ocr_progress = {**(bidder.ocr_progress or {}), "errors": [str(exc)[:300]]}
                bidder.status = "ClaimsReady"
                session.commit()
        return

    # 5. Write the final reading.
    with SessionLocal() as session:
        bidder = session.get(Bidder, bidder_id)
        if bidder is None:
            return
        _apply_bid(session, bidder, docs, result, name_given)
        bidder.warnings = list(dict.fromkeys(warnings + result.warnings + bp.skipped_by_cap(docs)
                                             + bp.coverage_warnings(docs, True, errors)))
        bidder.ocr_status = "done" if not errors else ("failed" if len(errors) >= max(1, total) else "done")
        bidder.ocr_progress = {"done": total, "total": total, "cached": cached, "errors": errors[:10], **_usage(ocr)}
        bidder.timings = {**(bidder.timings or {}), "ocr_seconds": round(time.time() - t_ocr, 2)}
        bidder.status = "ClaimsReady"
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
    from app.services.bidder_processing import tender_context

    llm = get_llm()
    reference = _reference_date(tender)
    ctx = tender_context(tender)
    dates = [b.facts.get("bid_date") for b in tender.bidders if b.facts and b.facts.get("bid_date")]
    fallback_bid_date = max(dates) if dates else None
    count = 0
    for bidder in tender.bidders:
        if bidder.status != "ClaimsReady":
            continue
        rows = session.scalars(select(BidderExtractedValue).where(BidderExtractedValue.bidder_id == bidder.id)).all()
        # A receipt screenshot cropped without its timestamp still belongs to the same submission
        # window as the other bids: the latest of theirs stands in.
        bidder_ctx = {**ctx, "bid_date": (bidder.facts or {}).get("bid_date") or fallback_bid_date}
        for criterion in tender.criteria:
            claims = [_claim_from_row(r) for r in rows if r.criteria_key == criterion.key]
            result = evaluate_criterion(_spec(criterion, bidder_ctx), claims, criterion.source_quote or criterion.label,
                                        reference, llm)
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
    prices = session.scalars(select(BidderExtractedValue).where(
        BidderExtractedValue.bidder_id == b.id, BidderExtractedValue.criteria_key == "quoted_price")).all()
    distinct = {p.value_numeric for p in prices}
    price = prices[0] if len(distinct) == 1 else None
    covered = set(session.scalars(select(BidderExtractedValue.criteria_key).where(
        BidderExtractedValue.bidder_id == b.id, BidderExtractedValue.criteria_key != "quoted_price")).all())
    main = max(b.documents, key=lambda d: d.page_count or 0) if b.documents else None
    facts = b.facts or {}
    return {
        "id": b.id, "name": b.bidder_name, "status": b.status, "error": b.error, "bid_ref": b.bid_ref,
        "criteria_with_evidence": len(covered),
        "document": {"id": main.id, "file_name": main.file_name, "page_count": main.page_count} if main else None,
        "documents": [{
            "id": d.id, "file_name": d.file_name, "origin": d.origin, "doc_type": d.doc_type, "page_count": d.page_count,
            "page_kinds": d.page_kinds or [], "page_types": d.page_types or [],
            "contents": (facts.get("contents") or {}).get(d.id, []), "error": d.error,
            "ocr_pages": len(d.ocr_modes or {}),
        } for d in b.documents],
        "identity": {k: v for k, v in facts.items() if k != "contents"},
        "warnings": b.warnings or [],
        "ocr_status": b.ocr_status or "not_needed", "ocr_progress": b.ocr_progress or {}, "timings": b.timings or {},
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
        # A non-gating condition (bid validity, rate analysis) is noted, never counted as an issue.
        noted = {v["id"] for v in mine if v["verdict"] != "pass" and not criteria[v["criterion_id"]].gating}
        b["counts"] = {k: sum(1 for v in mine if v["verdict"] == k and v["id"] not in noted) for k in ("pass", "fail", "unknown")}
        b["counts"]["not_a_gate"] = len(noted)
        issues = [{
            "criterion_id": v["criterion_id"], "criterion_label": criteria[v["criterion_id"]].label,
            "verdict": v["verdict"], "reason": v["reason"], "requirement": v["requirement"], "evidence": v["evidence"],
        } for v in mine if v["verdict"] != "pass" and v["id"] not in noted]
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
