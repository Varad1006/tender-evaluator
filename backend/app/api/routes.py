"""REST API (FSD section 8 conventions: /api/v1, {success, data, message} envelope).

PoC: single demo user, so no auth guard (RBAC is cut in the Build Brief). Lifecycle status is a
label only.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import get_llm
from app.config import settings
from app.config.models import roles
from app.db import get_session
from app.models import Bidder, BidderDocument, Tender, TenderDocument
from app.services import jobs, pipeline

router = APIRouter(prefix="/api/v1")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def ok(data, message: str | None = None) -> dict:
    return {"success": True, "data": data, "message": message}


MAGIC = {".pdf": (b"%PDF",), ".zip": (b"PK\x03\x04",), ".rar": (b"Rar!",), ".xls": (b"\xd0\xcf\x11\xe0",),
         ".xlsx": (b"PK\x03\x04",)}


async def _read_upload(file: UploadFile, allowed: tuple[str, ...] = (".pdf",)) -> bytes:
    content = await file.read()
    limit = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > limit:
        raise ApiError(413, "VAL_002", f"{file.filename}: exceeds the {settings.MAX_FILE_SIZE_MB} MB limit.")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in allowed and not (content.startswith(b"%PDF") and ".pdf" in allowed):
        raise ApiError(400, "VAL_001", f"{file.filename}: only {', '.join(allowed)} files are accepted.")
    if not any(content.startswith(m) for m in MAGIC.get(suffix, (b"%PDF",))):
        raise ApiError(400, "VAL_001", f"{file.filename}: the file content does not match its type.")
    return content


def _safe_name(name: str | None) -> str:
    return re.sub(r"[^\w.\- ]+", "_", Path(name or "document.pdf").name)[:200]


def _tender_or_404(session: Session, tender_id: str) -> Tender:
    tender = pipeline.load_tender(session, tender_id)
    if tender is None:
        raise ApiError(404, "AUTH_003", "Not found.")
    return tender


# --------------------------------------------------------------------------------------

@router.get("/health")
def health():
    llm = get_llm()
    return ok({
        "llm": {"enabled": llm is not None, "provider": "gemini" if llm else None, "models": roles()},
        "ocr": {"enabled": bool(settings.DATALAB_API_KEY and settings.OCR_ENABLED), "provider": "datalab",
                "modes": {"scanned": settings.OCR_MODE_SCANNED, "vector": settings.OCR_MODE_VECTOR},
                "max_concurrency": settings.OCR_MAX_CONCURRENCY},
        "max_file_size_mb": settings.MAX_FILE_SIZE_MB,
        "max_bidders_soft_limit": settings.MAX_BIDDERS_SOFT_LIMIT,
    })


@router.get("/tenders")
def list_tenders(session: Session = Depends(get_session)):
    tenders = session.scalars(select(Tender).order_by(Tender.created_at.desc())).all()
    return ok([{
        "id": t.id, "title": t.title, "ref_no": t.tender_ref_no, "authority": t.authority_name,
        "status": t.status, "extraction_status": t.extraction_status, "criteria_count": len(t.criteria),
        "bidder_count": len(t.bidders), "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in tenders])


@router.post("/tenders", status_code=201)
async def create_tender(files: list[UploadFile] | None = File(None),
                        file: UploadFile | None = File(None), session: Session = Depends(get_session)):
    """A tender is a bundle: notice PDF, conditions PDFs, BOQ .xls, or the zip/rar MahaTender provides."""
    uploads = [f for f in (files or []) + ([file] if file else []) if f is not None]
    if not uploads:
        raise ApiError(400, "VAL_001", "Upload at least one tender file.")
    contents = [(f, await _read_upload(f, (".pdf", ".zip", ".rar", ".xls", ".xlsx"))) for f in uploads]
    tender_id = str(uuid.uuid4())
    folder = pipeline.uploads_dir(tender_id)
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for f, content in contents:
        name = _safe_name(f.filename)
        while (folder / name).exists():
            name = f"_{name}"
        (folder / name).write_bytes(content)
        names.append(name)
    label = names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} more"
    tender = Tender(id=tender_id, title=label, tender_doc_name=label, tender_doc_path=str(folder),
                    status="Draft", extraction_status="pending")
    session.add(tender)
    session.commit()
    jobs.submit(pipeline.process_tender, tender_id)
    return ok({"id": tender_id, "status": tender.status}, "Tender created")


@router.get("/tenders/{tender_id}")
def get_tender(tender_id: str, session: Session = Depends(get_session)):
    return ok(pipeline.serialize_tender(session, _tender_or_404(session, tender_id)))


@router.delete("/tenders/{tender_id}")
def delete_tender(tender_id: str, session: Session = Depends(get_session)):
    tender = _tender_or_404(session, tender_id)
    session.delete(tender)
    session.commit()
    pipeline.remove_tender_files(tender_id)
    return ok({"id": tender_id}, "Tender deleted")


def _serve_tender_doc(doc: TenderDocument) -> FileResponse:
    path = Path(doc.file_path).resolve()
    if pipeline.tender_dir(doc.tender_id).resolve() not in path.parents:  # never serve outside this tender's folder
        raise ApiError(404, "AUTH_003", "Not found.")
    is_pdf = path.suffix.lower() == ".pdf"
    return FileResponse(path, media_type="application/pdf" if is_pdf else "application/octet-stream",
                        filename=doc.file_name, content_disposition_type="inline" if is_pdf else "attachment")


@router.get("/tenders/{tender_id}/document")
def tender_document(tender_id: str, session: Session = Depends(get_session)):
    """The primary document (first notice PDF) of the bundle."""
    tender = _tender_or_404(session, tender_id)
    pdfs = [d for d in tender.documents if d.file_name.lower().endswith(".pdf")]
    doc = next((d for d in pdfs if d.doc_type == "notice"), pdfs[0] if pdfs else None)
    if doc is None:
        raise ApiError(404, "AUTH_003", "Not found.")
    return _serve_tender_doc(doc)


@router.get("/tender-documents/{document_id}/file")
def tender_document_file(document_id: str, session: Session = Depends(get_session)):
    doc = session.get(TenderDocument, document_id)
    if doc is None:
        raise ApiError(404, "AUTH_003", "Not found.")
    return _serve_tender_doc(doc)


@router.post("/tenders/{tender_id}/bidders", status_code=201)
async def add_bidder(tender_id: str, files: list[UploadFile] | None = File(None),
                     file: UploadFile | None = File(None), bidder_name: str | None = Form(None),
                     session: Session = Depends(get_session)):
    """A bid is a bundle: the portal's envelope (Fee_PreQual_Technical_<bid id>.zip, holding the EMD
    receipt and the merged document scan), a RAR, or the PDFs themselves."""
    tender = _tender_or_404(session, tender_id)
    if tender.extraction_status != "ready":
        raise ApiError(409, "LIFECYCLE_001", "Tender criteria must be extracted before adding bidders.")
    uploads = [f for f in (files or []) + ([file] if file else []) if f is not None]
    if not uploads:
        raise ApiError(400, "VAL_001", "Upload the bid's files (the envelope zip, or its PDFs).")
    contents = [(f, await _read_upload(f, (".pdf", ".zip", ".rar"))) for f in uploads]

    from app.services.bidder_processing import bid_ref_from_names

    names = [_safe_name(f.filename) for f, _ in contents]
    bidder = Bidder(tender_id=tender.id, bidder_name=(bidder_name or "").strip(), bid_ref=bid_ref_from_names(names))
    session.add(bidder)
    session.flush()
    folder = pipeline.bidder_uploads_dir(tender.id, bidder.id)
    folder.mkdir(parents=True, exist_ok=True)
    for name, (_, content) in zip(names, contents):
        while (folder / name).exists():
            name = f"_{name}"
        (folder / name).write_bytes(content)
    if tender.status == "Draft":  # FSD 7.7: first bidder document moves the tender to Under Evaluation
        tender.status = "Under Evaluation"
    session.commit()
    jobs.submit(pipeline.process_bidder, bidder.id)
    return ok({"id": bidder.id}, "Bid uploaded")


@router.delete("/bidders/{bidder_id}")
def delete_bidder(bidder_id: str, session: Session = Depends(get_session)):
    bidder = session.get(Bidder, bidder_id)
    if bidder is None:
        raise ApiError(404, "AUTH_003", "Not found.")
    tender_id = bidder.tender_id
    session.delete(bidder)
    session.commit()
    pipeline.remove_bidder_files(tender_id, bidder_id)
    return ok({"id": bidder_id}, "Bidder removed")


@router.get("/documents/{document_id}/file")
def bidder_document(document_id: str, session: Session = Depends(get_session)):
    doc = session.get(BidderDocument, document_id)
    if doc is None:
        raise ApiError(404, "AUTH_003", "Not found.")
    path = pipeline.guarded_bidder_path(doc.bidder.tender_id, doc.bidder_id, doc.file_path)
    return FileResponse(path, media_type="application/pdf", filename=doc.file_name, content_disposition_type="inline")


@router.post("/tenders/{tender_id}/evaluate")
def evaluate(tender_id: str, session: Session = Depends(get_session)):
    tender = _tender_or_404(session, tender_id)
    if not tender.criteria:
        raise ApiError(409, "LIFECYCLE_001", "No criteria to evaluate against.")
    busy = [b.bidder_name or "unnamed bidder" for b in tender.bidders if b.status in ("Registered", "Extracting")]
    if busy:
        raise ApiError(409, "LIFECYCLE_001", f"Still reading: {', '.join(busy)}.")
    if not any(b.status == "ClaimsReady" for b in tender.bidders):
        raise ApiError(409, "LIFECYCLE_001", "Upload at least one readable bidder document first.")
    count = pipeline.evaluate_tender(session, tender)
    session.expire_all()
    tender = _tender_or_404(session, tender_id)
    return ok(pipeline.build_evaluation(session, tender, include_ranking=True), f"{count} verdicts generated")


@router.get("/tenders/{tender_id}/verdicts")
def verdicts(tender_id: str, session: Session = Depends(get_session)):
    return ok(pipeline.build_evaluation(session, _tender_or_404(session, tender_id), include_ranking=False))


@router.get("/tenders/{tender_id}/comparison")
def comparison(tender_id: str, session: Session = Depends(get_session)):
    return ok(pipeline.build_evaluation(session, _tender_or_404(session, tender_id), include_ranking=True))


@router.get("/samples")
def samples():
    files = sorted(settings.SAMPLE_DIR.glob("*.pdf")) if settings.SAMPLE_DIR.exists() else []
    return ok([{"name": f.name, "kind": "tender" if "tender" in f.name.lower() else "bidder",
                "size": f.stat().st_size} for f in files])


@router.get("/samples/{name}")
def sample_file(name: str):
    path = (settings.SAMPLE_DIR / Path(name).name).resolve()
    if path.parent != settings.SAMPLE_DIR.resolve() or not path.exists() or path.suffix != ".pdf":
        raise ApiError(404, "AUTH_003", "Not found.")
    return FileResponse(path, media_type="application/pdf", filename=path.name)
