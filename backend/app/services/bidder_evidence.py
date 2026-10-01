"""Finders for the PMC works criteria a bid is checked against: EMD, contractor registration, bid
capacity, sole bidder (no joint venture) and executed key quantities.

Each reads the bidder's own documents only. Patterns name kinds of document and the ways
authorities print them (a PWD registration certificate, a Zilla Parishad नोंदणी प्रमाणपत्र, the
e-tendering portal's payment receipt) — never any one bidder's wording or figures.
"""

from __future__ import annotations

import re
from datetime import date

from app.adapters.pdf_parser import ParsedDocument, Sentence
from app.services.bidder_extraction import FINDERS, Claim, CriterionSpec, _claim
from app.services.values import ascii_digits, format_inr, format_inr_short

_DMY = r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})"
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}


def _date(d: str, m: str, y: str) -> date | None:
    try:
        year = int(y)
        year += 2000 if year < 100 else 0
        return date(year, int(m), int(d))
    except ValueError:
        return None


def _types(doc: ParsedDocument) -> list:
    return getattr(doc, "page_types", None) or [None] * len(doc.pages)


def _pages_of(doc: ParsedDocument, *kinds: str):
    types = _types(doc)
    for p in doc.pages:
        if p.quality.quotable and (types[p.number - 1] if p.number - 1 < len(types) else None) in kinds:
            yield p


def _norm_ref(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ascii_digits(text or "").upper())


def _line(text: str, needle_start: int) -> str:
    start = text.rfind("\n", 0, needle_start) + 1
    end = text.find("\n", needle_start)
    return text[start: end if end != -1 else len(text)].strip()


# --------------------------------------------------------------------------------------------
# EMD
# --------------------------------------------------------------------------------------------

_EMD_AMOUNT = re.compile(r"EMD\s*(?:Fee|Amount)\s*(?:in\s*)?(?:₹|Rs\.?|INR)?\s*[:|]?\s*(?:₹|Rs\.?)?\s*(?P<n>\d[\d,]*(?:\.\d+)?)", re.I)
_EMD_OTHER = re.compile(r"(?:earnest\s*money(?:\s*deposit)?|इसारा\s*रक्कम|बयाणा\s*रक्कम)[^\n\d]{0,40}?(?:₹|Rs\.?|रु\.?)?\s*(?P<n>\d[\d,]{3,}(?:\.\d+)?)", re.I)
_TENDER_REF = re.compile(r"Tender\s*Ref(?:erence)?\.?\s*No\.?\s*[:|]?\s*(?P<ref>[A-Z0-9][^\n|]{4,80}?)\s*(?=Bank|eProc|Tender\s|$|\n|\|)", re.I)
_TENDER_ID = re.compile(r"Tender\s*ID\s*[:|]?\s*(?P<id>\d{4}[\\_]*[A-Z]+[\\_]*\d+[\\_]*\d*)", re.I)
_PAY_STATUS = re.compile(r"Bank\s*Status\s*[:|]?\s*(?P<s>\w+)|transaction\s+has\s+been\s+(?P<s2>completed\s+successfully|failed)", re.I)
_PAID_AT = re.compile(r"(?P<d>\d{2})-(?P<m>\d{2})-(?P<y>\d{4}),?\s+\d{1,2}:\d{2}|(?P<d2>\d{2})-(?P<mon>[A-Za-z]{3})-(?P<y2>\d{4})\s+\d{1,2}:\d{2}")


