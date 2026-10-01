"""What each page of a bid is.

A MahaTender bid envelope is usually two files: the portal's EMD payment receipt and one merged
scan of everything else — registration, PAN, GST, turnover and bid-capacity certificates, ITRs,
stamp-paper undertakings, dozens of work certificates, machinery hire agreements — in whatever
order the contractor stapled them. Nothing marks where one document ends and the next begins.

So each page is typed from its own words, in English and Marathi. The type decides where a finder
looks (a turnover figure on an ITR page is not the CA's certificate) and what the committee sees as
the bid's contents. Vocabulary only: every pattern names a kind of document, never one bidder.
"""

from __future__ import annotations

import re
from collections import Counter

# Most specific first: a page is given the first type whose pattern it matches.
PAGE_TYPES: list[tuple[str, str, re.Pattern]] = [
    ("emd_receipt", "EMD payment receipt", re.compile(
        r"EMD\s*Fee|Bank\s*Response\s*On\s*Payment|BidPayMessage|earnest\s*money\s*(?:deposit\s*)?(?:receipt|paid)"
        r"|इसारा\s*रक्कम\s*भरल|बयाणा\s*रक्कम\s*भरल", re.I)),
    ("bid_capacity", "Bid capacity certificate", re.compile(r"bid(?:ding)?\s*capacity\s*(?:calculation|certificate)?|बीड\s*कॅपॅसिटी", re.I)),
    ("turnover_certificate", "Turnover certificate (CA)", re.compile(
        r"turn\s?over\s*cert|certify\s*that\s*the\s*turn\s?over|turn\s?over\s*of\s*(?:the\s*)?(?:firm|company|mr|m/s)"
        r"|annual\s*turn\s?over|उलाढाल\s*प्रमाणपत्र", re.I)),
    ("constitution", "Firm constitution (deed / shop act / udyam)", re.compile(
        r"partnership\s*deed|deed\s*of\s*partnership|memorandum\s*of\s*association|certificate\s*of\s*incorporation"
        r"|udyam|shop\s*(?:&|and)?\s*establishment|दुकाने\s*व\s*आस्थापना|भागीदारी\s*(?:पत्र|करार)", re.I)),
    ("registration", "Contractor registration certificate", re.compile(
        r"(?:contractor|engineer)'?s?\s*registration|unemployed\s*engineer|registration\s*class|enlistment|registered\s*contractor"
        r"|ठेकेदार\s*नोंदणी|कंत्राटदार\s*नोंदणी|नोंदणी\s*प्रमाणपत्र|नोंदणीकृत\s*ठेकेदार", re.I)),
    ("itr", "Income-tax return", re.compile(r"income\s*tax\s*return\s*acknowledgement|\bITR-?\d\b.*assessment\s*year|form\s*26as", re.I | re.S)),
    ("gst", "GST registration", re.compile(r"GST\s*REG-?0?6|goods\s*and\s*services\s*tax.*registration|\bGSTIN\b", re.I | re.S)),
    ("pan", "PAN card", re.compile(r"permanent\s*account\s*number", re.I)),
    ("solvency", "Solvency certificate", re.compile(r"solvency", re.I)),
    ("balance_sheet", "Balance sheet / P&L", re.compile(r"balance\s*sheet|profit\s*(?:&|and)\s*loss\s*(?:account|statement)", re.I)),
    ("works_in_hand", "Works in hand (Statement I)", re.compile(
        r"work[s]?\s*in\s*hand|works?\s*tendered\s*for|हातातील\s*काम|प्रगतीपथावरील\s*काम", re.I)),
    ("completion_certificate", "Work completion certificate", re.compile(
        r"completion\s*certificate|work\s*done\s*certificate|satisfactor\w*\s*complet|कामाचा\s*दाखला"
        r"|प[ुू]?र्णत्वाचा\s*दाखला|काम\s*प[ुू]?र्ण\S*\s*(?:झाल्याचा\s*)?दाखला|समाधानकारक\S*\s*(?:रित्या|रीत्या)?\s*प[ुू]?र्ण"
        # A plain "दाखला" (certificate) is a completion certificate when it goes on to name finished
        # works or the amounts paid for them.
        r"|दाखला(?=.{0,600}(?:प[ुू]?र्ण|ठेकेदाराची\s*रक्कम|निविदा\s*र))", re.I | re.S)),
    ("completed_works", "Completed works statement (Statement II)", re.compile(
        r"works?\s*of\s*similar\s*(?:type|nature)|similar\s*type\s*and\s*magnitude|completed\s*works?\s*statement"
        r"|form\s*-?\s*(?:II|2)\b|statement\s*-?\s*(?:no\.?\s*)?(?:II|2)\b", re.I)),
    ("boq_certificate", "Executed quantities (BOQ certificate)", re.compile(
        r"B\s*\.?\s*O\s*\.?\s*Q\s*\.?\s*CERTIFICATE|प्रत्यक्ष\s*क्व[ॉा]ं?टिटी|executed\s*quantit", re.I)),
    ("work_order", "Work order / agreement", re.compile(
        r"work\s*order|letter\s*of\s*(?:acceptance|award)|कार्या?ं?रंभ\s*आदेश|वर्क\s*ऑर्डर", re.I)),
    ("machinery", "Machinery / equipment", re.compile(
        r"machinery\s*list|list\s*of\s*(?:machinery|equipment)|plant\s*(?:&|and)\s*machinery|hire\s*agreement"
        r"|यंत्र\s*सामु?ग्री|मशिनरी|भाडेतत्वावर", re.I)),
    ("technical_staff", "Technical staff", re.compile(
        r"technical\s*staff|list\s*of\s*(?:technical\s*)?personnel|key\s*personnel|curriculum\s*vitae"
        r"|तांत्रिक\s*कर्मचारी|अभियंता\s*यादी", re.I)),
    ("affidavit", "Undertaking / affidavit (stamp paper)", re.compile(
        r"affidavit|undertaking|प्रतिज्ञापत्र|हमीपत्र|\bMAHARASHTRA\s*\d{4}\s*[A-Z]{2}\s*\d{6}|मुद्रांक", re.I)),
    ("identity", "Identity document", re.compile(r"unique\s*identification\s*authority|आधार\s*क्रमांक|\baadhaa?r\b", re.I)),
    ("labour", "Labour registration (PF / ESIC)", re.compile(r"provident\s*fund|\bESIC\b|employees'?\s*state\s*insurance|labour\s*licen", re.I)),
    ("bank", "Bank document", re.compile(r"bank\s*statement|pass\s*book|account\s*statement", re.I)),
]
LABELS = {key: label for key, label, _ in PAGE_TYPES}
LABELS_BY_KEY = {key: pattern for key, _, pattern in PAGE_TYPES}
_SWORN = re.compile(r"हमीपत्र|प्रतिज्ञापत्र|\baffidavit\b|solemnly\s*affirm", re.I)


