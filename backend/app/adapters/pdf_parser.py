"""PDF parsing adapter: turns a PDF into page-anchored sentences with their section heading.

Every downstream citation (tender requirement or bidder evidence) is a `Sentence` from here,
so the quote shown to the committee is always verbatim text from a known page.

Lessons from the PMC / MahaTender corpus baked in here:
  * Each page is classified for readability (see readability.py). Garbled and image-only pages
    produce no sentences: quoting a bad OCR layer would be worse than saying "not readable".
  * Ruled tables (Tender Work Input Form, PQ tables, B-1 rows) are extracted cell by cell.
    Plain line extraction interleaves the columns of a table and destroys both.
  * Raw lines are kept per page for key-value forms, where paragraph grouping would merge rows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from app.adapters.readability import PageQuality, classify_page

logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Tokens that end with a period but do not end a sentence.
ABBREVIATIONS = {
    "rs", "no", "nos", "mr", "mrs", "ms", "dr", "pvt", "co", "sr", "st", "viz", "vs", "govt", "dept",
    "reg", "cl", "sec", "approx", "m/s", "e.g", "i.e", "inc", "jr", "ref",
}

CLAUSE_PREFIX = re.compile(r"^(\d+(?:\.\d+){1,3})\s+")
ENUM_LINE = re.compile(r"^\(?(?:(\d{1,2})[.)]?|([a-hA-H])[.)])\s+[A-Z(]")


class UnreadablePDF(Exception):
    """Raised when a PDF has no usable text layer (scanned-only or corrupt)."""


@dataclass
class Sentence:
    page: int
    text: str
    section: str
    clause: str | None = None
    in_table: bool = False
    row: tuple[str, ...] | None = None  # the full table row this sentence came from
    lead: str | None = None  # for an enumerated list item: the intro line that governs it ("… shall furnish …-")


@dataclass
class ParsedPage:
    number: int
    text: str
    headings: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    quality: PageQuality = field(default_factory=lambda: PageQuality("english"))
    raw_text: str = ""


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]
    sentences: list[Sentence]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def readable_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.quality.machine_readable]

    def page_text(self, number: int) -> str:
        return self.pages[number - 1].text if 0 < number <= len(self.pages) else ""


def _unwrap_rows(rows: list[list[str]]) -> list[list[str]]:
    """Rejoin a tall table cell that came back as one cell per visual line.

    In a numbered criteria table pdfplumber often rules every line, so one requirement becomes four
    rows — and the neighbouring column's lines are interleaved between them. PMC's DBT RFP p.15
    splits "Manpower Declaration: The Bidder must have an on-roll staff strength of a minimum of 30
    IT Professionals …" across rows 11, 12, 14 and 16, with the evidence column occupying 13 and 15.
    Read row by row it states no number at all.

    The table's own semantics say where a record begins: the serial-number column is filled on the
    first line of a requirement and empty on its continuations. So a row with an empty label column
    is folded into the row above, column by column. Tables without such a column (a plain two-column
    key/value form) are left alone — every row there is its own record.
    """
    width = max((len(r) for r in rows), default=0)
    if width < 2 or sum(1 for r in rows if r and (r[0] or "").strip()) < 2:
        return rows
    out: list[list[str]] = []
    for row in rows:
        cells = [(c or "").strip() for c in row] + [""] * (width - len(row))
        if out and not cells[0] and any(cells[1:]):
            for i in range(1, width):
                if cells[i]:
                    out[-1][i] = f"{out[-1][i]} {cells[i]}".strip()
            continue
        out.append(cells)
    return out


def _is_bold(chars: list[dict]) -> bool:
    visible = [c for c in chars if c["text"].strip()]
    if not visible:
        return False
    bold = sum(1 for c in visible if "bold" in c["fontname"].lower())
    return bold / len(visible) >= 0.9


def _split_sentences(paragraph: str) -> list[str]:
    parts, start = [], 0
    for match in re.finditer(r"[.;?!](?=\s+[A-Z(\"'“])", paragraph):
        end = match.end()
        token = re.search(r"([\w/.]+)\.$", paragraph[start:end])
        if token and token.group(1).lower() in ABBREVIATIONS:
            continue
        # Single capital initials ("A. Kumar") are not sentence ends either.
        if re.search(r"\b[A-Z]\.$", paragraph[start:end]):
            continue
        parts.append(paragraph[start:end].strip())
        start = end
    tail = paragraph[start:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if len(p) > 2]


def _clean(text: str | None) -> str:
    t = re.sub(r"\s+", " ", text or "")
    return re.sub(r"(\w)- (\w)", r"\1-\2", t).strip()


def _tables(page) -> tuple[list[list[list[str]]], list[tuple]]:
    """Ruled tables with at least a few cells. A lone rectangle (page border, boxed note) is not a table."""
    try:
        found = page.find_tables()
    except Exception:  # pdfplumber can trip on malformed drawings; fall back to plain lines
        return [], []
    tables, boxes = [], []
    page_area = float(page.width * page.height)
    for t in found:
        try:
            rows = [[_clean(c) for c in row] for row in t.extract()]
        except Exception:
            continue
        rows = [r for r in rows if any(r)]
        cells = sum(1 for r in rows for c in r if c)
        x0, top, x1, bottom = t.bbox
        if cells < 3 or ((x1 - x0) * (bottom - top) > 0.85 * page_area and cells < 6):
            continue
        tables.append(rows)
        boxes.append(t.bbox)
    return tables, boxes


MARGIN_FRACTION = 0.12  # running headers/footers live in the top/bottom 12% of a page
_PAGE_NO = re.compile(r"^\W*(page\s*)?\d+\s*(of\s*\d+)?\W*$|\bpage\s*\d+\s*of\s*\d+\b", re.I)


def _signature(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.lower())).strip()


def _all_lines(page) -> list[dict]:
    return [ln for ln in page.extract_text_lines(keep_blank_chars=False) if ln["text"].strip()]


def _running_lines(pages_lines: list[tuple[float, list[dict]]]) -> set[str]:
    """Signatures of lines that repeat in the margins of several pages: headers and footers.
    Detected by repetition, not by a fixed margin, so body text that runs close to the edge
    (common in scanned-and-retyped volumes) is kept."""
    counts: dict[str, int] = {}
    for height, lines in pages_lines:
        seen = set()
        for ln in lines:
            if ln["top"] < MARGIN_FRACTION * height or ln["top"] > (1 - MARGIN_FRACTION) * height:
                seen.add(_signature(ln["text"]))
        for sig in seen:
            counts[sig] = counts.get(sig, 0) + 1
    need = max(2, int(0.3 * len(pages_lines)))
    return {sig for sig, n in counts.items() if n >= need}


def _content_lines_from(lines: list[dict], height: float, running: set[str]) -> list[dict]:
    out = []
    for ln in lines:
        in_margin = ln["top"] < MARGIN_FRACTION * height or ln["top"] > (1 - MARGIN_FRACTION) * height
        if in_margin and (_signature(ln["text"]) in running or _PAGE_NO.search(ln["text"])):
            continue
        out.append(ln)
    return out


def _content_lines(page) -> list[dict]:
    """Single-page fallback (no repetition to learn from): drop only page-number furniture."""
    return _content_lines_from(_all_lines(page), page.height, set())


def _paragraph_blocks(lines: list[dict], lead: str | None = None) -> tuple[list[tuple[str, str, str | None]], str | None]:
    """Group consecutive lines into paragraphs by vertical gap; bold short lines are headings.

    Returns (kind, text, lead) blocks and the list lead still open at the end of the page (lists
    run onto the next page). Items of an enumerated list carry the intro that governs them, since
    "2 Valid PF code and ESI Certificate" has no "shall" of its own."""
    heights = sorted(ln["bottom"] - ln["top"] for ln in lines) or [10.0]
    line_height = heights[len(heights) // 2]
    blocks: list[tuple[str, str, str | None]] = []
    current: list[str] = []
    current_lead = lead if lead and lines and ENUM_LINE.match(lines[0]["text"].strip()) else None
    prev_bottom = None
    expected = {"num": 0, "let": 0}  # next list number / letter seen in this run of lines

    def flush():
        nonlocal current
        if current:
            blocks.append(("para", " ".join(current), current_lead))
            current = []

    for ln in lines:
        text = ln["text"].strip()
        # A numbered or lettered list item starts its own paragraph even without a vertical gap:
        # "…detailed out below- / 1 Valid GST … / 2 Valid PF code … / 3 …" must not become one
        # sentence, or the first rule to match it swallows every item.
        m = ENUM_LINE.match(text)
        if m:
            kind, n = ("num", int(m.group(1))) if m.group(1) else ("let", ord(m.group(2).lower()) - 96)
            if current and n == 1 and re.search(r"[:\-–]$", current[-1]):
                intro = " ".join(current)
                flush()
                current_lead = intro
            elif current and (n in (expected[kind], expected[kind] + 1) and expected[kind] > 0
                              or n == 1 and re.search(r"[.;]$", current[-1])):
                flush()
            expected[kind] = n + 1
        if _is_bold(ln["chars"]) and len(text) < 110:
            flush()
            current_lead = None
            blocks.append(("heading", text, None))
            prev_bottom = ln["bottom"]
            continue
        gap = (ln["top"] - prev_bottom) if prev_bottom is not None else 0
        if current and gap > 0.75 * line_height:
            flush()
            if not m:
                current_lead = None  # a new, un-numbered paragraph ends the list
        current.append(text)
        prev_bottom = ln["bottom"]
    flush()
    return blocks, current_lead


@dataclass
class PageScan:
    number: int
    text: str
    quality: PageQuality
    scanned: bool  # page is backed by a full-page image (a scan), as opposed to vector text


# Share of a page's pixels that are ink strokes (clearly darker than their neighbourhood). Scanned
# backsides and separator sheets (letterhead show-through, punch holes, creases) measure < 0.3%;
# the sparsest real text page in the corpus measures 1.3%. OCR on a blank scan is not only wasted:
# Chandra has been seen to invent text on one ("पुस्तक का विवरण" × 10 on scancopies_275 p.8).
BLANK_INK = 0.003


def _ink_ratio(page) -> float:
    from PIL import ImageFilter

    try:
        img = page.render(scale=0.25, grayscale=True).to_pil().convert("L")
    except Exception:
        return 1.0  # cannot tell: treat as content
    pixels, background = img.tobytes(), img.filter(ImageFilter.MedianFilter(9)).tobytes()
    ink = sum(1 for a, b in zip(pixels, background) if b - a > 40)
    return ink / max(1, len(pixels))


def quick_scan(path: str | Path) -> list[PageScan]:
    """Tier-0 local pass with pdfium: text + readability + scan detection for every page.

    ~2 ms/page (the whole 1,100-page PMC corpus scans in 2.4 s), so it runs on every document
    and decides where the slower structural parse and the remote OCR tier are worth spending.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:
        if "password" in str(exc).lower():
            raise UnreadablePDF("The PDF is password-protected. Upload the unprotected copy downloaded from the "
                                "e-tendering portal.") from exc
        raise UnreadablePDF(f"Could not open PDF (damaged or incomplete download?): {exc}") from exc
    if len(doc) == 0:
        raise UnreadablePDF("The PDF has no pages.")
    out = []
    try:
        for index, page in enumerate(doc, start=1):
            try:
                text = page.get_textpage().get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            except Exception:
                text = ""
            width, height = page.get_size()
            area = 0.0
            try:
                for obj in page.get_objects(filter=(pdfium_raw.FPDF_PAGEOBJ_IMAGE,)):
                    left, bottom, right, top = obj.get_bounds()
                    area += max(0.0, right - left) * max(0.0, top - bottom)
            except Exception:
                pass
            quality = classify_page(text)
            scanned = area > 0.5 * width * height or quality.kind == "image"
            if quality.kind == "image" and _ink_ratio(page) < BLANK_INK:
                quality = PageQuality("blank")
            out.append(PageScan(index, text, quality, scanned=scanned))
    finally:
        doc.close()
    return out


