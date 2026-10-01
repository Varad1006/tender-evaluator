"""Hybrid tender-processing pipeline (independent of the database, so scripts and the API share it).

    uploads ─► expand archives ─► per document, in parallel:
                                    pdfium quick scan (every page, ~2 ms/page): text, readability, scan?
                                    pdfplumber structure (readable pages of eligibility docs only)
                                    xlrd (BOQ spreadsheets)
            ─► extract (facts, PMC standard criteria, rules, checklists)      ← results visible in seconds
            ─► OCR router: pages the local pass cannot read, eligibility docs only, by priority
                   cache hit (same file hash + page)  → reuse
                   vector page, broken/lossy text      → Datalab "fast"
                   scanned page (phone scans, handwriting) → Chandra "balanced"
               batched ≤10 pages/request, 4 requests in flight, rate-limited, retried
            ─► merge OCR pages ─► extract again (derived criteria become located and quoted)

Nothing waits on OCR: the local extraction is persisted first, and an OCR failure leaves it in place.
"""

from __future__ import annotations

import logging
import multiprocessing
import re
import time
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.boq_xls import BoqSummary, read_boq
from app.adapters.datalab_ocr import DatalabOcr, OcrJob, OcrPage, file_sha256, get_ocr
from app.adapters.llm import get_llm
from app.adapters.ocr_text import ocr_page, page_sentences
from app.adapters.pdf_parser import PageScan, ParsedDocument, ParsedPage, UnreadablePDF, parse_pdf, quick_scan
from app.adapters.readability import NEEDS_OCR
from app.config import settings
from app.services.bundle import NON_ELIGIBILITY_TYPES, BundleFile, classify_document, expand
from app.services.bundle_extraction import extract_bundle
from app.services.criteria_extraction import TenderExtraction
from app.services.pmc_eligibility import DocView

log = logging.getLogger(__name__)

OCR_PRIORITY = {"corrigendum": 0, "datasheet": 0, "notice": 1, "conditions": 2, "rfp": 3, "other": 4}
MODE_RANK = {"fast": 0, "balanced": 1, "accurate": 2}
PARALLEL_PARSE_MIN_PAGES = 60


@dataclass
class ProcessedDoc:
    document_id: str
    name: str
    origin: str
    path: Path
    doc_type: str
    file_hash: str
    scans: list[PageScan] = field(default_factory=list)
    parsed: ParsedDocument | None = None
    boq: BoqSummary | None = None
    error: str | None = None
    ocr_modes: dict[int, str] = field(default_factory=dict)  # page -> mode actually applied

    @property
    def view(self) -> DocView:
        return DocView(self.document_id, self.name, self.doc_type, self.parsed)


@dataclass
class BundleResult:
    docs: list[ProcessedDoc]
    extraction: TenderExtraction
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    ocr_pages: int = 0
    ocr_cached: int = 0
    ocr_errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# Local tier
# --------------------------------------------------------------------------------------

def _structural_parse(path: str, pages: list[int], scans: list[PageScan]) -> ParsedDocument:
    return parse_pdf(path, strict=False, only_pages=set(pages), scans=scans)


def _scan_only(scans: list[PageScan]) -> ParsedDocument:
    return ParsedDocument(pages=[ParsedPage(number=s.number, text="", quality=s.quality, raw_text=s.text) for s in scans],
                          sentences=[])


def local_pass(files: list[BundleFile], ids: list[str] | None = None, role: str = "tender") -> list[ProcessedDoc]:
    """role="bidder": a bid's files have no tender roles and any page may carry evidence, so every
    document is structurally parsed (its type is decided per page later, by bidder_documents)."""
    docs: list[ProcessedDoc] = []
    for i, f in enumerate(files):
        doc = ProcessedDoc(ids[i] if ids else str(uuid.uuid4()), f.name, f.origin, f.path, "other", file_sha256(f.path))
        if f.kind in (".xls", ".xlsx"):
            doc.doc_type, doc.boq = "boq", read_boq(f.path, f.kind)
            doc.error = doc.boq.error
        else:
            try:
                doc.scans = quick_scan(f.path)
                doc.doc_type = "bid" if role == "bidder" else \
                    classify_document(f.name, "\n".join(s.text for s in doc.scans[:2]), len(doc.scans))
            except UnreadablePDF as exc:
                doc.error = str(exc)
        docs.append(doc)

    # Structural parse only where requirements live, and only on pages with a usable text layer.
    todo = [(d, [s.number for s in d.scans if s.quality.kind in ("english", "marathi")])
            for d in docs if d.scans and (role == "bidder" or d.doc_type not in NON_ELIGIBILITY_TYPES)]
    todo = [(d, pages) for d, pages in todo if pages]
    for d in docs:
        if d.scans and d.parsed is None:
            d.parsed = _scan_only(d.scans)  # replaced below for docs that get a structural parse
    total_pages = sum(len(p) for _, p in todo)
    if len(todo) >= 2 and total_pages >= PARALLEL_PARSE_MIN_PAGES:
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=min(4, len(todo)), mp_context=ctx) as pool:
            futures = [(d, pool.submit(_structural_parse, str(d.path), pages, d.scans)) for d, pages in todo]
            for d, fut in futures:
                try:
                    d.parsed = fut.result()
                except Exception as exc:  # one bad PDF must not stall the bundle
                    d.error = f"Could not parse: {exc}"
    else:
        for d, pages in todo:
            try:
                d.parsed = _structural_parse(str(d.path), pages, d.scans)
            except Exception as exc:
                d.error = f"Could not parse: {exc}"
    return docs