# Types recognisable anywhere on the page. Everything else must be named in the page's heading:
# a work certificate's table has a "work order" column, an affidavit mentions "my registration
# certificate", a shop-act licence the "previous registration number".
_ANYWHERE = {"emd_receipt", "turnover_certificate", "itr", "gst", "pan", "solvency",
             "boq_certificate", "completion_certificate"}  # a bid-capacity certificate is titled as one
_HEAD = 700  # characters


def page_type(text: str) -> str | None:
    t = text or ""
    if len(t.strip()) < 30:
        return None
    head = t[:_HEAD]
    # A sworn statement lists the documents the bid carries ("टर्नओव्हर सर्टिफिकेट, बीड कॅपॅसिटी, …");
    # it is the statement, not any of them.
    if _SWORN.search(head) and not LABELS_BY_KEY["emd_receipt"].search(head):
        return "affidavit"
    for key, _, pattern in PAGE_TYPES:
        if pattern.search(head):
            return key
    for key, _, pattern in PAGE_TYPES:
        if key in _ANYWHERE and pattern.search(t):
            return key
    return None


def classify_pages(parsed) -> list[str | None]:
    """One type (or None) per page. A page with no type of its own that follows a page of a
    multi-page document (a certificate's table running over, the second page of an agreement)
    inherits that type when it has no heading of its own."""
    out: list[str | None] = []
    for p in parsed.pages:
        text = p.text or p.raw_text or ""
        kind = page_type(text) if p.quality.kind not in ("image", "garbled", "blank") else None
        if kind is None and out and out[-1] in _CONTINUES and p.quality.kind not in ("image", "garbled", "blank") \
                and (p.tables or len(text.strip()) > 80):
            kind = out[-1]
        out.append(kind)
    return out


# Documents whose content routinely runs onto the next page without repeating a heading.
_CONTINUES = {"completion_certificate", "boq_certificate", "completed_works", "works_in_hand", "affidavit",
              "machinery", "work_order", "balance_sheet", "technical_staff"}


def contents(types: list[str | None]) -> list[dict]:
    """Page ranges per type, in bid order: what the committee sees as the bid's table of contents."""
    out: list[dict] = []
    for i, t in enumerate(types, start=1):
        if t is None:
            continue
        if out and out[-1]["type"] == t and out[-1]["to"] == i - 1:
            out[-1]["to"] = i
        else:
            out.append({"type": t, "label": LABELS[t], "from": i, "to": i})
    return out


def summary(types: list[str | None]) -> dict[str, int]:
    return dict(Counter(t for t in types if t))