def find_emd(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    """The portal's payment receipt (or a bank challan): amount, which tender it paid for, status."""
    out: list[Claim] = []
    want_ref = _norm_ref(spec.tender.get("ref_no") or "")
    for p in doc.pages:
        text = ascii_digits(p.text or "")
        if not p.quality.quotable or not text.strip():
            continue
        m = _EMD_AMOUNT.search(text) or (_EMD_OTHER.search(text) if _types(doc)[p.number - 1] == "emd_receipt" else None)
        if not m:
            continue
        try:
            amount = float(m.group("n").replace(",", ""))
        except ValueError:
            continue
        if amount < 100:
            continue
        ref_m = _TENDER_REF.search(text)
        ref = ref_m.group("ref").strip() if ref_m else None
        status_m = _PAY_STATUS.search(text)
        status = (status_m.group("s") or status_m.group("s2")) if status_m else None
        paid = None
        pm = _PAID_AT.search(text)
        if pm:
            paid = _date(pm.group("d"), pm.group("m"), pm.group("y")) if pm.group("d") else \
                _date(pm.group("d2"), str(_MONTHS.get(pm.group("mon").lower()[:3], 0)), pm.group("y2"))
        for_tender = None
        if ref and want_ref:
            got = _norm_ref(ref)
            for_tender = got == want_ref or (len(got) > 12 and (got in want_ref or want_ref in got))
        quote = _line(p.text or "", (p.text or "").find(m.group(0)[:12])) if m.group(0)[:12] in (p.text or "") else m.group(0)
        s = Sentence(page=p.number, text=quote[:400] or m.group(0), section="EMD receipt")
        out.append(_claim(spec.key, s, f"{format_inr(amount)}" + (f" ({status})" if status else ""), amount,
                          tender_ref=ref, for_this_tender=for_tender, payment_status=status,
                          paid_on=paid.isoformat() if paid else None,
                          tender_id=(_TENDER_ID.search(text).group("id").replace("\\", "") if _TENDER_ID.search(text) else None)))
    return out


# --------------------------------------------------------------------------------------------
# Contractor registration
# --------------------------------------------------------------------------------------------

_AUTHORITIES = [
    ("PWD", re.compile(r"public\s*works\s*department|सार्वजनिक\s*बांधकाम", re.I)),
    ("PMC", re.compile(r"pune\s*municipal\s*corporation|पुणे\s*महानगरपालिका", re.I)),
    ("PCMC", re.compile(r"pimpri|पिंपरी", re.I)),
    ("Zilla Parishad", re.compile(r"zilla\s*parishad|जिल्हा\s*परिषद", re.I)),
    ("MJP", re.compile(r"jeevan\s*pradhikaran|जीवन\s*प्राधिकरण", re.I)),
    ("CPWD", re.compile(r"\bCPWD\b|central\s*public\s*works", re.I)),
]
_CLASS = re.compile(r"\bCLASS\s*[-:]?\s*(?P<c>[IVX]{1,4}|\d|[A-E](?:-?\d)?)\b|वर्ग\s*[-:]?\s*(?P<m>\d{1,2})", re.I)
_VALID_RANGE = re.compile(r"(?:from\s*)?" + _DMY + r"\s*(?:to|ते|till|upto|up\s*to)\s*" + _DMY, re.I)
_VALID_UPTO = re.compile(r"(?:valid\s*(?:up\s*to|upto|till)|expiry\s*date|पर्यंत\s*वैध)\s*[:\-]?\s*" + _DMY, re.I)
_CAPACITY = re.compile(r"(?:work\s*capacity|capacity|पात्रता)[^\n\d]{0,40}?(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>lakhs?|lacs?|crores?|लक्ष|कोटी)", re.I)


def find_registration(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out: list[Claim] = []
    for p in _pages_of(doc, "registration"):
        raw = p.text or ""
        text = ascii_digits(raw)
        authority = next((name for name, pat in _AUTHORITIES if pat.search(text[:600])), None)
        cm = _CLASS.search(text)
        klass = (cm.group("c") or cm.group("m")) if cm else None
        valid_from = valid_to = None
        vm = _VALID_RANGE.search(text)
        if vm:
            valid_from, valid_to = _date(*vm.group(1, 2, 3)), _date(*vm.group(4, 5, 6))
        else:
            um = _VALID_UPTO.search(text)
            if um:
                valid_to = _date(*um.group(1, 2, 3))
        capacity = None
        km = _CAPACITY.search(text)
        if km:
            n = float(km.group("n").replace(",", ""))
            capacity = n * (10_000_000 if km.group("u").lower().startswith(("cr", "को")) else 100_000)
        if not (klass or valid_to or capacity):
            continue
        parts = [authority or "Registration"]
        if klass:
            parts.append(f"Class {klass}")
        if valid_to:
            parts.append(f"valid to {valid_to.strftime('%d-%m-%Y')}")
        if capacity:
            parts.append(f"works up to {format_inr_short(capacity)}")
        anchor = vm.group(0) if vm else (cm.group(0) if cm else "")
        pos = text.find(anchor) if anchor else 0
        s = Sentence(page=p.number, text=_line(raw, max(pos, 0))[:400] or raw[:300], section="Registration certificate")
        out.append(_claim(spec.key, s, ", ".join(parts), capacity, authority=authority, klass=klass,
                          valid_from=valid_from.isoformat() if valid_from else None,
                          valid_to=valid_to.isoformat() if valid_to else None, capacity=capacity))
    return out


# --------------------------------------------------------------------------------------------
# Bid capacity: read the working, not just the conclusion
# --------------------------------------------------------------------------------------------

_BC_UNIT = re.compile(r"(?P<lakh>in\s*lakhs?|in\s*lacs?|\(\s*(?:INR|Rs\.?)\s*in\s*la(?:kh|c)s?\s*\)|रु\.?\s*लक्ष)|(?P<crore>in\s*crores?|crores?\b)", re.I)
_NUM = r"(\d+(?:\.\d+)?)"
_BC_A = [re.compile(p, re.I) for p in (
    r"value\s*of\s*A\s*[^:|\d\n]{0,20}[:|=]\s*" + _NUM,
    r"max(?:imum)?\.?\s*value\s*of\s*A[^:|\n]{0,30}[:|=]\s*" + _NUM,
    r"\bA\s*=\s*(?:Year[^=\n]{0,40}?i\.?\s*e\.?\s*)?" + _NUM + r"(?!\s*[-–/]\s*\d{2})",
    r"\(update[^)]*\)\s*=\s*" + _NUM,
)]
_BC_B = [re.compile(p, re.I) for p in (
    r"value\s*of\s*B[^:|\d\n]{0,30}[:|=]\s*" + _NUM,
    r"\bB\s*=\s*" + _NUM,
    r"work\s*in\s*hand[^\n\d]{0,40}?(?:Rs\.?)?\s*[–-]?\s*" + _NUM,
)]
_BC_N = [re.compile(p, re.I) for p in (
    r"value\s*of\s*N\s*[:|=]\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<u>months?|years?)?",
    r"\bN\s*=\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<u>months?|years?)?",
)]
_BC_K = re.compile(r"A\s*[x×*]\s*N\s*[x×*]\s*(?P<k>\d(?:\.\d+)?)", re.I)
_BC_RESULT = re.compile(r"(?:bid(?:ding)?\s*capacity[^\n=:|]{0,40}?[=:|]\s*(?:Rs\.?\s*)?|^\s*[:=]\s*)(?P<n>\d[\d,]*(?:\.\d+)?)\s*(?P<u>crores?|lakhs?|lacs?)?", re.I | re.M)
_ADDRESSED = re.compile(r"(?:issued|given)\s+specifically\s+to\s+(?P<to>[A-Z][^.\n]{4,60}?)\s*(?:and|\.|$)|^\s*To,?\s*(?P<to2>(?:The\s*)?[A-Z][^\n]{3,60}?(?:Department|Corporation|PMC|PWD|PCMC)[^\n]{0,30})$", re.M)
_UDIN = re.compile(r"\bUDIN\b", re.I)


def _first(patterns, text: str) -> float | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def read_bid_capacity(text: str) -> dict | None:
    """A, N, B, the multiplier, the unit and the stated result of one certificate's working."""
    t = ascii_digits(text or "")
    unit_m = _BC_UNIT.search(t)
    unit = (100_000.0 if unit_m.group("lakh") else 10_000_000.0) if unit_m else 1.0
    if unit == 1.0:
        # "A=30.00 Lacs", "Rs. 12.50 lakhs": the unit written beside the figures, not in a heading.
        near = re.search(r"\d\s*(?P<lakh>lakhs?|lacs?)\b|\d\s*(?P<crore>crores?)\b", t, re.I)
        if near:
            unit = 100_000.0 if near.group("lakh") else 10_000_000.0
    a, b = _first(_BC_A, t), _first(_BC_B, t)
    # "A = 30.00 Lacs … (30.00 × 1.5 = 45.00) brought to current price level": a working that states
    # two values for A has not decided which one it uses. The committee decides; we do not pick.
    a_values = sorted({float(m.group(1)) for pat in _BC_A for m in pat.finditer(t)}
                      | {float(x) for x in re.findall(r"\bA\s*=[^\n]{0,40}?=\s*(\d+(?:\.\d+)?)", t)})
    ambiguous_a = len(a_values) > 1 and (max(a_values) - min(a_values)) > 0.01 * max(a_values)
    n_years = None
    for pat in _BC_N:
        m = pat.search(t)
        if m:
            n = float(m.group("n"))
            u = (m.group("u") or "").lower()
            n_years = n / 12 if u.startswith("month") or (not u and n > 3) else n
            break
    km = _BC_K.search(t)
    k = float(km.group("k")) if km else None
    results = []
    for m in _BC_RESULT.finditer(t):
        try:
            v = float(m.group("n").replace(",", ""))
        except ValueError:
            continue
        u = (m.group("u") or "").lower()
        scale = 10_000_000.0 if u.startswith("cr") else 100_000.0 if u.startswith(("lakh", "lac")) else unit
        if v > 0:
            results.append(v * scale)
    if a is None and not results:
        return None
    to = _ADDRESSED.search(text or "")
    addressed = (to.group("to") or to.group("to2")).strip() if to else None
    return {"A": a * unit if a is not None else None, "B": b * unit if b is not None else None, "N": n_years,
            "ambiguous_A": [v * unit for v in a_values] if ambiguous_a else None,
            "k": k, "unit": unit, "stated": results[-1] if results else None, "stated_all": results,
            "addressed_to": addressed, "restricted": bool(re.search(r"not\s+(?:be\s+)?used\s+for\s+any\s+other\s+purpose", text or "", re.I)),
            "udin": bool(_UDIN.search(text or ""))}


def find_bid_capacity(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out: list[Claim] = []
    want_n = spec.tender.get("completion_years")
    want_k = float(spec.params.get("formula_multiplier") or 1.5)
    for p in _pages_of(doc, "bid_capacity"):
        got = read_bid_capacity(p.text or "")
        if not got:
            continue
        recomputed = None
        defects = []
        if got["ambiguous_A"]:
            defects.append("states more than one value for A (" + ", ".join(format_inr_short(v) for v in got["ambiguous_A"])
                           + "), so the working does not settle which one applies")
        elif got["A"] is not None and want_n:
            recomputed = got["A"] * want_n * want_k - (got["B"] or 0.0)
        if got["k"] and abs(got["k"] - want_k) > 1e-6:
            defects.append(f"computed as (A × N × {got['k']:g}) − B; this tender prescribes (A × N × {want_k:g}) − B")
        if got["N"] and want_n and abs(got["N"] - want_n) > 0.05:
            defects.append(f"uses a period N of {got['N']:g} year(s); this tender's is {want_n:g}")
        if got["restricted"] and got["addressed_to"] and not re.search(r"pune\s*municipal|\bPMC\b", got["addressed_to"], re.I):
            defects.append(f"is addressed to {got['addressed_to']} and says it is not to be used for any other purpose")
        if not got["udin"]:
            defects.append("carries no UDIN for the chartered accountant")
        value = got["stated"] if got["stated"] is not None else recomputed
        shown = format_inr(value) if value is not None else "working only"
        s = Sentence(page=p.number, text=re.sub(r"\s+", " ", p.text or "")[:400], section="Bid capacity certificate")
        out.append(_claim(spec.key, s, shown, value, A=got["A"], B=got["B"], N=got["N"], k=got["k"],
                          recomputed=recomputed, defects=defects, addressed_to=got["addressed_to"],
                          restricted=got["restricted"], udin=got["udin"],
                          not_comparable=bool([d for d in defects if "UDIN" not in d])))
    return out


# --------------------------------------------------------------------------------------------
# Sole bidder: a single firm, and no joint-venture agreement
# --------------------------------------------------------------------------------------------

_JV_AGREEMENT = re.compile(r"joint\s*venture\s*agreement|consortium\s*agreement|\bJV\s*agreement|agreement\s+of\s+joint\s+venture"
                           r"|संयुक्त\s*(?:उपक्रम|भागीदारी)\s*करार", re.I)
_CONSTITUTION = [
    ("Proprietorship", re.compile(r"constitution\s*of\s*business\s*[:|]?\s*proprietor|details\s*of\s*proprietor|designation/status\s*[:|]?\s*proprietor|propriet[eo]r(?:ship)?\s*/\s*partners?\s+[A-Z]", re.I)),
    ("Partnership", re.compile(r"constitution\s*of\s*business\s*[:|]?\s*partnership|partnership\s*deed|deed\s*of\s*partnership", re.I)),
    ("Company", re.compile(r"certificate\s*of\s*incorporation|constitution\s*of\s*business\s*[:|]?\s*(?:private|public)\s*limited", re.I)),
]


def find_sole_bidder(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    out: list[Claim] = []
    types = _types(doc)
    for p in doc.pages:
        text = p.text or ""
        if not p.quality.quotable or not text.strip():
            continue
        kind = types[p.number - 1] if p.number - 1 < len(types) else None
        m = _JV_AGREEMENT.search(text)
        if m and kind not in ("completion_certificate", "boq_certificate", "work_order"):
            s = Sentence(page=p.number, text=_line(text, m.start())[:400], section="Joint venture")
            out.append(_claim(spec.key, s, "Joint-venture / consortium agreement in the bid", jv=True))
        if kind in ("gst", "registration", "constitution"):
            for label, pat in _CONSTITUTION:
                cm = pat.search(text)
                if cm:
                    s = Sentence(page=p.number, text=_line(text, cm.start())[:400], section="Firm constitution")
                    out.append(_claim(spec.key, s, f"Single firm ({label})", constitution=label, jv=False))
                    break
    return out


# --------------------------------------------------------------------------------------------
# Executed key quantities
# --------------------------------------------------------------------------------------------

def find_key_quantities(doc: ParsedDocument, spec: CriterionSpec) -> list[Claim]:
    from app.services.key_quantities import executed_rows, match_required

    required = spec.params.get("items") or []
    if not required:
        return []
    out: list[Claim] = []
    for row in executed_rows(doc):
        for req in match_required(row, required):
            s = Sentence(page=row.page, text=row.quote[:400], section="Executed quantities", in_table=True)
            out.append(_claim(spec.key, s, f"{req['item']}: {row.qty:g} {row.unit or ''}".strip(), row.qty,
                              item=req["item"], row_item=row.item, unit=row.unit, year=row.year,
                              year_note=row.year_note, work=row.work, tender_year=row.tender_year))
    return out


FINDERS.update({
    "pmc_emd": find_emd,
    "emd": find_emd,
    "pmc_registration": find_registration,
    "pmc_bid_capacity": find_bid_capacity,
    "no_joint_venture": find_sole_bidder,
    "pmc_key_quantities": find_key_quantities,
})