# --------------------------------------------------------------------------------------
# OCR tier
# --------------------------------------------------------------------------------------

def _mixed_devanagari(text: str) -> bool:
    """An 'english' page that also carries a substantial lossy-Devanagari passage (a Marathi clause
    inside an English notice): worth OCR so the Marathi can be quoted, not just cited."""
    dev = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    return dev >= 150


def ocr_pages(d: ProcessedDoc) -> list[tuple[int, str]]:
    """(page, mode) for each page of one document that the local tier could not read."""
    out = []
    for s in d.scans:
        if not s.scanned and len(s.text.strip()) < 150:
            continue  # a vector page with almost no text is blank (letterhead/footer only): nothing to read
        if s.quality.kind in NEEDS_OCR or (s.quality.kind == "english" and _mixed_devanagari(s.text)):
            out.append((s.number, settings.OCR_MODE_SCANNED if s.scanned else settings.OCR_MODE_VECTOR))
    return out


def plan_ocr_all(docs: list[ProcessedDoc]) -> list[tuple[ProcessedDoc, int, str]]:
    candidates = []
    for order, d in enumerate(docs):
        if d.doc_type not in OCR_PRIORITY or not d.scans:
            continue
        for page, mode in ocr_pages(d):
            candidates.append((OCR_PRIORITY[d.doc_type], order, page, d, mode))
    candidates.sort(key=lambda c: c[:3])
    return [(d, page, mode) for *_, page, d, mode in candidates]


def plan_ocr(docs: list[ProcessedDoc]) -> list[tuple[ProcessedDoc, int, str]]:
    """(doc, page, mode) for every page worth sending to OCR, most valuable first, capped."""
    return plan_ocr_all(docs)[: settings.OCR_MAX_PAGES_PER_TENDER]


def skipped_by_cap(docs: list[ProcessedDoc]) -> list[str]:
    extra = plan_ocr_all(docs)[settings.OCR_MAX_PAGES_PER_TENDER:]
    if not extra:
        return []
    by_doc: dict[str, int] = {}
    for d, _, _ in extra:
        by_doc[d.name] = by_doc.get(d.name, 0) + 1
    return [f"{n} unreadable page(s) of {name} were not sent to OCR (per-tender cap of "
            f"{settings.OCR_MAX_PAGES_PER_TENDER} pages; raise TE_OCR_MAX_PAGES_PER_TENDER to include them)."
            for name, n in by_doc.items()]


def batch_jobs(plan: list[tuple[ProcessedDoc, int, str]]) -> list[tuple[ProcessedDoc, OcrJob]]:
    groups: dict[tuple[str, str], list[int]] = {}
    by_id = {}
    for d, page, mode in plan:
        groups.setdefault((d.document_id, mode), []).append(page)
        by_id[d.document_id] = d
    jobs = []
    for (doc_id, mode), pages in groups.items():
        d = by_id[doc_id]
        pages.sort()
        for i in range(0, len(pages), settings.OCR_BATCH_PAGES):
            jobs.append((d, OcrJob(d.path, d.file_hash, pages[i:i + settings.OCR_BATCH_PAGES], mode)))
    return jobs


def looks_hallucinated(markdown: str) -> bool:
    """A vision model asked to read a near-empty page sometimes invents a form, repeating one line
    ("पुस्तक का विवरण" ten times on a blank scan). Real pages rarely repeat a line of words that often."""
    lines = [re.sub(r"\s+", " ", ln).strip(" |#*-") for ln in markdown.splitlines()]
    lines = [ln for ln in lines if len(re.findall(r"\w", ln)) >= 4]
    if len(lines) < 6:
        return False
    top = Counter(lines).most_common(1)[0][1]
    return top >= 5 and top >= 0.5 * len(lines)


def apply_ocr(doc: ProcessedDoc, pages: list[OcrPage]) -> None:
    if doc.parsed is None:
        doc.parsed = _scan_only(doc.scans)
    replaced = set()
    for op in pages:
        if not op.markdown.strip() or not (0 < op.page <= len(doc.parsed.pages)):
            continue
        if looks_hallucinated(op.markdown):
            log.warning("Ignoring repetitive OCR output for %s p.%s", doc.name, op.page)
            continue
        current = doc.parsed.pages[op.page - 1]
        # Never replace a good English text layer with a thin OCR result (e.g. a mostly blank page).
        if current.quality.kind == "english" and len(op.markdown) < 0.5 * len(current.raw_text or current.text):
            continue
        doc.parsed.pages[op.page - 1] = ocr_page(op.page, op.markdown, op.mode)
        doc.ocr_modes[op.page] = op.mode
        replaced.add(op.page)
    if not replaced:
        return
    kept = [s for s in doc.parsed.sentences if s.page not in replaced]
    section = ""
    for n in sorted(replaced):
        sents, section = page_sentences(doc.parsed.pages[n - 1], section)
        kept.extend(sents)
    doc.parsed.sentences = sorted(kept, key=lambda s: s.page)


