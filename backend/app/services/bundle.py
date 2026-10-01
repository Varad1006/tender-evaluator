"""Tender bundles: a MahaTender download is not one PDF.

Observed shapes (PMC corpus, 13 tenders): a zip holding the tender notice PDF, a shared
general-conditions PDF (often scanned), a BOQ .xls, and sometimes a .rar with more PDFs
(data sheet, detailed conditions, technical specifications, SOPs). This module expands
archives recursively, keeps a trail of where each file came from, and classifies each
document's role so eligibility rules only run where requirements actually live.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_DEPTH = 3
MAX_FILES = 60
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200  # a zip member expanding more than this is treated as a zip bomb
SUPPORTED = {".pdf", ".xls", ".xlsx"}
IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
OFFICE = {".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx"}
ARCHIVES = {".zip", ".rar"}

_MAGIC = [(b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"), (b"Rar!", ".rar"), (b"\xd0\xcf\x11\xe0", ".xls"),
          (b"\xff\xd8\xff", ".jpg"), (b"\x89PNG", ".png"), (b"II*\x00", ".tif"), (b"MM\x00*", ".tif")]


def sniff_suffix(name: str, path: Path) -> str:
    """Trust content over the file name: portals and users drop or mangle extensions."""
    suffix = Path(name).suffix.lower()
    try:
        head = path.read_bytes()[:8]
    except OSError:
        return suffix
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            if ext == ".zip" and suffix in (".xlsx", ".docx", ".pptx", ".odt"):
                return suffix  # OOXML files are zips too
            if ext == ".xls" and suffix in (".doc", ".ppt"):
                return suffix
            return ext
    return suffix


class BundleError(Exception):
    pass


@dataclass
class BundleFile:
    path: Path
    name: str
    origin: str  # e.g. "work_2291063.zip › Tenderdocuments.rar › DATASHEET.pdf"
    kind: str = ".pdf"  # sniffed from content: .pdf | .xls | .xlsx


def _skip(member: str) -> bool:
    parts = Path(member).parts
    # ".." is not junk: it is left to the path guard, which rejects and reports it
    return any(p.startswith("__MACOSX") or (p.startswith(".") and p != "..") for p in parts) or member.endswith("/")


def _safe_target(root: Path, member: str) -> Path | None:
    target = (root / member).resolve()
    return target if root.resolve() in target.parents else None  # zip-slip guard


def _member_name(info: zipfile.ZipInfo) -> str:
    """Zips made on Indian Windows machines store UTF-8 (Marathi) names without the UTF-8 flag."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _extract_zip(archive: Path, dest: Path, warnings: list[str]) -> list[Path]:
    out = []
    with zipfile.ZipFile(archive) as zf:
        members = [i for i in zf.infolist() if not _skip(i.filename)]
        declared = sum(i.file_size for i in members)
        if declared > MAX_TOTAL_BYTES:
            raise BundleError(f"{archive.name}: expands to {declared // (1 << 20)} MB, above the "
                              f"{MAX_TOTAL_BYTES // (1 << 20)} MB bundle limit.")
        for info in members:
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO and info.file_size > 50 << 20:
                warnings.append(f"{archive.name} › {info.filename}: suspicious compression ratio, skipped.")
                continue
            name = _member_name(info)
            target = _safe_target(dest, name)
            if target is None:
                warnings.append(f"{archive.name} › {name}: path escapes the archive, skipped.")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as fh:
                    shutil.copyfileobj(src, fh)
            except RuntimeError as exc:  # encrypted member
                warnings.append(f"{archive.name} › {name}: password-protected, skipped ({exc}).")
                continue
            out.append(target)
    return out


def _extract_rar(archive: Path, dest: Path) -> list[Path]:
    # libarchive's bsdtar reads RAR (the macOS default `tar` is bsdtar). GNU tar cannot.
    tool = shutil.which("bsdtar") or shutil.which("tar")
    if not tool:
        raise BundleError(f"{archive.name}: RAR archives need bsdtar on the server — upload the extracted PDFs instead.")
    dest.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([tool, "-xf", str(archive), "-C", str(dest)], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        msg = proc.stderr.strip()[:200] or "unsupported format"
        if re.search(r"passphrase|password|encrypt", msg, re.I):
            msg = "password-protected"
        raise BundleError(f"{archive.name}: could not extract RAR ({msg}).")
    files = [p for p in dest.rglob("*") if p.is_file() and not _skip(str(p.relative_to(dest)))
             and _safe_target(dest, str(p.relative_to(dest)))]
    if sum(p.stat().st_size for p in files) > MAX_TOTAL_BYTES:
        shutil.rmtree(dest, ignore_errors=True)
        raise BundleError(f"{archive.name}: expands above the {MAX_TOTAL_BYTES // (1 << 20)} MB bundle limit.")
    return files


def image_to_pdf(path: Path, dest_dir: Path) -> Path:
    """A photographed or scanned page (jpg/png/tiff, multi-page tiff too) becomes a PDF of scanned pages,
    so it flows through the same scan detection and OCR routing as any scanned tender document."""
    from PIL import Image, ImageSequence

    img = Image.open(path)
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(img)]
    out = dest_dir / f"{path.stem}.pdf"
    dest_dir.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, "PDF", save_all=True, append_images=frames[1:], resolution=200)
    return out