def parse_pdf(path: str | Path, strict: bool = True, only_pages: set[int] | None = None,
              scans: list[PageScan] | None = None) -> ParsedDocument:
    """Parse a PDF. With strict=True (bidder documents) a PDF with no readable page is an error;
    with strict=False (tender bundles) it is returned with every page marked unreadable.

    only_pages limits the (slow) structural parse to those pages; the others keep their quick-scan
    text and quality but produce no sentences. Pass the quick-scan result to avoid re-reading."""
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:  # pdfminer raises a variety of types on corrupt input
        raise UnreadablePDF(f"Could not open PDF: {exc}") from exc

    pages: list[ParsedPage] = []
    sentences: list[Sentence] = []
    section = ""
    scan_by_page = {s.number: s for s in scans or []}

    with pdf:
        # Pass 1: classify pages and pull raw lines/tables for the pages we will structure.
        prepared: list[tuple] = []
        for index, page in enumerate(pdf.pages, start=1):
            scan = scan_by_page.get(index)
            if scan is not None:
                raw, quality = scan.text, scan.quality
            else:
                try:
                    raw = page.extract_text() or ""
                except Exception:
                    raw = ""
                quality = classify_page(raw)
            if quality.kind in ("image", "garbled", "blank") or (only_pages is not None and index not in only_pages):
                prepared.append((index, page, raw, quality, None, None))
                continue
            tables, boxes = _tables(page) if quality.machine_readable else ([], [])
            text_page = page
            for box in boxes:
                try:
                    text_page = text_page.outside_bbox(box)
                except Exception:
                    pass
            try:
                lines = _all_lines(text_page)
            except Exception:
                lines = []
            prepared.append((index, page, raw, quality, tables, lines))
        running = _running_lines([(p.height, lines) for _, p, _, _, _, lines in prepared if lines])

        # Pass 2: paragraphs, headings, sentences.
        list_lead: str | None = None
        for index, page, raw, quality, tables, lines in prepared:
            if lines is None:
                pages.append(ParsedPage(number=index, text="", quality=quality, raw_text=raw))
                continue
            lines = _content_lines_from(lines, page.height, running)
            blocks, list_lead = _paragraph_blocks(lines, list_lead)

            headings: list[str] = []
            page_parts: list[str] = []
            for kind, text, lead in blocks:
                text = _clean(text)
                page_parts.append(text)
                if kind == "heading":
                    section = CLAUSE_PREFIX.sub("", text)
                    headings.append(text)
                    continue
                clause_match = CLAUSE_PREFIX.match(text)
                clause = clause_match.group(1) if clause_match else None
                body = text[clause_match.end():] if clause_match else text
                for sentence in _split_sentences(body):
                    sentences.append(Sentence(page=index, text=sentence, section=section, clause=clause,
                                              lead=_clean(lead) if lead else None))

            for rows in tables:
                for row in rows:
                    page_parts.append(" | ".join(c for c in row if c))
                for row in _unwrap_rows(rows):
                    cells = tuple(c for c in row if c)
                    for cell in cells:
                        clause_match = CLAUSE_PREFIX.match(cell)
                        body = cell[clause_match.end():] if clause_match else cell
                        for sentence in _split_sentences(body):
                            sentences.append(Sentence(page=index, text=sentence, section=section,
                                                      clause=clause_match.group(1) if clause_match else None,
                                                      in_table=True, row=cells))

            pages.append(ParsedPage(
                number=index, text="\n".join(page_parts), headings=headings,
                lines=[_clean(ln["text"]) for ln in lines], tables=tables, quality=quality, raw_text=raw,
            ))

    if strict and not any(p.quality.machine_readable or p.quality.kind == "marathi" for p in pages):
        raise UnreadablePDF(
            "Could not read this PDF. It has no usable text layer (scanned image or garbled OCR). "
            "Try a text-layer PDF; OCR is not part of the PoC."
        )
    return ParsedDocument(pages=pages, sentences=sentences)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_on_page(quote: str, doc: ParsedDocument, page: int) -> bool:
    """Quote-then-extract guard: an LLM-returned quote is only accepted if it is verbatim on that page."""
    return bool(quote) and normalise(quote) in normalise(doc.page_text(page))