def run_ocr(docs: list[ProcessedDoc], ocr: DatalabOcr, on_progress=None,
            plan: list[tuple[ProcessedDoc, int, str]] | None = None) -> tuple[int, int, list[str]]:
    plan = plan_ocr(docs) if plan is None else plan
    if not plan:
        return 0, 0, []
    todo, cached = [], 0
    for d, page, mode in plan:
        hit = ocr.cached(d.file_hash, page, tuple(m for m, r in MODE_RANK.items() if r >= MODE_RANK.get(mode, 0)))
        if hit:
            apply_ocr(d, [hit])
            cached += 1
        else:
            todo.append((d, page, mode))
    done = {"pages": cached}
    total = len(plan)
    if on_progress:
        on_progress(done["pages"], total)
    jobs = batch_jobs(todo)
    doc_for_job = {id(job): d for d, job in jobs}

    def finished(job: OcrJob, pages: list[OcrPage]):
        done["pages"] += len(job.pages)
        if on_progress:
            on_progress(done["pages"], total)

    results, errors = ocr.run([job for _, job in jobs], on_done=finished)
    for job, pages in results:
        apply_ocr(doc_for_job[id(job)], pages)
    return total, cached, errors


def _ranges(pages: list[int]) -> str:
    out, start = [], None
    for a, b in zip(pages, pages[1:] + [None]):
        start = a if start is None else start
        if b != a + 1:
            out.append(f"{start}–{a}" if a != start else str(a))
            start = None
    return ", ".join(out)


def coverage_warnings(docs: list[ProcessedDoc], ocr_available: bool, ocr_errors: list[str] | None = None) -> list[str]:
    """Say which requirement-bearing pages were never read, so a missing criterion is not mistaken
    for an absent one."""
    out: list[str] = []
    readable_docs = [d for d in docs if d.doc_type not in NON_ELIGIBILITY_TYPES and d.scans]
    if not readable_docs and any(d.boq for d in docs):
        out.append("Only a BOQ spreadsheet was uploaded: eligibility criteria live in the tender notice / conditions "
                   "PDFs, so add those to extract them.")
    capped = {(d.document_id, page) for d, page, _ in plan_ocr_all(docs)[settings.OCR_MAX_PAGES_PER_TENDER:]} \
        if ocr_available else set()  # reported by skipped_by_cap()
    for d in readable_docs:
        if d.parsed is None:
            continue
        scans = {s.number: s for s in d.scans}
        unread = [p.number for p in d.parsed.pages if p.quality.kind in ("image", "garbled")
                  and (d.document_id, p.number) not in capped
                  and (scans.get(p.number) is None or scans[p.number].scanned or len(scans[p.number].text.strip()) >= 150)]
        if not unread:
            continue
        failed_here = [e for e in ocr_errors or [] if e.startswith(d.path.name)]
        why = ("OCR failed for them" if failed_here else "OCR returned no text for them") if ocr_available else \
            "OCR is not configured (set DATALAB_API_KEY)"
        out.append(f"{len(unread)} page(s) of {d.name} could not be read (p. {_ranges(unread)}) — {why}. "
                   "Requirements on those pages are not extracted; check them manually.")
    for d in docs:
        if d.error:
            out.append(f"{d.name}: {d.error}")
    return out


# --------------------------------------------------------------------------------------

def extract(docs: list[ProcessedDoc]) -> TenderExtraction:
    boqs = [(d.document_id, d.name, d.boq) for d in docs if d.boq and not d.boq.error]
    return extract_bundle([d.view for d in docs if d.parsed is not None or d.boq is not None], boqs, llm=get_llm())


def process_bundle(uploads: list[tuple[str, Path]], workdir: Path, use_ocr: bool = True,
                   on_local=None, on_ocr_start=None, on_ocr_progress=None) -> BundleResult:
    t0 = time.time()
    files, warnings = expand(uploads, workdir)
    docs = local_pass(files)
    extraction = extract(docs)
    result = BundleResult(docs, extraction, warnings, {"local": time.time() - t0})
    if on_local:
        on_local(result)

    ocr = get_ocr() if use_ocr else None
    if ocr is None:
        result.warnings.extend(coverage_warnings(docs, ocr_available=False))
        return result
    t1 = time.time()
    if on_ocr_start:
        on_ocr_start(len(plan_ocr(docs)))
    total, cached, errors = run_ocr(docs, ocr, on_progress=on_ocr_progress)
    result.ocr_pages, result.ocr_cached, result.ocr_errors = total, cached, errors
    result.warnings.extend(skipped_by_cap(docs))
    result.warnings.extend(w for w in coverage_warnings(docs, True, errors) if w not in result.warnings)
    if total:
        result.extraction = extract(docs)
    result.timings["ocr"] = time.time() - t1
    return result
