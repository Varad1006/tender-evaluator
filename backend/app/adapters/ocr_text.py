"""Chandra markdown -> ParsedPage, so OCR'd pages flow through the same rules as text pages.

Chandra's markdown carries more than text, and the conversion keeps what matters for evaluation:
  <del>…</del>  struck-out text (the printed "sewer / storm water line" a clerk crossed out) is
               removed from what rules read, and kept in `struck` for the reviewer
  <i>…</i>      handwritten insertions stay in the text; they are often the operative wording
  <u>…</u>      filled-in blanks ("Rs. <u>52.5</u> Lacs") stay in the text
  $\\frac{…}$   LaTeX formulas are flattened to plain text
  | a | b |     markdown tables become table rows, like pdfplumber tables
"""

from __future__ import annotations

import html
import re

from app.adapters.pdf_parser import CLAUSE_PREFIX, ParsedPage, Sentence, _split_sentences
from app.adapters.readability import PageQuality

_DEV = re.compile(r"[\u0900-\u097F]")
_LAT = re.compile(r"[A-Za-z]")


def _flatten_latex(s: str) -> str:
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    for _ in range(3):
        s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1) / (\2)", s)
    s = s.replace("\\times", "×").replace("\\%", "%").replace("\\{", "{").replace("\\}", "}")
    s = re.sub(r"\\[a-zA-Z]+", " ", s)
    return s.replace("$", "")


def clean_inline(s: str) -> tuple[str, list[str], list[str]]:
    """Returns (clean text, struck-out fragments, handwritten fragments)."""
    struck = [html.unescape(re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r"<del>(.*?)</del>", s, re.S)]
    hand = [html.unescape(re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r"<i>(.*?)</i>", s, re.S)]
    s = re.sub(r"<del>.*?</del>", "", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</?(b|i|u|strong|em|sup|sub|span|p)[^>]*>", "", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)  # image references
    s = re.sub(r"\*\*|__", "", s)  # markdown bold
    s = _flatten_latex(html.unescape(s))
    s = re.sub(r"\(\s*\)", "", s)  # "( )" left behind by a struck-out parenthetical
    return s, [x for x in struck if x], [x for x in hand if x]


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def ocr_page(number: int, markdown: str, mode: str) -> ParsedPage:
    lines_out: list[str] = []
    tables: list[list[list[str]]] = []
    blocks: list[tuple[str, str]] = []
    struck_all: list[str] = []
    hand_all: list[str] = []

    current_table: list[list[str]] | None = None
    para: list[str] = []

    def flush_para():
        if para:
            blocks.append(("para", " ".join(para)))
            para.clear()

    for raw_line in markdown.split("\n"):
        line = raw_line.rstrip()
        if line.lstrip().startswith("|"):
            flush_para()
            cells = _cells(line)
            if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in cells):
                continue  # separator row
            row = []
            for c in cells:
                text, struck, hand = clean_inline(c)
                struck_all += struck
                hand_all += hand
                row.append(re.sub(r"\s+", " ", text).strip())
            if current_table is None:
                current_table = []
                tables.append(current_table)
            if any(row):
                current_table.append(row)
                lines_out.append(" | ".join(c for c in row if c))
            continue
        current_table = None
        text, struck, hand = clean_inline(line)
        struck_all += struck
        hand_all += hand
        for piece in text.split("\n"):
            piece = piece.strip()
            if not piece:
                flush_para()
                continue
            lines_out.append(piece.lstrip("#-* ").strip())
            if piece.startswith("#"):
                flush_para()
                blocks.append(("heading", piece.lstrip("# ").strip()))
            elif re.match(r"^[-*]\s+", piece) or re.match(r"^\(?[0-9a-zA-Zivx]{1,4}[.)]\s", piece):
                flush_para()
                para.append(re.sub(r"^[-*]\s+", "", piece))
            else:
                para.append(piece)
    flush_para()

    text = "\n".join(lines_out)
    dev, lat = len(_DEV.findall(text)), len(_LAT.findall(text))
    kind = "ocr_mr" if dev > lat else "ocr_en"
    page = ParsedPage(number=number, text=text, headings=[b[1] for b in blocks if b[0] == "heading"],
                      lines=lines_out, tables=tables, quality=PageQuality(kind), raw_text=text)
    page.ocr_mode = mode  # type: ignore[attr-defined]
    page.struck = struck_all  # type: ignore[attr-defined]
    page.handwritten = hand_all  # type: ignore[attr-defined]
    page.blocks = blocks  # type: ignore[attr-defined]
    return page


def page_sentences(page: ParsedPage, start_section: str = "") -> tuple[list[Sentence], str]:
    """Sentences from an OCR page's paragraphs and table cells (same shape as pdf_parser output)."""
    section, out = start_section, []
    for kind, text in getattr(page, "blocks", []):
        text = re.sub(r"\s+", " ", text).strip()
        if kind == "heading":
            section = CLAUSE_PREFIX.sub("", text)
            continue
        m = CLAUSE_PREFIX.match(text)
        body = text[m.end():] if m else text
        for s in _split_sentences(body):
            out.append(Sentence(page=page.number, text=s, section=section, clause=m.group(1) if m else None))
    for rows in page.tables:
        for row in rows:
            cells = tuple(c for c in row if c)
            for cell in cells:
                for s in _split_sentences(cell):
                    out.append(Sentence(page=page.number, text=s, section=section, in_table=True, row=cells))
    return out, section
