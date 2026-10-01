"""Key tender facts from MahaTender / PMC structured sections, reconciled across sources.

The same number (estimated cost, EMD, period...) is printed in up to six places in one PMC
bundle: the Tender Notice box, the Tender Work Input Form, the B-1 row, the conditions volume's
tender table, the Tender Abstract total and the BOQ spreadsheet. Any single source can be
wrong or blank, so every fact keeps all its sources (document + page + line) and is marked
consistent / single source / conflict. Downstream formulas only use a fact when its sources
agree, or when there is exactly one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.values import format_inr


@dataclass
class FactSource:
    document_id: str
    doc_name: str
    page: int | None
    quote: str
    value: object
    method: str  # which structured section produced it
    ocr: bool = False  # read by OCR (possibly handwriting) rather than from a text layer or spreadsheet
    lossy: bool = False  # Devanagari from a PDF text layer: conjuncts dropped, never display it as-is
    amended: bool = False  # from a corrigendum / addendum: supersedes the original tender's value


@dataclass
class Fact:
    key: str
    label: str
    kind: str  # money | months | text
    sources: list[FactSource] = field(default_factory=list)
    value: object = None
    status: str = "missing"  # consistent | single_source | conflict | missing
    note: str | None = None

    @property
    def display(self) -> str:
        if self.value is None:
            return "—"
        if self.kind == "money":
            return format_inr(float(self.value))
        if self.kind == "months":
            return f"{self.value} months"
        return str(self.value)


FACT_LABELS = {
    "ref_no": ("Tender reference", "text"),
    "ref_no_local": ("Tender number (office)", "text"),
    "work_name": ("Name of work", "text"),
    "work_name_mr": ("Name of work (Marathi)", "text"),
    "department": ("Department", "text"),
    "tender_category": ("Tender category", "text"),
    "product_category": ("Product category", "text"),
    "form_of_contract": ("Form of contract", "text"),
    "contract_type": ("Contract type", "text"),
    "estimated_cost": ("Estimated cost put to tender", "money"),
    "total_estimated_cost": ("Total estimated cost (incl. GST)", "money"),
    "emd": ("Earnest money deposit", "money"),
    "security_deposit": ("Security deposit", "money"),
    "completion_months": ("Completion period", "months"),
    "rainy_season": ("Monsoon in period", "text"),
    "dlp_months": ("Defect liability period", "months"),
    "cost_of_tender_doc": ("Cost of tender document", "money"),
    "pre_bid_meeting": ("Pre-bid meeting", "text"),
}

# "PMC/ROAD/2026/123", and zone tenders "PMC/ZONE-9/2026-27/OFFICE-NAME/7" (financial year,
# then the office). A space after a slash is a line break or an OCR artefact.
_REF = re.compile(r"\bPMC/[A-Z0-9][A-Z0-9\-]*(?:/[A-Z0-9\-]+)*?/20\d\d(?:-\d{2})?(?:/ ?[A-Z][A-Z0-9\-]*)*/\d+\b")
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEV = re.compile(r"[\u0900-\u097F]")

# Portal-form labels -> (fact key, value type). Same wording in the Tender Notice box, the
# Tender Work Input Form and the drainage Data Sheet, whether printed or scanned.
_TABLE_LABELS = [
    (r"Type of Contract", ("contract_type", "text")),
    (r"Completion Month", ("completion_months", "int")),
    (r"Rainy Season", ("rainy_season", "title")),
    (r"Cost of Tender", ("cost_of_tender_doc", "money")),
    (r"Estimated Cost\s*\(Work", ("estimated_cost", "money")),
    (r"Earnest Money Deposit", ("emd", "money")),
    (r"Security Deposit", ("security_deposit", "money")),
    (r"Tender Reference No", ("ref_no", "ref")),
    (r"Tender Number", ("ref_no", "ref")),
    (r"Form of Contract", ("form_of_contract", "title")),
    (r"Tender Category", ("tender_category", "title")),
    (r"Product Category", ("product_category", "title")),
    (r"Tender Value Estimated cost Without", ("estimated_cost", "money")),
    (r"Estimated Cost put to the tender", ("estimated_cost", "money")),
    (r"Total Estimated Cost", ("total_estimated_cost", "money")),
    (r"Completion Period", ("completion_months", "int")),
    (r"EMD Fee", ("emd", "money")),
    (r"Pre Bid Meeting\s*:", ("pre_bid_meeting", "title")),
    (r"Work Name\s*\(In English\)", ("work_name", "text")),
]


def _label(cell: str) -> tuple[str, str] | None:
    c = (cell or "").strip()
    if not c or len(c) > 140:
        return None
    for pattern, target in _TABLE_LABELS:
        if re.match(r"^(?:\d+\.?\s*)?" + pattern, c, re.I):
            return target
    return None


def _prose_label(text: str) -> tuple[str, str] | None:
    t = text.lower()
    if re.search(r"estimated\s*cost", t):
        return ("total_estimated_cost", "money") if re.search(r"total|incl", t) else ("estimated_cost", "money")
    if re.search(r"earnest|\bemd\b", t):
        return ("emd", "money")
    if re.search(r"security\s*deposit", t):
        return ("security_deposit", "money")
    if re.search(r"completion|time\s*limit", t):
        return ("completion_months", "int")
    return None


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _line_with(text: str, needle: str) -> str:
    for line in text.split("\n"):
        if needle in line:
            return line.strip()[:240]
    return needle


class FactCollector:
    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {k: Fact(k, *v) for k, v in FACT_LABELS.items()}
        self.ocr = False  # set by the caller while scanning an OCR page
        self.text_layer = False  # set while scanning a PDF text layer (its Devanagari is lossy)
        self.amending = False  # set while scanning a corrigendum

    def add(self, key: str, value, doc_id: str, doc_name: str, page: int | None, quote: str, method: str) -> None:
        if value in (None, "", 0):
            return
        if self.facts[key].kind == "money" and isinstance(value, (int, float)) and value < 100:
            return  # a percentage or clause number caught by a loose pattern, never a tender amount
        lossy = self.text_layer and not self.ocr and isinstance(value, str) and bool(_DEV.search(value))
        if key == "work_name" and isinstance(value, str) and _DEV.search(value) and not lossy:
            key = "work_name_mr"  # the "(In English)" field sometimes holds the Marathi name
        self.facts[key].sources.append(FactSource(doc_id, doc_name, page, quote.strip()[:240], value,
                                                  method + (" (OCR)" if self.ocr else ""), self.ocr, lossy,
                                                  self.amending))

    # ----------------------------------------------------------------------------------
    def scan_page(self, raw: str, doc_id: str, doc_name: str, page: int) -> None:
        add = lambda k, v, q, m: self.add(k, v, doc_id, doc_name, page, q, m)  # noqa: E731

        for ref in dict.fromkeys(_REF.findall(raw)):
            add("ref_no", ref.replace(" ", ""), _line_with(raw, ref), "reference")

        # Tender Notice box (portal generated): contract type / completion / rainy season, then four amounts.
        m = re.search(r"Type of Contract:.*?Rainy Season:.*?\n\s*(B-?\d)\s+(\d+)\s+(Included|Excluded)", raw, re.S | re.I)
        if m:
            q = m.group(0).split("\n")[-1]
            add("contract_type", m.group(1).upper().replace("-", ""), q, "Tender Notice")
            add("completion_months", int(m.group(2)), q, "Tender Notice")
            add("rainy_season", m.group(3).capitalize(), q, "Tender Notice")
        if "Cost of Tender" in raw and "Security Deposit" in raw:
            for line in raw.split("\n"):
                amounts = re.findall(r"Rs\.?\s*" + _NUM + r"\s*/-", line)
                if len(amounts) == 4:
                    for key, v in zip(("cost_of_tender_doc", "estimated_cost", "emd", "security_deposit"), amounts):
                        add(key, _num(v), line, "Tender Notice")
                    break

        # Tender Work Input Form (key-value).
        if "Tender Work Input Form" in raw or "Form of Contract" in raw:
            for key, pattern in (
                ("form_of_contract", r"Form of Contract:\s*([A-Z][A-Z ]+?)\s*$"),
                ("tender_category", r"Tender Category:\s*([A-Z][A-Z ]+?)\s*$"),
                ("pre_bid_meeting", r"Pre Bid Meeting:\s*(Yes|No)\b"),
            ):
                mm = re.search(pattern, raw, re.M)
                if mm:
                    add(key, mm.group(1).strip().title(), mm.group(0), "Work Input Form")
            mm = re.search(r"Completion Period:\s*(\d+)", raw)
            if mm:
                add("completion_months", int(mm.group(1)), mm.group(0), "Work Input Form")
            mm = re.search(r"EMD Fee:\s*" + _NUM, raw)
            if mm:
                add("emd", _num(mm.group(1)), mm.group(0), "Work Input Form")
            mm = re.search(r"Estimated cost Without G\.?S\.?T\.?(.{0,160}?)(?<![\d/])(\d{4,}(?:\.\d+)?)\b", raw, re.S | re.I)
            if mm:
                add("estimated_cost", _num(mm.group(2)), _line_with(raw, mm.group(2)), "Work Input Form")
            mm = re.search(r"Work Name\s*\(In English\):\s*\n?\s*\d?\s*(.+)", raw)
            if mm and re.search(r"[A-Za-z]{4}", mm.group(1)):
                add("work_name", mm.group(1).strip().rstrip("."), mm.group(0), "Work Input Form")

        # Department heading on the invitation page.
        mm = re.search(r"(?:CHIEF ENGINEERING|SUPERINTENDENT ENGINEER)\s+([A-Z&]+)\s+DEPARTMENT", raw)
        if mm:
            add("department", mm.group(1).title(), mm.group(0), "Invitation")

        # B-1 form row: 9 "n/-" amounts then time limit, DLP and cost of tender document. OCR gives the
        # same row as a table ("| 5900000/- | 5000000/- | … | 12 | 12 | 5000"), on the page after "B-1 Form".
        mm = re.search(r"((?:\d+/-\s*\|?\s*){9})(\d+)\s*\|?\s*(\d+)\s*\|?\s*(\d+)", raw)
        if mm and ("B-1" in raw or (re.search(r"earnest\s*money", raw, re.I) and re.search(r"security\s*deposit", raw, re.I))):
            amounts = [_num(a) for a in re.findall(r"(\d+)/-", mm.group(1))]
            q = mm.group(0)
            add("total_estimated_cost", amounts[0], q, "B-1 form")
            add("estimated_cost", amounts[1], q, "B-1 form")
            add("emd", amounts[7], q, "B-1 form")
            add("security_deposit", amounts[8], q, "B-1 form")
            add("completion_months", int(mm.group(2)), q, "B-1 form")
            add("dlp_months", int(mm.group(3)), q, "B-1 form")
            add("cost_of_tender_doc", float(mm.group(4)), q, "B-1 form")

        # Conditions volume tender table: "... 5900000 5000000 900000 50000 250000 12 24 5000"
        # (total, put to tender, GST, EMD, SD, months, DLP, doc cost). Text order varies by extractor,
        # so the eight numbers are matched on their own and sanity-checked instead of anchored.
        if re.search(r"Total\s+Estimated", raw, re.I) and re.search(r"Earnest\s+money", raw, re.I):
            for mm in re.finditer(r"(?<![\d,])(\d{5,})\s+(\d{5,})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d{1,3})\s+(\d{1,3})\s+(\d{3,5})(?!\d)", raw):
                v = [float(x) for x in mm.groups()]
                if not (v[0] >= v[1] and 0.004 * v[1] <= v[3] <= 0.02 * v[1] and 0.02 * v[1] <= v[4] <= 0.08 * v[1]):
                    continue
                q = mm.group(0)
                add("total_estimated_cost", v[0], q, "Conditions tender table")
                add("estimated_cost", v[1], q, "Conditions tender table")
                add("emd", v[3], q, "Conditions tender table")
                add("security_deposit", v[4], q, "Conditions tender table")
                add("completion_months", int(v[5]), q, "Conditions tender table")
                add("dlp_months", int(v[6]), q, "Conditions tender table")
                add("cost_of_tender_doc", v[7], q, "Conditions tender table")
                break

        # Road / conditions volumes state the key numbers in prose, often with words run together by
        # the text layer ("EARNESTMONEYDEPOSIT : a) An earnest money depositofRs. 123450/-",
        # "time limit for completion ofwork is 9 calendarmonths").
        mm = re.search(r"earnest\s*money\s*deposit\s*of\s*Rs\.?\s*([\d,]{4,})\s*/-", raw, re.I)
        if mm:
            add("emd", _num(mm.group(1)), _line_with(raw, mm.group(1)), "Conditions clause")
        mm = re.search(r"ESTIMATED\s*COST\s*PUT\s*TO\s*TENDER\s*:?\s*Rs\.?\s*([\d,]{5,})\s*/-", raw, re.I)
        if mm:
            add("estimated_cost", _num(mm.group(1)), _line_with(raw, mm.group(1)), "Conditions clause")
        mm = re.search(r"time\s*limit\s*for\s*completion\s*of\s*(?:the\s*)?work\s*(?:is|shall\s*be)\s*(\d{1,2})\s*"
                       r"(?:\(\s*\w+\s*\)\s*)?calendar\s*months?", raw, re.I)
        if mm:
            add("completion_months", int(mm.group(1)), mm.group(0), "Conditions clause")

        # Tender Abstract header: "Work Name : <name of work> ... (For Technical Sanctioned)"
        mm = re.search(r"^\s*Work Name\s*:\s*(.+?)(?:\(For Technical Sanction(?:ed)?\))?\s*$", raw, re.M)
        if mm and re.search(r"[A-Za-z]{4}", mm.group(1)):
            add("work_name", mm.group(1).strip().rstrip("."), mm.group(0), "Tender Abstract")
        # Office-level tender numbers when there is no PMC/... reference: "टेंडर क्रमांक. -155-2026"
        mm = re.search(r"(?:टेंडर|टेडर|निविदा)\s*क्रमांक\s*\.?\s*[:\-]?\s*-?\s*(\d{1,4})\s*[-/]\s*(20\d\d)", raw)
        if mm:
            add("ref_no_local", f"{mm.group(1)}/{mm.group(2)}", mm.group(0), "Goshwara")
        # RFP cover: "REQUEST FOR PROPOSAL for <title> DRAFT Pune Municipal Corporation"
        mm = re.search(r"REQUEST FOR PROPOSAL\s+for\s+(.+?)\s+(?:DRAFT|Pune Municipal|Issued By)", raw, re.S | re.I)
        if mm and page <= 2:
            add("work_name", re.sub(r"\s+", " ", mm.group(1)).strip(), mm.group(0)[:200], "RFP cover")

        # Tender Abstract (BOQ summary) total.
        mm = re.search(r"Total For Sub Work:.{0,160}?(\d{4,}(?:\.\d+)?)", raw, re.S)
        if mm:
            add("estimated_cost", _num(mm.group(1)), _line_with(raw, "Total For Sub Work"), "Tender Abstract total")

    # ----------------------------------------------------------------------------------
    def scan_tables(self, tables: list[list[list[str]]], doc_id: str, doc_name: str, page: int) -> None:
        """Portal forms as tables — from pdfplumber or from Chandra's markdown. Two shapes:
        a label row over a value row (Tender Notice box) and label/value rows (Work Input Form,
        Data Sheet)."""
        for rows in tables:
            for upper, lower in zip(rows, rows[1:]):
                if len(upper) == len(lower) and sum(1 for c in upper if _label(c)) >= 2:
                    for label_cell, value_cell in zip(upper, lower):
                        self._typed(_label(label_cell), value_cell, f"{label_cell} {value_cell}", doc_id, doc_name, page, "form table")
            for row in rows:
                cells = [c for c in row if c]
                for j, cell in enumerate(cells):
                    label = _label(cell)
                    if not label:
                        continue
                    inline = cell.split(":", 1)[1].strip() if ":" in cell else ""
                    value = inline or (cells[j + 1] if j + 1 < len(cells) else "")
                    if value and not _label(value) and not value.rstrip().endswith(":"):
                        self._typed(label, value, " | ".join(cells), doc_id, doc_name, page, "form table")

    def _typed(self, label, value: str, quote: str, doc_id: str, doc_name: str, page: int, method: str) -> None:
        if not label or not value:
            return
        key, kind = label
        if any(s.page == page and s.document_id == doc_id and s.method == method and s.quote == quote[:240]
               for s in self.facts[key].sources):
            return
        v: object = None
        if kind == "money":
            if "%" in value:
                return  # "Security Deposit (5%)" is a rate, not an amount
            m = re.search(r"(\d[\d,]*(?:\.\d+)?)", value.replace(" ", ""))
            v = _num(m.group(1)) if m else None
            if v is not None and re.search(r"lacs?|lakhs?", value, re.I):
                v *= 100_000
            if v is not None and v < 100:
                return
        elif kind == "int":
            m = re.search(r"\d+", value)
            v = int(m.group(0)) if m else None
        elif kind == "ref":
            m = _REF.search(value)
            v = m.group(0) if m else None
        elif kind == "title":
            v = value.strip().title()
            if key == "pre_bid_meeting" and v not in ("Yes", "No"):
                return
        else:
            v = value.strip().rstrip(".")
        self.add(key, v, doc_id, doc_name, page, quote, method)

    def scan_marathi(self, text: str, doc_id: str, doc_name: str, page: int) -> None:
        """Tender Goshwara (summary) in proper Unicode Marathi — only from OCR, never from the lossy layer.
        Only on the Goshwara page itself: Marathi conditions pages reuse the same words for other amounts."""
        if "गोषवारा" not in text:
            return
        t = text.translate(_DEV_DIGITS).replace("**", "")
        for key, pattern in (
            ("estimated_cost", r"मागविण्यात\s*आलेली\s*रक्कम\s*(?:रु\.?|रुपये|Rs\.?)?\s*:?\s*([\d,]+(?:\.\d+)?)"),
            ("emd", r"बया[णन]ा\s*रक्कम.{0,60}?(?:रुपये|रु\.?)\s*:?\s*(?:Rs\.?\s*)?([\d,]+)"),
            ("security_deposit", r"अनामत\s*रक्कम.{0,60}?(?:रुपये|रु\.?)\s*:?\s*(?:Rs\.?\s*)?([\d,]+)"),
        ):
            m = re.search(pattern, t)
            if m:
                self.add(key, _num(m.group(1)), doc_id, doc_name, page, _line_with(text, m.group(0)[:20]), "Goshwara (Marathi)")
        m = re.search(r"कामाचे\s*नाव\s*[:\-]\s*(.+)", t)
        if m and len(m.group(1).strip()) > 8:
            self.add("work_name_mr", m.group(1).strip().rstrip("."), doc_id, doc_name, page, m.group(0)[:200], "Goshwara (Marathi)")
        m = re.search(r"पूर्ण\s*करण्या\s*साठी\s*मुदत\s*(\d+)", t)
        if m:
            self.add("completion_months", int(m.group(1)), doc_id, doc_name, page, _line_with(text, "मुदत"), "Goshwara (Marathi)")
        m = re.search(r"(?:लायबिलीटी|लायबिलिटी|लायबलीटी|लायबिलीटी)\s*(?:पिरियड|पिरीयड|पिरेड|पिरएड|पिरिअड)\s*(\d+)", t)
        if m:
            self.add("dlp_months", int(m.group(1)), doc_id, doc_name, page, _line_with(text, m.group(0)[:12]), "Goshwara (Marathi)")

    def scan_boq(self, summary, doc_id: str, doc_name: str) -> None:
        if summary.estimated_total:
            self.add("estimated_cost", summary.estimated_total, doc_id, doc_name, None,
                     f"BOQ 'Total in Figures': {summary.estimated_total:.0f}", "BOQ spreadsheet")
        if summary.contract_no and _REF.search(summary.contract_no):  # some are typed in Marathi (पी एम सी/…)
            self.add("ref_no", _REF.search(summary.contract_no).group(0), doc_id, doc_name, None, f"Contract No: {summary.contract_no}", "BOQ spreadsheet")
        elif summary.contract_no:
            m = re.search(r"(\d{1,4})\s*[-/ ]\s*-?\s*(20\d\d)", summary.contract_no.translate(_DEV_DIGITS))
            if m:
                self.add("ref_no_local", f"{m.group(1)}/{m.group(2)}", doc_id, doc_name, None,
                         f"Contract No: {summary.contract_no}", "BOQ spreadsheet")
        if summary.work_name:
            self.add("work_name_mr" if _DEV.search(summary.work_name) else "work_name", summary.work_name.strip().rstrip("."),
                     doc_id, doc_name, None, f"Name of Work: {summary.work_name}", "BOQ spreadsheet")
        if summary.boq_type:
            self.add("form_of_contract", summary.boq_type.title(), doc_id, doc_name, None, f"{summary.boq_type} BoQ", "BOQ spreadsheet")

    # ----------------------------------------------------------------------------------
    def scan_corrigendum(self, raw: str, tables: list[list[list[str]]], doc_id: str, doc_name: str, page: int) -> None:
        """Revised values in a corrigendum: a table with an 'Existing | Revised' pair of columns, or
        prose ('The estimated cost … shall be read as Rs. 31,25,000/-')."""
        for rows in tables:
            for h, head in enumerate(rows[:3]):
                revised = next((j for j, c in enumerate(head) if re.search(r"revised|amended|corrected|read\s*as|modified|new", c, re.I)), None)
                if revised is None or not any(re.search(r"existing|original|earlier|instead|published|old", c, re.I) for c in head):
                    continue
                for row in rows[h + 1:]:
                    label = next((_label(c) for c in row if _label(c)), None) or _prose_label(" ".join(row))
                    if label and revised < len(row) and row[revised]:
                        self._typed(label, row[revised], " | ".join(c for c in row if c), doc_id, doc_name, page, "Corrigendum table")
                break
        for m in re.finditer(r"(estimated\s*cost(?:\s*put\s*to\s*tender)?|earnest\s*money(?:\s*deposit)?|\bEMD\b|security\s*deposit"
                             r"|completion\s*period|time\s*limit(?:\s*for\s*completion)?)[^.\n]{0,160}?"
                             r"(?:revised|read\s*as|modified|changed|amended)\s*(?:to|as)?\s*:?\s*(?:Rs\.?|₹|INR)?\s*([\d,]+(?:\.\d+)?)"
                             r"\s*(lakhs?|lacs?|months?)?", raw, re.I):
            label = _prose_label(m.group(1))
            if label:
                self._typed(label, m.group(2) + (f" {m.group(3)}" if m.group(3) else ""), m.group(0), doc_id, doc_name, page,
                            "Corrigendum")

    def reconcile(self) -> dict[str, Fact]:
        for fact in self.facts.values():
            if not fact.sources:
                continue
            # A corrigendum supersedes the tender: its value stands, and the original is noted.
            amended = [s for s in fact.sources if s.amended and not s.lossy]
            if amended and len({str(s.value) for s in amended}) == 1:
                fact.value, fact.status = amended[0].value, "amended"
                before = sorted({str(format_inr(float(s.value)) if fact.kind == "money" else s.value)
                                 for s in fact.sources if not s.amended and str(s.value) != str(amended[0].value)})
                fact.note = (f"Changed by corrigendum ({amended[0].doc_name}"
                             + (f" p.{amended[0].page}" if amended[0].page else "") + ")"
                             + (f"; the tender documents say {', '.join(before)}." if before else "."))
                continue
            clean = [s for s in fact.sources if not s.lossy]
            if not clean:
                fact.status = "lossy"
                fact.note = "Only available as a broken Devanagari text layer; not shown. Read the cited page."
                continue
            values = [s.value for s in clean]
            if fact.kind == "money":
                distinct = sorted({round(float(v)) for v in values})
                agree = distinct[-1] - distinct[0] <= 2
            else:
                norm = [re.sub(r"\s+", " ", str(v)).strip().rstrip(".").upper() for v in values]
                longest = max(norm, key=len)
                # A shorter source is often just the same text cut at a line break.
                agree = all(longest.startswith(n) for n in norm)
                if agree:
                    values = [values[norm.index(longest)]]
            if agree:
                fact.value = values[0]
                fact.status = "consistent" if len(fact.sources) > 1 else "single_source"
                continue
            # Typed text and spreadsheets outrank OCR (which includes handwriting): when every
            # non-OCR source agrees, they decide, and the OCR outliers are reported, not trusted.
            typed = [s for s in fact.sources if not s.ocr and not s.lossy]
            if typed and fact.kind == "money" and max(float(s.value) for s in typed) - min(float(s.value) for s in typed) <= 2:
                fact.value, fact.status = typed[0].value, "consistent"
                outliers = {float(s.value) for s in fact.sources if s.ocr and abs(float(s.value) - float(typed[0].value)) > 2}
                if outliers:
                    fact.note = ("OCR read " + ", ".join(format_inr(v) for v in sorted(outliers))
                                 + f" on a scanned page; {len(typed)} typed source(s) agree on {format_inr(float(typed[0].value))}.")
            elif typed and fact.kind != "money" and len({str(s.value).upper() for s in typed}) == 1 and fact.key != "work_name_mr":
                fact.value, fact.status = typed[0].value, "consistent"
            else:
                fact.status = "conflict"
                fact.value = None

        # PMC collects 4% as security deposit and converts the 1% EMD into the remaining 1%, so a
        # 4% figure and a 5% figure for the same tender are consistent, not a contradiction.
        sd, emd = self.facts["security_deposit"], self.facts["emd"]
        if sd.sources and emd.value:
            vals = sorted({round(float(s.value)) for s in sd.sources})
            if len(vals) == 2 and abs(vals[0] + float(emd.value) - vals[1]) <= 2:
                sd.status, sd.value = "consistent", vals[1]
                sd.note = (f"{format_inr(vals[0])} is the balance collected; with the {format_inr(float(emd.value))} EMD "
                           f"converted, the total security deposit is {format_inr(vals[1])}.")

        # Marathi names differ in small OCR/typing ways across sources: prefer the spreadsheet's typed text.
        mr = self.facts["work_name_mr"]
        if mr.status == "conflict":
            typed = [s for s in mr.sources if not s.ocr and not s.lossy]
            pick = typed[0] if typed else max(mr.sources, key=lambda s: len(str(s.value)))
            mr.value, mr.status = pick.value, "consistent"
            mr.note = "Sources spell the Marathi name slightly differently; the typed spreadsheet text is shown."

        # Reference numbers: prefer the most specific one (portal forms sometimes print a stub).
        ref = self.facts["ref_no"]
        if ref.status == "conflict":
            full = [s.value for s in ref.sources if re.search(r"/20\d\d/\d+$", str(s.value))]
            if full and len(set(full)) == 1:
                ref.value, ref.status = full[0], "consistent"
        return self.facts