def expand(uploads: list[tuple[str, Path]], workdir: Path) -> tuple[list[BundleFile], list[str]]:
    """uploads: (original filename, stored path). Returns supported files plus warnings."""
    files: list[BundleFile] = []
    warnings: list[str] = []
    total = 0
    seen_hashes: dict[str, str] = {}
    # Extracted members come back as resolved paths; compare them against a resolved folder, or a
    # workdir under a symlink (macOS /var → /private/var) makes every archive member "outside" it.
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    workdir = workdir.resolve()

    def visit(name: str, path: Path, origin: str, depth: int) -> None:
        nonlocal total
        if path.stat().st_size == 0:
            warnings.append(f"{origin}: empty file, skipped.")
            return
        suffix = sniff_suffix(name, path)
        if suffix in ARCHIVES:
            if depth >= MAX_DEPTH:
                warnings.append(f"{origin}: nested too deeply, skipped.")
                return
            dest = workdir / f"x_{uuid.uuid4().hex[:8]}"
            try:
                members = _extract_zip(path, dest, warnings) if suffix == ".zip" else _extract_rar(path, dest)
            except (zipfile.BadZipFile, BundleError, subprocess.TimeoutExpired, OSError) as exc:
                warnings.append(f"{origin}: {exc}")
                return
            if not members:
                warnings.append(f"{origin}: archive is empty.")
            for m in sorted(members):
                visit(m.name, m, f"{origin} › {m.relative_to(dest)}", depth + 1)
            return
        if suffix in IMAGES:
            try:
                path = image_to_pdf(path, workdir / f"img_{uuid.uuid4().hex[:8]}")
                name, suffix = f"{Path(name).stem}.pdf", ".pdf"
                origin += " (image → PDF)"
            except Exception as exc:
                warnings.append(f"{origin}: could not read image ({exc}).")
                return
        if suffix in OFFICE:
            warnings.append(f"{origin}: {suffix} documents are not read — export it to PDF and upload the PDF.")
            return
        if suffix not in SUPPORTED:
            warnings.append(f"{origin}: {suffix or 'unknown'} files are not read, skipped.")
            return
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen_hashes:
            warnings.append(f"{origin}: identical to {seen_hashes[digest]}, read once.")
            return
        seen_hashes[digest] = origin
        size = path.stat().st_size
        total += size
        if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            warnings.append(f"{origin}: bundle size limit reached, skipped.")
            return
        if Path(name).suffix.lower() != suffix:
            name = f"{Path(name).stem}{suffix}" if not Path(name).suffix else name
        files.append(BundleFile(path=path, name=name, origin=origin, kind=suffix))

    for name, path in uploads:
        visit(name, path, name, 0)
    return files, warnings


# --------------------------------------------------------------------------------------
# Document roles
# --------------------------------------------------------------------------------------

DOC_TYPES = {
    "notice": "Tender notice",
    "conditions": "Tender conditions",
    "rfp": "Request for proposal",
    "datasheet": "Data sheet / qualification parameters",
    "boq": "Bill of quantities",
    "specifications": "Technical specifications / SOP / QAP",
    "approval": "Internal approval",
    "other": "Other",
}

# Documents that describe how to *execute* the work; eligibility rules do not run on them.
NON_ELIGIBILITY_TYPES = {"boq", "specifications", "approval"}

_NAME_HINTS = [
    (r"boq|abstract|\babs\b", "boq"),
    (r"specification|\bsop\b|qap|quality", "specifications"),
    (r"data ?sheet", "datasheet"),
    (r"terms|atisharti|condition|dtp|goshwara|volume", "conditions"),
    (r"notice|tender\d*$|^\d+$", "notice"),
    (r"rfp|proposal", "rfp"),
]

_TEXT_HINTS = [
    (r"REQUEST FOR PROPOSAL", "rfp"),
    # A conditions volume also carries "TENDER NO:" on its cover, so it is checked first.
    (r"FINAL Tender Conditions|CHECK LIST|VOLUME\s*-\s*I\b|INSTRUCTIONS TO TENDERER", "conditions"),
    (r"Parameters for Qualification Criteria|DATA SHEET", "datasheet"),
    (r"Tender Work Input Form|Tender Notice|TENDER No\.?\s*:|TENDER DOCUMENT", "notice"),
    (r"Tender Abstract|Abstract Estimate", "boq"),
    (r"QUALITY ASSURANCE|SOP for|Chapter\s*:?\s*1\s*-?\s*SOP|Technical Specifications|Chapter 1 WIRING", "specifications"),
]


_CORRIGENDUM_TITLE = re.compile(r"^\s*(?:CORRIGENDUM|ADDENDUM|AMENDMENT)(?:\s*(?:No\.?|-|–)\s*\w+)?\b|शुद्धिपत्रक|शुध्दीपत्रक|शुद्धीपत्रक",
                                re.M | re.I)


def classify_document(name: str, first_pages_text: str, page_count: int | None = None) -> str:
    stem = Path(name).stem.lower()
    if Path(name).suffix.lower() in (".xls", ".xlsx"):
        return "boq"
    # A corrigendum is a short document titled as such. Volumes mention "Corrigendum" in their
    # contents and clauses ("… as per NIT and Corrigendum/Addendum"), so length and position matter.
    if (page_count is None or page_count <= 8) and (
            _CORRIGENDUM_TITLE.search((first_pages_text or "")[:600]) or re.search(r"corrig|addend|shuddhi", stem)):
        return "corrigendum"
    # Ward-office notices are named by tender number ("155.pdf") and open with the Tender
    # Abstract, so page 1 alone would call them a BOQ.
    if re.fullmatch(r"\d{2,4}", stem):
        return "notice"
    for pattern, kind in _TEXT_HINTS:
        if re.search(pattern, first_pages_text or "", re.IGNORECASE):
            return kind
    for pattern, kind in _NAME_HINTS:
        if re.search(pattern, stem):
            return kind
    return "other"
