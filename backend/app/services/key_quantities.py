"""Executed quantities on a bidder's BOQ / completion certificates, matched to the tender's key items.

The tender asks for "aggregate annual production of the following items in any one financial year"
(M-15 concrete ≥ 14 cum, interlocking blocks removed and refixed ≥ 600 sqm, ...). Authorities certify
what a contractor executed item by item ("प्रत्यक्ष केलेल्या कामाचे परिमाण", "B O Q CERTIFICATE"),
in Marathi or English, one work per certificate. This module reads those rows and says which of the
tender's items each row is — by the item's distinguishing features (concrete grade, block thickness,
remove-and-refix), not by string equality, and never by any one bidder's wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.adapters.pdf_parser import ParsedDocument
from app.services.values import ascii_digits

# --------------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------------

_UNITS = [
    ("cum", re.compile(r"^(?:cu\.?\s*m\.?|cum|cmt|m3|m³|cubic\s*met(?:re|er)s?|घ\.?\s*मी\.?|घमी|क्यु\.?\s*मी)$", re.I)),
    ("sqm", re.compile(r"^(?:sq\.?\s*m\.?|sqm|smt|m2|m²|square\s*met(?:re|er)s?|चौ\.?\s*मी\.?|चौमी|स्क्वे)$", re.I)),
    ("rmt", re.compile(r"^(?:r\.?\s*m\.?t?|rmt|running\s*met(?:re|er)s?|र\.?\s*मी\.?|रमी|मीटर)$", re.I)),
    ("nos", re.compile(r"^(?:nos?\.?|numbers?|per\s*no\.?|each|नग|संख्या)$", re.I)),
    ("MT", re.compile(r"^(?:m\.?\s*t\.?|mt|tonnes?|metric\s*tonnes?|मे\.?\s*टन)$", re.I)),
    ("kg", re.compile(r"^(?:kg|kgs|किलो)$", re.I)),
]


def unit_of(text: str) -> str | None:
    t = (text or "").strip().strip(".").strip()
    for name, pat in _UNITS:
        if pat.match(t):
            return name
    return None


# --------------------------------------------------------------------------------------------
# Item features
# --------------------------------------------------------------------------------------------

_GRADE = re.compile(r"(?:\bM|एम)\s*-?\s*(\d{2})\b", re.I)
_THICK = re.compile(r"(\d{2,3})\s*(?:mm|मि\.?\s*मी\.?|मिमी)", re.I)
_FEATURES = {
    "concrete": re.compile(r"concre|concerte|conc\.|\bPCC\b|\bPQC\b|कॉ[ंन]?क्रीट|काँक्रीट|कॉक्रीट|कॉंक्रिट|सिमेंट\s*काँक्रीट", re.I),
    "block": re.compile(r"block|paver|ब्लॉक|पेव्हर|पेव्हिंग", re.I),
    "interlock": re.compile(r"interlock|इंटरलॉक|इन्टरलॉक", re.I),
    "remove": re.compile(r"remov|dismantl|taking\s*out|काढून|काढणे", re.I),
    "refix": re.compile(r"re-?fix|re-?lay|refitt|re-?laying|परत\s*बसव|बसविणे|बसवणे|बसविने", re.I),
    "shot": re.compile(r"shot|शॉट", re.I),
    "blast": re.compile(r"blast|ब्लास्ट", re.I),
    "excavation": re.compile(r"excavat|खोदाई|खोदकाम", re.I),
    "bituminous": re.compile(r"bitum|\bBC\b|\bDBM\b|\bSDBC\b|डांबर", re.I),
    "gsb": re.compile(r"\bGSB\b|granular\s*sub", re.I),
    "wmm": re.compile(r"\bWMM\b|wet\s*mix", re.I),
    "pipe": re.compile(r"pipe|पाईप|पाइप", re.I),
    "manhole": re.compile(r"manhole|मॅनहोल|मनहोल|चेंबर|chamber", re.I),
    "kerb": re.compile(r"kerb|curb|कर्ब", re.I),
    "steel": re.compile(r"steel|स्टील|reinforcement", re.I),
    "paint": re.compile(r"paint|पेंट|रंग", re.I),
}


def features(text: str) -> dict:
    t = ascii_digits(text or "")
    tags = {name for name, pat in _FEATURES.items() if pat.search(t)}
    feats: dict = {"tags": tags}
    g = _GRADE.search(t)
    if g and g.group(1) in {"10", "15", "20", "25", "30", "35", "40", "45", "50"}:
        feats["grade"] = g.group(1)
    th = _THICK.search(t)
    if th:
        feats["thickness"] = th.group(1)
    return feats


_GENERIC = {"block", "concrete"}  # the noun; the grade, thickness or finish is what tells items apart


def matches(required: str, row_item: str) -> bool:
    """Every distinguishing feature of the tender's item is present in the row's description.
    "60 mm Shot Blasted Block" is matched by "60 mm shot blasted" (the certificate drops the noun),
    and "M-15 Concrete" by "एम १५ कॉंक्रीट" or "PCC (M15 for steps)"."""
    need, have = features(required), features(row_item)
    if not need["tags"] and "grade" not in need:
        return False
    if "grade" in need and have.get("grade") != need["grade"]:
        return False
    if "thickness" in need and have.get("thickness") != need["thickness"]:
        return False
    specific = need["tags"] - _GENERIC
    if specific or "grade" in need or "thickness" in need:
        return specific <= have["tags"] and bool(need["tags"] & have["tags"] or "grade" in need)
    return need["tags"] <= have["tags"]


# --------------------------------------------------------------------------------------------
# Executed rows
# --------------------------------------------------------------------------------------------

@dataclass
class ExecutedRow:
    item: str
    qty: float
    unit: str | None
    page: int
    year: int | None  # financial-year start when the certificate states one
    year_note: str | None
    work: str | None  # the certificate's name of work, to tell works apart
    quote: str
    tender_year: int | None = None  # the year the work was tendered: completion was that year or later


_ITEM_HEAD = re.compile(r"item|particular|description|तपशील|बाबी?|अवयव|आयटम|कामाचा\s*प्रकार", re.I)
_QTY_HEAD = re.compile(r"qty|quantit|परिमाण|क्व[ाॉ]ं?टिटी|मापे|executed|अद्यावत|up\s*to\s*date|(?:19|20)\d{2}\s*[-–]\s*\d{2,4}", re.I)
# What was done, as against what the tender asked for: a running abstract carries both columns.
_DONE_HEAD = re.compile(r"executed|actual|up\s*to\s*date|work\s*done|प्रत्यक्ष|अद्यावत|केलेले", re.I)
_TENDER_HEAD = re.compile(r"tender\s*qty|tender\s*quantit|estimated|निविदा|मंजूर|as\s*per\s*tender", re.I)
_TENDER_NO_CELL = re.compile(r"(?<![\d/])\d{1,4}\s*(?:Ex\.?)?\s*[-–/.]\s*((?:19|20)\d{2})(?!\d)|/((?:19|20)\d{2})/\d+", re.I)
_UNIT_HEAD = re.compile(r"^\s*(?:unit|एकक|युनिट)\s*$", re.I)
_FY_HEAD = re.compile(r"((?:19|20)\d{2})\s*[-–]\s*(?:(?:19|20)\d{2}|\d{2})")
_WORK = re.compile(r"(?:कामाचे\s*ना[ंव]?व|name\s*of\s*work|विषय)\s*[:|\-–]*\s*(?P<w>[^\n|]{8,200})", re.I)
_TENDER_NO = re.compile(r"(?:टेंडर|निविदा|tender)\s*(?:क्र|नं|क्रमांक|no)\.?\s*[:|\-–]*\s*[^\n|]{0,40}?(?P<y>(?:19|20)\d{2})", re.I)
_QTY_CELL = re.compile(r"^\s*(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[^\d\s][^\d]{0,14})?\s*$")


def executed_rows(doc: ParsedDocument) -> list[ExecutedRow]:
    types = getattr(doc, "page_types", None) or [None] * len(doc.pages)
    out: list[ExecutedRow] = []
    work, year, year_note, tender_year = None, None, None, None
    for p in doc.pages:
        kind = types[p.number - 1] if p.number - 1 < len(types) else None
        text = ascii_digits(p.text or "")
        if kind not in ("boq_certificate", "completion_certificate") or not p.quality.quotable:
            work, year, year_note, tender_year = None, None, None, None  # continuation pages follow directly
            continue
        wm = _WORK.search(text)
        if wm:
            work = re.sub(r"\s+", " ", wm.group("w")).strip()
            year, year_note, tender_year = None, None, None
        tm = _TENDER_NO.search(text)
        if tm:
            tender_year = int(tm.group("y"))
        fy = re.search(r"(?:F\.?\s*Y\.?|सन)\s*((?:19|20)\d{2})\s*[-–]\s*\d{2,4}", text)
        if fy:
            year, year_note = int(fy.group(1)), fy.group(0)
        for rows in p.tables:
            head_i, item_c, qty_c, unit_c = None, None, None, None
            for i, row in enumerate(rows[:3]):
                cells = [(c or "").strip() for c in row]
                item_cols = [j for j, c in enumerate(cells) if _ITEM_HEAD.search(c) and len(c) < 40]
                # "BOQ Item No. | Item Name": the number column is not the item.
                named = [j for j in item_cols if not re.search(r"\bno\.?\s*$|number|\bsr\b|क्र", cells[j], re.I)]
                ic = (named or item_cols or [None])[0]
                heads = [(rows[i + 1][j] if i + 1 < len(rows) and j < len(rows[i + 1]) else "") for j in range(len(cells))]
                qty_cols = [j for j, c in enumerate(cells) if _QTY_HEAD.search(ascii_digits(c + " " + (heads[j] or "")))
                            and len(c) < 60 and j != ic and not _TENDER_HEAD.search(c)]
                done_cols = [j for j in qty_cols if _DONE_HEAD.search(cells[j] + " " + (heads[j] or ""))]
                qc = (done_cols or qty_cols or [None])[-1 if done_cols else 0]
                if ic is not None and qc is not None:
                    head_i, item_c, qty_c = i, ic, qc
                    unit_c = next((j for j, c in enumerate(cells) if _UNIT_HEAD.match(c)), None)
                    hy = _FY_HEAD.search(ascii_digits(cells[qc]))
                    if hy:
                        year, year_note = int(hy.group(1)), cells[qc]
                    break
            body = rows[head_i + 1:] if head_i is not None else rows
            for row in body:
                cells = [(c or "").strip() for c in row]
                if head_i is None:
                    # A continuation page: "18 | I.C 60x45 | 5.000 | Nos" with no header of its own.
                    texts = [j for j, c in enumerate(cells) if re.search(r"[A-Za-zऀ-ॿ]{3}", c) and not unit_of(c)]
                    nums = [j for j, c in enumerate(cells) if _QTY_CELL.match(ascii_digits(c))]
                    if not texts or not nums or work is None:
                        continue
                    ic, qc = texts[0], next((j for j in nums if j > texts[0]), None)
                    if qc is None:
                        continue
                    uc = next((j for j, c in enumerate(cells) if j > qc and unit_of(c)), None)
                else:
                    ic, qc, uc = item_c, qty_c, unit_c
                if ic >= len(cells) or qc >= len(cells):
                    continue
                m = _QTY_CELL.match(ascii_digits(cells[qc]))
                if not m or not re.search(r"[A-Za-zऀ-ॿ]{2}", cells[ic]):
                    continue
                try:
                    qty = float(m.group("n").replace(",", ""))
                except ValueError:
                    continue
                unit = unit_of(cells[uc]) if uc is not None and uc < len(cells) else None
                if unit is None and m.group("u"):
                    unit = unit_of(m.group("u"))
                if qty <= 0:
                    continue
                out.append(ExecutedRow(item=cells[ic], qty=qty, unit=unit, page=p.number, year=year,
                                       year_note=year_note, work=work, quote=" | ".join(c for c in cells if c),
                                       tender_year=tender_year))
    out += _horizontal_rows(doc, types)
    # The same certificate is often enclosed twice, sometimes without its heading page the second
    # time: the same item at the same quantity to the last decimal is the same work.
    seen, unique = set(), []
    for r in out:
        key = (re.sub(r"\W+", "", r.item.lower())[:40], r.qty, r.unit)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


_QTY_UNIT_CELL = re.compile(r"^\s*(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[^\d\s][^\d]{0,10})?\s*$")


def _horizontal_rows(doc: ParsedDocument, types: list) -> list[ExecutedRow]:
    """"माती खोदाई | आर सी.सी पाईप पुरवणे | M-30 | GSB" over "१६००.०० घ.मी | NP-3 | १३९.४० घ.मी | ४२८ घ.मी":
    item names across one row, the quantities executed under them in the next."""
    out: list[ExecutedRow] = []
    for p in doc.pages:
        kind = types[p.number - 1] if p.number - 1 < len(types) else None
        if kind not in ("boq_certificate", "completion_certificate") or not p.quality.quotable:
            continue
        text = ascii_digits(p.text or "")
        wm = _WORK.search(text)
        tn = _TENDER_NO_CELL.search(ascii_digits(" ".join(c or "" for rows in p.tables for r in rows for c in r))) \
            or _TENDER_NO.search(text)
        tender_year = int(next(g for g in tn.groups() if g)) if tn else None
        fy = re.search(r"(?:F\.?\s*Y\.?|सन)\s*((?:19|20)\d{2})\s*[-–]\s*\d{2,4}", text)
        for rows in p.tables:
            for upper, lower in zip(rows, rows[1:]):
                names = [(c or "").strip() for c in upper]
                values = [ascii_digits((c or "").strip()) for c in lower]
                if len(names) < 2 or len(names) != len(values):
                    continue
                if not all(re.search(r"[A-Za-z\u0900-\u097F]", n) and not _QTY_UNIT_CELL.match(ascii_digits(n)) for n in names if n):
                    continue
                parsed = [_QTY_UNIT_CELL.match(v) for v in values]
                if sum(1 for m in parsed if m) < max(1, len(values) // 2):
                    continue  # not a row of quantities
                if not any(m and unit_of(m.group("u") or "") for m in parsed):
                    continue  # a table's heading over its data row: quantities carry units, amounts do not
                for name, m, raw in zip(names, parsed, lower):
                    if not name or not m:
                        continue
                    try:
                        qty = float(m.group("n").replace(",", ""))
                    except ValueError:
                        continue
                    unit = unit_of(m.group("u") or "")
                    out.append(ExecutedRow(item=name, qty=qty, unit=unit, page=p.number,
                                           year=int(fy.group(1)) if fy else None, year_note=fy.group(0) if fy else None,
                                           work=re.sub(r"\s+", " ", wm.group("w")).strip() if wm else None,
                                           quote=f"{name}: {raw}", tender_year=tender_year))
    return out


def match_required(row: ExecutedRow, required: list[dict]) -> list[dict]:
    return [req for req in required
            if matches(req["item"], row.item) and (not req.get("unit") or not row.unit or row.unit == req["unit"])]
