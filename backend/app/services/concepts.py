"""What a requirement is *about*, and every way a bid is known to say it.

A tender states a requirement in its own words and a bid answers in the bidder's. The two
vocabularies barely overlap, and nothing in the submission is labelled with the tender's wording:

    tender (DBT RFP p.15)  "an on-roll staff strength of a minimum of 30 IT Professionals
                            having Core Expertise in Software Development"
    bid    (p.75)          "Undertaking for number of technically qualified professionals on
                            companies' payroll ... our organization has 50+ technically qualified
                            professionals having core expertise in Software development"

Not one of "on-roll", "staff strength" or "IT Professionals" appears in the bid. Searching for the
requirement's words finds nothing and the criterion comes back unknown, which is the failure mode
this module exists to remove: the fact *is* in the submission, under another name.

So a requirement is first resolved to a **concept**, and the concept carries the vocabulary. Three
kinds of cue, because they license different conclusions:

  * `anchors`    name the fact on their own. "Net-Worth", "Annual Turnover", "on companies'
                 payroll". Finding one is grounds to read a value out of the neighbourhood.
  * `supports`   are consistent with the fact but name other things too. "Revenue from
                 Operations" is the turnover line of a P&L, and also appears in notes and
                 schedules; "employees" appears in a dozen unrelated declarations. A support
                 needs a second cue, or a structure (a year grid, a numbered roster) to stand.
  * `vetoes`     name the fact in text that states no value of it. Every bid carrying a tax
                 invoice carries "it shall be accounted for in the turnover of sales while filing
                 of return" (7453310 p.279) — the word "turnover", next to invoice amounts, in a
                 sentence that reports nobody's turnover. A veto outranks any anchor on the same
                 span, because a false figure cited to a real page is worse than no figure.

The cue lists are vocabulary, not tender rules: they say how Indian procurement documents word
these facts, and they are the same list whichever tender is loaded. Nothing here may branch on a
particular tender, issuer or bidder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.values import GSTIN


def _rx(*alts: str) -> re.Pattern:
    return re.compile("|".join(alts), re.IGNORECASE)


_DEVA = re.compile(r"[ऀ-ॿ]")
_LATN = re.compile(r"[A-Za-z]")


def script_of(text: str) -> str:
    """"deva", "latin" or "mixed" — which alphabet a span is written in.

    A tender and the bids answering it are routinely in different languages: a Marathi PMC works
    tender is answered by bids whose certificates, invoices and balance sheets are in English, and
    an English RFP draws bids with Marathi completion certificates stapled into them. Comparing the
    requirement's own words against the evidence page is meaningless when the two are not even in
    the same alphabet, so the matching has to know.
    """
    deva, latin = len(_DEVA.findall(text or "")), len(_LATN.findall(text or ""))
    if deva and latin:
        ratio = deva / (deva + latin)
        if 0.15 < ratio < 0.85:
            return "mixed"
    return "deva" if deva > latin else "latin"


# Every bid opens with a checklist of what it contains, and the tender closes with the same list.
# Those rows name each concept precisely — "Copy of the audited total turnover, net-worth and
# Profit & Loss for financial years 2022-23, 2023-24 and 2024-25 ... Page No.80-89" — next to
# numbers that are page ranges and years, not money. Read as evidence it yields a net worth of
# ₹2,022 for a company whose certificate says ₹3.32 crore, cited to a real page of the real bid.
#
# A checklist is a claim about where a document is, never about what it says, so this suppresses
# every concept at once rather than being repeated in each one's vetoes.
CHECKLIST = _rx(
    r"\bpage\s*(?:no|nos|number)s?\.?\s*[:\-]?\s*\d",
    r"\bas\s+per\s+(?:the\s+)?format\s+(?:mentioned|given|specified)",
    r"\bas\s+per\s+section\s+\d",
    r"\bcompliance\s*\(?\s*yes\s*/\s*no",
    r"^\s*\d{1,2}[.)]?\s+[A-Z][^\n]{3,60}?\s+\d{1,3}\s*[-–]\s*\d{1,3}\s*$",
    r"^\s*copy\s+of\s+the\b",
    r"\bchecklist\b",
    r"\bdocumentary\s+evidence\b",
    r"\bto\s+be\s+(?:included|submitted|enclosed)\b",
)


# A page can state the fact and deny it in the same breath. One bid's EPF office writes "As on date
# of your application, your establishment is **not registered with ESIC**", and the two pages after
# it are a general brochure about the ESI Act. Every word a search for ESIC registration looks for
# is present, and the document says the bidder does not have it. Passing a bidder on a registration
# their own evidence denies is as wrong as failing one who has it.
#
# This is separate from a veto: a veto says "this text is not about the fact", a negation says "this
# text is about the fact and says no". Concepts whose whole purpose is a negative declaration
# ("we have not been blacklisted") set `negations=None`, or every one of them would suppress itself.
DEFAULT_NEGATION = _rx(
    r"\bnot\s+(?:yet\s+)?(?:registered|applicable|available|enclosed|attached|submitted|obtained|"
    r"held|allotted|issued|covered|required)\b",
    r"\bdoes\s+not\s+(?:hold|have|possess|apply)\b",
    r"\bno\s+(?:such\s+)?(?:certificate|registration|licence|license|record|code)\s+(?:is|was|has)\b",
    r"\bnil\b",
    r"\bnot\s+applicable\b",
)


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    anchors: re.Pattern
    supports: re.Pattern | None = None
    vetoes: re.Pattern | None = None
    # A structured identifier is the strongest anchor there is: a PAN, a GSTIN, a CIN or a Udyam
    # number is recognisable by its shape alone, so the fact is located without depending on any
    # word near it — which matters because the word is often in a column header two cells away, or
    # on a scan that OCR read as something else.
    identifier: re.Pattern | None = None
    negations: re.Pattern | None = DEFAULT_NEGATION
    # Headings of the document that carries the fact. A bid is one merged scan of thirty
    # documents, and the heading is often the only thing on the page that says what the grid is.
    documents: re.Pattern | None = None
    # Row labels of a derivation the bidder states instead of the figure itself.
    derived_from: tuple[tuple[str, ...], ...] = ()
    requirement_cues: re.Pattern | None = None
    datatype: str = "currency"  # currency | count | boolean
    # A roster of named people, where the number of rows is itself the evidence.
    roster: bool = False

    def hit(self, text: str) -> str | None:
        """How this text states the fact: "identifier", "anchor", "support", or None.

        A checklist row, a veto or a negation all return None, in that order of cheapness. Nothing
        below them can override: text that is not about the fact, or says the bidder does not have
        it, is not evidence that they do.
        """
        if CHECKLIST.search(text):
            return None
        if self.vetoes is not None and self.vetoes.search(text):
            return None
        if self.negations is not None and self.negations.search(text):
            return None
        if self.identifier is not None and self.identifier.search(text):
            return "identifier"
        if self.anchors.search(text):
            return "anchor"
        if self.supports is not None and self.supports.search(text):
            return "support"
        return None


# ----------------------------------------------------------------------------------------------
# Money the bidder earned
# ----------------------------------------------------------------------------------------------

# Twelve bids state turnover eleven different ways. Observed headings, verbatim:
#   "TURNOVER CERTIFICATE" / "ANNUAL TURNOVER CERTIFICATE" / "Turnover Certificates"
#   "Financial Year | Turnover (Rs)"                       (7321343 p.6)
#   "Financial Year | Sales Turnover (in Work done crore)" (7322267 p.9)
#   "Sr No | Financial Year | Annual Turnover"             (7453310 p.48)
#   "No. | Details | FY 2022-23 ... | 1 Turnover 407"      (DBT bid p.15, figures in lakhs)
#   "Revenue From Operations"                              (7453310 p.87, audited P&L)
#   "PART H - Details of Turnover as per GSTR-3B ... Taxable Turnover | Total Turnover"
#   "Gross receipt"                                        (7455121 p.64, Form 26AS)
#   "Turnover / Receipts"                                  (7455121 p.77, ITR computation)
# and in prose: "has the turnover as stated below during the last Five financial years",
# "their annual turnovers for last three consecutive years are as under", "the value of Business
# Turnover of, MR.<name> PROP OF M/s <firm>".
TURNOVER = Concept(
    key="turnover",
    label="Annual turnover",
    anchors=_rx(
        r"\bturn\s?-?\s?overs?\b",
        r"\bgross\s+receipts?\b",
        r"\bcontract\s+receipts?\b",
        r"\bbusiness\s+receipts?\b",
        r"\bsales?\s+and\s+services?\b",
        # Marathi. "मागील ५ वर्षातील कंत्राटदाराची कामाची उलाढाल", "चार्टर्ड अकाउंटंट प्रमाणित
        # केलेली निविदाधारकाची मागील पाच वर्षातील आर्थिक उलाढाल".
        r"उलाढाल", r"आर्थिक\s*उलाढाल", r"वार्षिक\s*उलाढाल", r"कामाची\s*उलाढाल",
    ),
    supports=_rx(
        r"\brevenue\s+from\s+operations?\b",
        r"\bincome\s+from\s+operations?\b",
        r"\btotal\s+(?:income|revenue)\b",
        r"\breceipts?\s*/\s*turnover\b",
        r"\boperating\s+income\b",
    ),
    # Every tax invoice in a bid carries the GST declaration, and bank-detail blocks interleave
    # with it in the text layer (7451021 p.67-68). "Aggregate turnover in the preceding year" is
    # the composition-scheme line on a GST certificate, not a reported figure.
    vetoes=_rx(
        r"accounted\s+for\s+in\s+(?:the\s+)?(?:\S+\s+){0,6}?turnover\s+of\s+(?:supplies|sales)",
        r"turnover\s+of\s+(?:supplies|sales)\s+while\s+fil",
        r"threshold\s+limit",
        r"\bturnover\b[^.\n]{0,40}\bexceeds?\s+(?:rs\.?|the)",
        r"liable\s+to\s+(?:be\s+)?register",
    ),
    documents=_rx(
        r"turn\s?over\s+certificate",
        r"certificate\s+of\s+turn\s?over",
        r"statement\s+of\s+(?:annual\s+)?turn\s?over",
        r"profit\s+(?:and|&)\s+loss",
        r"form\s*(?:no\.?)?\s*26\s*as",
        r"gstr\s*-?\s*3\s*b",
    ),
    requirement_cues=_rx(
        r"\bturn\s?-?\s?overs?\b", r"\bgross\s+receipts?\b", r"average\s+annual\s+(?:financial\s+)?turnover",
        r"उलाढाल",
    ),
)

# Net worth is the concept the corpus hides best. In twelve civil-works bids the words appear
# twice: once in an index ("14. Net Worth Certificate 49-49", 7453310 p.2) and once as a balance
# sheet caption ("(1) Shareholders Fund (a) Share Capital", p.86). The figure itself is either
#   * a labelled row of the CA's statement — "2 Net-Worth 255 293 332" (DBT bid p.15, lakhs), or
#   * never stated, and derivable only from the balance sheet. The certificate that asks for it
#     prints the derivation: "Net-worth is determined by subtracting total liabilities from total
#     assets" (DBT bid p.13), and the other standard route is share capital + reserves & surplus.
# `derived_from` holds both routes so a bid that states only a balance sheet can still answer.
NET_WORTH = Concept(
    key="net_worth",
    label="Net worth",
    anchors=_rx(
        r"\bnet\s*-?\s*worth\b",
        r"\bnetworth\b",
        r"\bnet\s+assets?\b",
        r"\bowners?\s*'?\s*funds?\b",
        r"\bproprietor'?s?\s+(?:capital|funds?)\b",
        r"निव्वळ\s*(?:मालमत्ता|संपत्ती|मूल्य)", r"नक्त\s*मालमत्ता",
    ),
    supports=_rx(
        r"\bshareholders?\s*'?\s*funds?\b",
        r"\bequity\s+and\s+liabilit",
        r"\breserves?\s+(?:and|&)\s+surplus\b",
        r"\bcapital\s+(?:and|&)\s+reserves?\b",
        r"\btotal\s+equity\b",
    ),
    # The auditor's description of the method is not the bidder's figure.
    vetoes=_rx(
        r"net\s*-?\s*worth\s+is\s+determined\s+by",
        r"verification\s+of\s+net\s*-?\s*worth",
        r"pursuant\s+to\s+the\s+requirements",
    ),
    documents=_rx(
        r"net\s*-?\s*worth\s+certificate",
        r"turn\s?over\s+(?:and|,)\s*net\s*-?\s*worth",
        r"balance\s+sheet",
        r"annexure\s*[-–·]?\s*a\b",
    ),
    derived_from=(
        ("total assets", "total liabilities"),
        ("share capital", "reserves and surplus"),
    ),
    requirement_cues=_rx(r"\bnet\s*-?\s*worth\b", r"\bnetworth\b", r"positive\s+net\s*-?\s*worth",
                         r"निव्वळ\s*(?:मालमत्ता|संपत्ती|मूल्य)"),
)

PROFIT = Concept(
    key="net_profit",
    label="Net profit",
    anchors=_rx(r"\bnet\s+profit\b", r"\bprofit\s+after\s+tax\b", r"\bPAT\b"),
    supports=_rx(r"\bprofit\s+(?:and|&)\s+loss\b", r"\bprofit\s+before\s+tax\b", r"\bsurplus\b"),
    vetoes=_rx(r"profit\s+(?:and|&)\s+loss\s+(?:account|statement)s?\s*$", r"loss\s+carried\s+forward"),
    documents=_rx(r"profit\s+(?:and|&)\s+loss", r"annexure\s*[-–·]?\s*a\b"),
    requirement_cues=_rx(r"\bnet\s+profit\b", r"\bprofit\s+(?:in|over|for)\b", r"positive\s+net\s+profit"),
)

SOLVENCY = Concept(
    key="solvency",
    label="Solvency",
    anchors=_rx(
        r"\bsolvenc(?:y|ies)\b",
        r"\bsolvent\s+(?:to|for|up\s+to)\b",
        r"\bfinancial\s+standing\b",
        r"आर्थिक\s*पत", r"पतदर्शक", r"सॉल्व्हन्सी",
    ),
    supports=_rx(
        r"\bbanker'?s?\s+certificate\b",
        r"\bcredit\s+(?:limit|facilit)",
        r"\bcash\s+credit\b",
        r"\boverdraft\b",
        r"\bsanctioned?\s+limit\b",
    ),
    vetoes=_rx(r"shall\s+(?:not\s+)?be\s+insolvent", r"receivership\s+or\s+bankrupt"),
    documents=_rx(r"solvency\s+certificate", r"banker'?s?\s+(?:certificate|report)"),
    requirement_cues=_rx(r"\bsolvenc", r"banker'?s?\s+certificate", r"\bfinancial\s+standing\b"),
)

# ----------------------------------------------------------------------------------------------
# People
# ----------------------------------------------------------------------------------------------

# The requirement counts people; the bid proves them with a roster. Three routes to the number,
# and they are checked in this order because they differ in how much they can be trusted:
#   1. a declared count — "has 50+ technically qualified professionals" (DBT bid p.75)
#   2. the roster's own row count — the numbered list on pp.75-77 runs to 66
#   3. a tabulated staff statement — "STATEMENT NO -IV Details of Technical Personnel Available
#      with Contractor" (7453310 p.292), "Technical Staff Certificate ... the following technical
#      staff is currently working with company / organization" (7455121 p.15)
# A declared count can be a round claim ("50+"); a roster is countable and is what corroborates it.
STAFF_STRENGTH = Concept(
    key="staff_strength",
    label="Staff strength",
    anchors=_rx(
        r"\bon\s*-?\s*roll\b", r"\bon\s+(?:its|their|our|the|compan(?:y|ies)'?s?)\s*'?\s*(?:rolls?|payroll)\b",
        r"\bpayroll\b",
        r"\bstaff\s+strength\b",
        r"\bmanpower\b",
        r"\bIT\s+professionals?\b",
        r"\btechnical(?:ly)?\s+(?:qualified\s+)?(?:professionals?|personnel|staff|manpower)\b",
        r"\bkey\s+personnel\b",
        r"\btechnical\s+persons?\b",
        # The Marathi personnel statement's own column headings (PMC works tender, Statement IV):
        # "अ.क्र. | नाव | शैक्षणिक अर्हता | कामाचा अनुभव | निविदाधारकाकडे कार्यरत कालावधी".
        r"तांत्रिक\s*(?:कर्मचारी|कर्मचा|मनुष्यबळ|व्यक्ती)", r"मनुष्यबळ",
        r"शैक्षणिक\s*(?:अर्हता|पात्रता)", r"कर्मचारी\s*संख्या",
    ),
    supports=_rx(
        r"\bemployees?\b", r"\bprofessionals?\b", r"\bengineers?\s+(?:employed|available|on)\b",
        r"\bgraduate\s+engineers?\b", r"\bqualified\s+(?:staff|engineers?)\b",
        r"\borgani[sz]ation(?:al)?\s+(?:chart|structure|set\s*-?\s*up)\b",
        r"\beducational\s+qualification\b",
    ),
    # The site-deployment clause of a works contract names the same cadres and is an undertaking
    # about the future, not a statement of present strength. `criteria_extraction` already refuses
    # these on the tender side; a bid repeats the clause back and must be refused here too.
    vetoes=_rx(
        r"shall\s+(?:be\s+)?(?:deploy|depute|engage|appoint|provide|post)",
        r"(?:deploy|depute)(?:ed|ment)?\s+(?:at|on)\s+(?:the\s+)?site",
        r"during\s+(?:the\s+)?(?:execution|currency|progress)",
        r"will\s+be\s+(?:deployed|appointed|engaged)",
    ),
    documents=_rx(
        r"(?:details?\s+of\s+)?technical\s+personnel",
        r"technical\s+staff\s+certificate",
        r"manpower\s+declaration",
        r"undertaking\s+for\s+number\s+of",
        r"list\s+of\s+(?:technical\s+)?(?:staff|personnel|employees)",
        r"statement\s*(?:no\.?)?\s*[-–]?\s*(?:iv|4)\b",
        r"form\s*-?\s*(?:iv|4)\b",
    ),
    requirement_cues=_rx(
        r"\bIT\s+professionals?\b", r"\bon\s*-?\s*roll\b", r"\bstaff\s+strength\b",
        r"\btechnical\s+manpower\b", r"\bmanpower\b", r"\bkey\s+personnel\b",
        r"\btechnical\s+(?:staff|personnel)\b",
        r"मनुष्यबळ", r"तांत्रिक\s*कर्मचारी", r"शैक्षणिक\s*(?:अर्हता|पात्रता)",
    ),
    datatype="count",
    roster=True,
)

# ----------------------------------------------------------------------------------------------
# Registrations and certificates: present or absent, no value to read
# ----------------------------------------------------------------------------------------------

QUALITY_CERT = Concept(
    key="quality_certification",
    label="Quality certification",
    anchors=_rx(r"\bISO\s*[/:]?\s*(?:IEC\s*)?\d{4,5}", r"\bCMMI\b", r"\bcapability\s+maturity\b",
        r"आय\.?एस\.?ओ",
    ),
    supports=_rx(r"\bquality\s+management\s+system\b", r"\bcertificate\s+of\s+registration\b"),
    vetoes=_rx(r"\bshall\s+(?:submit|obtain|furnish)\b", r"\bis\s+required\s+to\s+(?:have|possess)\b"),
    documents=_rx(r"ISO\s*\d{4,5}", r"quality\s+management"),
    requirement_cues=_rx(r"\bISO\b", r"\bCMMI\b", r"quality\s+certificat",
        r"आय\.?एस\.?ओ",
    ),
    datatype="boolean",
)

NON_BLACKLIST = Concept(
    key="non_blacklisting",
    label="Non-blacklisting declaration",
    anchors=_rx(
        r"\bnot\s+been\s+(?:black\s*-?\s*listed|blacklisted|debarred|banned)\b",
        r"\banti\s*-?\s*black\s*-?\s*listing\b",
        r"\bnon\s*-?\s*black\s*-?\s*listing\b",
    
        r"काळ्या\s*यादी",
        r"काळी\s*यादी",
    ),
    supports=_rx(r"\bblack\s*-?\s*list", r"\bdebarr", r"\bbanned\b"),
    # A MahaTender receipt page prints "Debarred/Revoked Details" as a portal heading
    # (7314254 p.1); the RFP prints the requirement itself. Neither is the bidder's declaration.
    vetoes=_rx(
        r"debarred\s*/\s*revoked\s+details",
        r"should\s+not\s+have\s+been\s+black",
        r"grounds\s+for\s+(?:the\s+)?annulment",
    ),
    documents=_rx(r"anti\s*-?\s*black", r"non\s*-?\s*black", r"declaration.{0,30}black"),
    requirement_cues=_rx(r"black\s*-?\s*list", r"\bdebarr", r"non\s*-?\s*blacklisting",
        r"काळ्या\s*यादी",
    ),
    datatype="boolean",
    negations=None,
)

LEGAL_ENTITY = Concept(
    key="legal_entity",
    label="Legal entity / incorporation",
    # A CIN ("U72900PN2011PTC139251") and an LLPIN ("AAD-7815") are minted by the Registrar of
    # Companies and by nobody else, so either one proves the bidder is the kind of entity the clause
    # requires without the word "incorporation" appearing anywhere near it.
    identifier=re.compile(r"\b[LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b|\b[A-Z]{3}\s*-\s*\d{4}\b"),
    anchors=_rx(
        r"\bcertificate\s+of\s+incorporation\b",
        r"\bcertificate\s+of\s+registration\b",
        r"\bC\.?I\.?N\.?\s*[:.]?\s*[A-Z]\d{5}[A-Z]{2}\d{4}",
        r"\bincorporated\s+under\b",
        r"\bpartnership\s+deed\b",
        r"\bLLP\s*I?N\b",
        r"\budyam\s+registration\b",
    
        r"नोंदणी\s*प्रमाणपत्र",
        r"नोंदणीकृत",
        r"भागीदारी\s*संस्था",
        r"पूर्वनोंदणी",
    ),
    supports=_rx(r"\bcompanies\s+act\b", r"\bLLP\s+act\b", r"\bregistrar\s+of\s+companies\b"),
    vetoes=_rx(r"should\s+be\s+an\s+entity\s+registered", r"sub\s*-?\s*contracting"),
    documents=_rx(r"incorporation", r"registration\s+certificate", r"partnership\s+deed"),
    requirement_cues=_rx(r"legal\s+entity", r"incorporat", r"registered\s+in\s+india",
        r"नोंदणी",
        r"नोंदणीकृत",
    ),
    datatype="boolean",
)


ALL: tuple[Concept, ...] = (
    TURNOVER, NET_WORTH, PROFIT, SOLVENCY, STAFF_STRENGTH,
    QUALITY_CERT, NON_BLACKLIST, LEGAL_ENTITY,
)
BY_KEY = {c.key: c for c in ALL}


def resolve(requirement_text: str, *, label: str = "", datatype: str = "") -> Concept | None:
    """The concept a tender's requirement is about, or None when no concept covers it.

    Matched on the requirement's own words, so the same criterion resolves identically whichever
    tender printed it. `label` is searched too: an extracted criterion often carries the clause's
    heading ("Manpower Declaration") where the body carries the measurement.
    """
    scored: list[tuple[int, Concept]] = []
    for concept in ALL:
        if concept.requirement_cues is None:
            continue
        in_body = len(set(m.group(0).lower() for m in concept.requirement_cues.finditer(requirement_text)))
        # The clause's own heading names the requirement; its body says everything around it. A row
        # reading "Power of Attorney" inside a clause that also mentions a registration certificate
        # is about the power of attorney, so the heading is worth more than a mention further down.
        in_label = len(set(m.group(0).lower() for m in concept.requirement_cues.finditer(label or "")))
        hits = in_body + 2 * in_label
        if hits:
            # A count requirement and a currency requirement can share vocabulary ("manpower
            # strength 20" vs "turnover of 4 Cr"); the datatype the tender assigned breaks the tie.
            bonus = 1 if datatype and concept.datatype == datatype else 0
            scored.append((hits + bonus, concept))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    return scored[0][1]


# ----------------------------------------------------------------------------------------------
# Identity: who the bidder is, proved by a number only one issuer can mint
# ----------------------------------------------------------------------------------------------

# A PAN appears 293 distinct ways in twelve bids — "PAN BZSPB8994G", "having PAN : AQRPD4656N",
# "(PAN BZSPB8994G )", and inside every ITR acknowledgement — and almost never next to the words a
# requirement uses ("Copy of PAN"). The shape is the evidence.
PAN = Concept(
    key="pan",
    label="PAN",
    identifier=re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    anchors=_rx(r"\bP\.?A\.?N\.?\b", r"permanent\s+account\s+number"),
    supports=_rx(r"income\s*-?\s*tax", r"\bITR\b", r"acknowledgement\s+number"),
    documents=_rx(r"\bPAN\b\s*card", r"permanent\s+account\s+number"),
    requirement_cues=_rx(r"\bPAN\b", r"permanent\s+account\s+number"),
    datatype="boolean",
)

GST = Concept(
    key="gst",
    label="GST registration",
    identifier=GSTIN,
    anchors=_rx(r"\bGSTIN\b", r"\bG\.?S\.?T\.?\b", r"goods\s+and\s+services\s+tax"),
    supports=_rx(r"registration\s+certificate", r"\bGSTR\s*-?\s*\d", r"composition\s+scheme"),
    # A GSTIN on a supplier's tax invoice is the supplier's, and a bid is full of purchase invoices.
    vetoes=_rx(r"buyer\s*\(bill\s*to\)", r"consignee\s*\(ship\s*to\)", r"bill\s+of\s+loading"),
    documents=_rx(r"goods\s+and\s+services\s+tax", r"GST\s+registration"),
    requirement_cues=_rx(r"\bGST\b", r"\bGSTIN\b", r"goods\s+and\s+services\s+tax"),
    datatype="boolean",
)

MSME = Concept(
    key="msme_udyam",
    label="MSME / Udyam registration",
    identifier=re.compile(r"\bUDYAM\s*-\s*[A-Z]{2}\s*-\s*\d{2}\s*-\s*\d{5,8}\b", re.IGNORECASE),
    anchors=_rx(r"udyam\s+registration", r"\bMSME\b", r"micro,?\s*small",
        r"उद्यम\s*नोंदणी",
        r"सूक्ष्म.{0,12}लघु",
    ),
    supports=_rx(r"type\s+of\s+enterprise", r"\bDIC\b", r"MSME\s*-?\s*DFO"),
    documents=_rx(r"udyam\s+registration\s+certificate", r"\bMSME\b\s+certificate"),
    requirement_cues=_rx(r"\bMSME\b", r"\budyam\b", r"micro,?\s*small\s+and\s+medium",
        r"उद्यम",
        r"सूक्ष्म",
    ),
    datatype="boolean",
)

# "DATE OF INCORPORATION / REGISTRATION OF ENTERPRISE 27/12/2019" (Udyam certificate, 7453310 p.4),
# "Date of commencement of business / 01/02/2013" (MahaTender profile, 7322312 p.48). A requirement
# asking for "Proof of Existence of Firm from last 5 years" is answered by a date, not a sentence.
FIRM_AGE = Concept(
    key="firm_age",
    label="Years in business",
    anchors=_rx(
        r"date\s+of\s+(?:incorporation|registration|establishment|commencement)",
        r"(?:incorporated|established|registered|commenced)\s+(?:on|in|since)",
        r"in\s+(?:continuous\s+)?(?:business|operation)\s+since",
        r"years\s+of\s+(?:experience|existence|standing)",
    ),
    supports=_rx(r"\bsince\s+(?:19|20)\d{2}\b", r"registration\s+no\.?\s+date\s*(?:&|and)\s*year"),
    vetoes=_rx(r"date\s+of\s+commencement\s+of\s+the\s+polic", r"date\s+of\s+(?:work|contract)\s+commencement"),
    documents=_rx(r"certificate\s+of\s+incorporation", r"udyam\s+registration", r"partnership\s+deed"),
    requirement_cues=_rx(r"existence\s+of\s+(?:the\s+)?firm", r"years\s+(?:of\s+)?(?:in\s+)?business",
                         r"operating\s+for", r"in\s+business\s+for", r"years\s+of\s+standing"),
    datatype="count",
)

# ----------------------------------------------------------------------------------------------
# Authority to sign
# ----------------------------------------------------------------------------------------------

# "Authorised Signatory" is the footer of every tax invoice, insurance schedule and vendor letter in
# a bid — nineteen of them in this corpus, on pages about cheque payment and jurisdiction clauses.
# It is a *support* at most. The instrument itself is what a requirement is asking for.
SIGNING_AUTHORITY = Concept(
    key="signing_authority",
    label="Power of attorney / board resolution",
    anchors=_rx(
        r"power\s+of\s+attorney", r"\bP\.?O\.?A\.?\b",
        r"board\s+resolution", r"resolution\s+(?:passed|adopted)",
        # The opening formula of every board resolution ever minuted.
        r"\bRESOLVED\s+(?:FURTHER\s+)?THAT\b",
        r"extract\s+of\s+the\s+(?:minutes|resolution)",
        r"attorney\s+holder", r"मुखत्यार",
    
        r"पॉवर\s*ऑफ\s*अॅ?टर्नी",
        r"मुखत्यारपत्र",
        r"अधिकारपत्र",
    ),
    supports=_rx(r"authori[sz]ed\s+signator", r"duly\s+authori[sz]ed", r"on\s+behalf\s+of\s+the\s+(?:company|firm)"),
    vetoes=_rx(
        r"cheque\s*/?\s*(?:DD|RTGS|NEFT)", r"subject\s+to\s+.{0,30}jurisdiction",
        r"computer\s+generated", r"terms\s*(?:&|and)\s*conditions\s*:",
        r"does\s+not\s+need\s+any\s+signature",
    ),
    documents=_rx(r"power\s+of\s+attorney", r"board\s+resolution"),
    requirement_cues=_rx(r"power\s+of\s+attorney", r"board\s+resolution", r"authori[sz]ed\s+signatory",
                         r"authori[sz]ing\s+the\s+signatory",
        r"पॉवर\s*ऑफ\s*अॅ?टर्नी",
        r"मुखत्यार",
    ),
    datatype="boolean",
)

# ----------------------------------------------------------------------------------------------
# Self-declarations. Each is a negative statement, so none of them may be suppressed by one.
# ----------------------------------------------------------------------------------------------

NOT_INSOLVENT = Concept(
    key="not_insolvent",
    label="Solvency self-declaration",
    anchors=_rx(
        r"\bnot\s+(?:be|been|being)?\s*(?:declared\s+|adjudged\s+)?(?:insolvent|bankrupt)\b",
        r"\bnot\s+in\s+receivership\b",
        r"neither\s+insolvent", r"no\s+(?:insolvency|bankruptcy|winding\s*-?\s*up)\s+proceeding",
        r"not\s+under\s+liquidation",
    ),
    supports=_rx(r"\binsolven", r"\breceivership\b", r"\bbankrupt", r"\bliquidat"),
    vetoes=_rx(r"should\s+not\s+be\s+insolvent", r"as\s+per\s+(?:the\s+)?section\s+\d"),
    documents=_rx(r"self\s*-?\s*declaration.{0,40}insolven", r"declaration\s+of\s+solvency"),
    requirement_cues=_rx(r"insolven", r"receivership", r"bankrupt", r"liquidation"),
    datatype="boolean",
    negations=None,
)

ANTI_COLLUSION = Concept(
    key="anti_collusion",
    label="Anti-collusion certificate",
    anchors=_rx(r"anti\s*-?\s*collusion", r"\bcollusion\b", r"not\s+colluded",
                r"without\s+collusion", r"\bcartel"),
    supports=_rx(r"bid\s+rigging", r"independent(?:ly)?\s+(?:prepared|arrived)", r"good\s+faith,?\s+without"),
    documents=_rx(r"anti\s*-?\s*collusion"),
    requirement_cues=_rx(r"collusion", r"anti\s*-?\s*collusion", r"bid\s+rigging"),
    datatype="boolean",
    negations=None,
)

NON_DISCLOSURE = Concept(
    key="non_disclosure",
    label="Non-disclosure agreement",
    anchors=_rx(r"non\s*-?\s*disclosure\s+agreement", r"\bN\.?D\.?A\.?\b",
                r"confidentiality\s+(?:agreement|undertaking)"),
    supports=_rx(r"confidential\s+information", r"shall\s+not\s+disclose"),
    vetoes=_rx(r"non-?disclosure\s+of\s+material\s+fact"),
    documents=_rx(r"non\s*-?\s*disclosure\s+agreement"),
    requirement_cues=_rx(r"non\s*-?\s*disclosure", r"confidentiality\s+agreement", r"\bNDA\b"),
    datatype="boolean",
    negations=None,
)

NO_DEVIATION = Concept(
    key="no_deviation",
    label="No-deviation certificate",
    anchors=_rx(r"no\s*-?\s*deviation", r"\bdeviations?\s*:?\s*(?:nil|none)\b",
                r"without\s+any\s+deviation", r"no\s+deviations?\s+(?:have|has|are)"),
    supports=_rx(r"strictly\s+in\s+accordance\s+with", r"\bdeviation"),
    documents=_rx(r"no\s*-?\s*deviation"),
    requirement_cues=_rx(r"deviation"),
    datatype="boolean",
    negations=None,
)

PAST_PERFORMANCE = Concept(
    key="past_performance",
    label="Past-performance declaration",
    anchors=_rx(
        r"no\s+(?:record|history)\s+of\s+(?:poor|unsatisfactory)",
        r"not\s+(?:been\s+)?(?:abandoned|terminated|rescinded)",
        r"no\s+(?:contract|work)\s+(?:has\s+been\s+)?terminated",
        r"not\s+made\s+(?:any\s+)?(?:misleading|false)\s+(?:representation|statement)",
    ),
    supports=_rx(r"poor\s+performance", r"abandon(?:ing|ed)\s+the\s+works?", r"misleading\s+or\s+false"),
    vetoes=_rx(r"record\s+for\s+poor\s+performance\s+such\s+as"),
    documents=_rx(r"past\s+performance", r"performance\s+declaration"),
    requirement_cues=_rx(r"poor\s+performance", r"abandoning\s+the\s+works?",
                         r"misleading\s+or\s+false\s+representation", r"terminated\s+contract"),
    datatype="boolean",
    negations=None,
)

NO_JOINT_VENTURE = Concept(
    key="no_joint_venture",
    label="No joint venture / consortium",
    anchors=_rx(r"joint\s+venture", r"\bJ\.?V\.?\b", r"\bconsortium\b",
                r"sub\s*-?\s*contract(?:ing)?\s+(?:is\s+)?not\s+(?:allowed|permitted)"),
    supports=_rx(r"sole\s+bidder", r"in\s+our\s+(?:own|individual)\s+capacity", r"proprietary\s+(?:firm|concern)"),
    documents=_rx(r"joint\s+venture", r"consortium"),
    requirement_cues=_rx(r"joint\s+venture", r"consortium", r"sub\s*-?\s*contracting"),
    datatype="boolean",
    negations=None,
)

# ----------------------------------------------------------------------------------------------
# Accounts, fees and labour registrations
# ----------------------------------------------------------------------------------------------

AUDITED_ACCOUNTS = Concept(
    key="audited_accounts",
    label="Audited accounts",
    anchors=_rx(
        r"balance\s+sheet", r"profit\s+(?:and|&)\s+loss\s+(?:account|statement|a/c)",
        r"audited\s+(?:financial\s+)?statements?", r"auditor'?s?\s+report",
        r"\bschedules?\s+to\s+the\s+(?:accounts|balance)",
    
        r"ताळेबंद",
        r"लेखापरीक्षित",
        r"नफा\s*(?:व|आणि)?\s*तोटा",
        r"सनदी\s*लेखापाल",
        r"चार्टर्ड\s*अकाउंटंट",
    ),
    supports=_rx(r"\bas\s+at\s+31(?:st)?[./-]\s*(?:03|march)", r"\bnotes?\s+to\s+(?:the\s+)?accounts",
                 r"chartered\s+accountants?", r"\bUDIN\b"),
    documents=_rx(r"balance\s+sheet", r"profit\s+(?:and|&)\s+loss", r"audited"),
    requirement_cues=_rx(r"balance\s+sheet", r"profit\s+(?:and|&)\s+loss", r"audited\s+(?:financial|total)",
                         r"audited\s+statements?",
        r"ताळेबंद",
        r"लेखापरीक्षित",
    ),
    datatype="boolean",
)

# "Tender Fee in ₹ : 6,579" on the MahaTender receipt that opens every bid. A separate payment from
# the EMD, and a requirement that asks for both is answered by two different numbers on one page.
TENDER_FEE = Concept(
    key="tender_fee",
    label="Tender fee",
    anchors=_rx(r"tender\s+fee", r"bid\s+document\s+fee", r"tender\s+document\s+(?:fee|cost)",
                r"(?:document|processing)\s+fee", r"निविदा\s*शु",
        r"निविदा\s*(?:शुल्क|फी)",
        r"निविदा\s*संच",
    ),
    supports=_rx(r"fee\s+in\s*₹", r"payment\s+(?:status|reference)", r"transaction\s+(?:id|no)"),
    # The EMD is the other payment on the same receipt and must not be read as the fee.
    vetoes=_rx(r"earnest\s+money", r"\bE\.?M\.?D\.?\b", r"security\s+deposit"),
    documents=_rx(r"tender\s+fee", r"bid\s+document\s+fee", r"payment\s+(?:slip|receipt)"),
    requirement_cues=_rx(r"tender\s+fee", r"bid\s+document\s+fee", r"tender\s+document\s+cost",
                         r"processing\s+fee",
        r"निविदा\s*(?:शुल्क|फी)",
    ),
)

PROVIDENT_FUND = Concept(
    key="provident_fund",
    label="EPF registration",
    identifier=re.compile(r"\b[A-Z]{2}[A-Z]{3}\d{7}\d{3}\b"),  # e.g. PUPUN1766515000
    anchors=_rx(r"employees'?\s+provident\s+fund", r"\bE\.?P\.?F\.?O?\b",
                r"provident\s+fund\s+code", r"\bEPF\s*(?:&|and)\s*MP\s+act",
        r"भविष्य\s*निर्वाह\s*निधी",
        r"भ\.?\s*नि\.?\s*नि",
    ),
    supports=_rx(r"code\s+number", r"establishment\s+is\s+registered", r"epfindia"),
    documents=_rx(r"provident\s+fund", r"\bEPFO?\b"),
    requirement_cues=_rx(r"\bEPF\b", r"provident\s+fund",
        r"भविष्य\s*निर्वाह",
    ),
    datatype="boolean",
)

# The one concept where a negation is the likeliest thing on the page: a bid's EPF intimation letter
# says "your establishment is not registered with ESIC", and the pages after it are a brochure about
# the ESI Act addressed to nobody. Both must fail to anchor.
EMPLOYEE_INSURANCE = Concept(
    key="employee_insurance",
    label="ESIC registration",
    identifier=re.compile(r"\b\d{2}\s*-?\s*\d{2}\s*-?\s*\d{6}\s*-?\s*\d{3}\s*-?\s*\d{4}\b"),
    anchors=_rx(r"employees'?\s+state\s+insurance", r"\bE\.?S\.?I\.?C\.?\b\s*(?:registration|code|certificate)",
                r"registered\s+with\s+ESIC"),
    supports=_rx(r"\bE\.?S\.?I\.?C?\.?\b", r"esic\.nic\.in"),
    # The brochure: generic guidance about the Act, not this bidder's registration.
    vetoes=_rx(
        r"esi\.?\s+act\s*,?\s*19\d{2}\s+is\s+applicable\s+to\s+all",
        r"(?:section|regulation)\s+[\d\-()a-z]+\s+of\s+the\s+esi",
        r"discharging\s+your\s+duties", r"authorized\s+to\s+accept\s+the\s+ESI",
        r"list\s+of\s+ESI\s+dispensaries", r"available\s+on\s+our\s+website",
        r"brochures?\s*/?\s*leaflets?", r"ensure\s*-?\s*to\s+insure",
        r"state\s+bank\s+of\s+india\s+are\s+authorized",
    ),
    documents=_rx(r"employees'?\s+state\s+insurance", r"ESIC\s+registration"),
    requirement_cues=_rx(r"\bESIC?\b", r"state\s+insurance"),
    datatype="boolean",
)

# ----------------------------------------------------------------------------------------------
# Experience, as a document rather than a measured value
# ----------------------------------------------------------------------------------------------

WORK_ORDER = Concept(
    key="work_order",
    label="Work order / letter of award",
    anchors=_rx(r"work\s+order", r"letter\s+of\s+(?:award|acceptance|intent)", r"\bL\.?O\.?[AI]\.?\b",
                r"signed\s+contract", r"purchase\s+order", r"कार्यादेश",
        r"कार्यादेश",
        r"कामाचा\s*आदेश",
        r"कार्यारंभ\s*आदेश",
    ),
    supports=_rx(r"work\s+order\s+no", r"agreement\s+no", r"awarded\s+to"),
    # A rate-analysis or measurement sheet quotes the work order it belongs to; it is not one.
    vetoes=_rx(
        r"work\s+portion\s+amount\s+from\s+work\s+order",
        r"before\s+work\s+order", r"rate\s+analysis",
    ),
    documents=_rx(r"work\s+order", r"letter\s+of\s+award"),
    requirement_cues=_rx(r"work\s+order", r"letter\s+of\s+award", r"signed\s+contract",
                         r"client\s+citation",
        r"कार्यादेश",
    ),
    datatype="boolean",
)

COMPLETION_CERTIFICATE = Concept(
    key="completion_certificate",
    label="Completion certificate",
    anchors=_rx(
        r"(?:work\s+)?completion\s+certificate", r"certificate\s+of\s+completion",
        r"certificate\s+in\s+respect\s+of\s+work\s+executed",
        r"performance\s+certificate", r"पूर्णत्वाचा?\s*दाखला",
    
        r"पूर्णत्वाचा?\s*दाखला",
        r"काम\s*पूर्ण\s*केल",
        r"समाधानकारक",
    ),
    supports=_rx(r"performance\s+(?:and\s+\w+\s+)?of\s+the\s+contractor\s+is\s+satisfactor",
                 r"work\s+(?:is\s+)?completed", r"satisfactor(?:y|ily)\s+completed"),
    vetoes=_rx(r"shall\s+(?:be\s+)?(?:submit|furnish|produce)", r"on\s+completion\s+of\s+the\s+work\s+the"),
    documents=_rx(r"completion\s+certificate", r"performance\s+certificate"),
    requirement_cues=_rx(r"completion\s+certificate", r"client\s+completion", r"performance\s+certificate",
        r"पूर्णत्व",
        r"काम\s*पूर्ण",
    ),
    datatype="boolean",
)

# Distinct from a head count: this asks whether *named roles* are staffed and with whom. "Project
# Lead", "UI UX/ Graphic Designer", "Database Administrator" (DBT RFP), "Project Manager 3 Solution
# Architect 3" (MahaIT marks table), "CV of Key Professionals (Appendix-8)" (PCMC).
KEY_ROLES = Concept(
    key="key_roles",
    label="Key personnel by role",
    anchors=_rx(
        r"\bC\.?V\.?s?\b", r"curriculum\s+vitae", r"r[eé]sum[eé]",
        r"project\s+(?:manager|lead|director)", r"solution\s+architect", r"team\s+lead",
        r"database\s+administrator", r"\bDBA\b", r"business\s+analyst",
        r"(?:UI\s*/?\s*UX|graphic)\s+designer", r"quality\s+(?:analyst|assurance)\s+(?:lead|engineer)",
        r"site\s+engineer", r"resident\s+engineer",
    
        r"अभियंता",
        r"प्रकल्प\s*व्यवस्थापक",
        r"स्थापत्य\s*अभियंता",
    ),
    supports=_rx(r"educational\s+qualification", r"years?\s+of\s+experience", r"\bdeputed?\b",
                 r"proposed\s+(?:team|personnel)"),
    vetoes=_rx(r"shall\s+(?:be\s+)?(?:deploy|depute|appoint|engage|provide)",
               r"will\s+be\s+(?:deployed|appointed|engaged)",
               # Every PMC certificate is signed by one of these, and a works tender names them on
               # page after page. "अधीक्षक अभियंता (यांत्रिकी) यांच्या योग्यता प्रमाणपत्राची
               # आवश्यकता" is about the officer who issues a certificate, not about the bidder's
               # engineers; counting him as proposed personnel staffs the bid from the municipality.
               r"(?:अधीक्षक|कार्यकारी|उप|शाखा|सहाय्?यक|उपअ?)\s*अभियंता",
               r"महापालिका|आयुक्त|खानेप्रमुख|कार्यालयाचा",
               r"(?:superintending|executive|deputy|assistant)\s+engineer"),
    documents=_rx(r"curriculum\s+vitae", r"\bC\.?V\.?\s+of\b", r"key\s+(?:professionals|personnel)",
                  r"proposed\s+team"),
    requirement_cues=_rx(r"\bCV\b", r"curriculum\s+vitae", r"key\s+(?:professionals|personnel)",
                         r"project\s+(?:manager|lead)", r"solution\s+architect",
                         r"database\s+administrator", r"designer", r"profile\s+total\s+marks",
        r"अभियंता",
        r"प्रकल्प\s*व्यवस्थापक",
    ),
    datatype="boolean",
)

MACHINERY = Concept(
    key="machinery",
    label="Plant and machinery",
    anchors=_rx(
        r"plant\s+(?:and|&)\s+machinery", r"machinery\s+(?:owned|available|held)",
        r"(?:list|details)\s+of\s+(?:plant|machinery|equipment)",
        r"equipment\s+(?:owned|available|held)", r"यंत्रसामग्री",
    
        r"यंत्रसामग्री",
        r"यंत्रे",
        r"अवजारे",
    ),
    supports=_rx(r"\bJCB\b", r"\bexcavator", r"\btipper", r"\broller\b", r"\btractor\b",
                 r"registration\s+(?:no|number).{0,20}vehicle", r"\bRC\s+book\b"),
    # A BOQ rate analysis prices machinery by the hour; an insurance or telematics letter names one.
    vetoes=_rx(
        r"\b(?:HR|hour)\b\s*$", r"rate\s+analysis", r"\bper\s+(?:hour|day)\b",
        r"livelink", r"\bpolicy\s+(?:no|period)\b", r"extra\s+item",
    ),
    documents=_rx(r"plant\s+(?:and|&)\s+machinery", r"list\s+of\s+machinery", r"machinery\s+statement"),
    requirement_cues=_rx(r"plant\s+(?:and|&)\s+machinery", r"machinery", r"equipment\s+(?:owned|required)",
        r"यंत्रसामग्री",
        r"यंत्रे",
    ),
    datatype="boolean",
)


ALL = ALL + (
    PAN, GST, MSME, FIRM_AGE, SIGNING_AUTHORITY,
    NOT_INSOLVENT, ANTI_COLLUSION, NON_DISCLOSURE, NO_DEVIATION, PAST_PERFORMANCE, NO_JOINT_VENTURE,
    AUDITED_ACCOUNTS, TENDER_FEE, PROVIDENT_FUND, EMPLOYEE_INSURANCE,
    WORK_ORDER, COMPLETION_CERTIFICATE, KEY_ROLES, MACHINERY,
)
BY_KEY = {c.key: c for c in ALL}

# ----------------------------------------------------------------------------------------------
# The bid's own paperwork
# ----------------------------------------------------------------------------------------------

# Every RFP asks for a set of signed letters whose names differ per tender — "Pre-Qualification Bid
# Covering letter", "Technical Bid Letter", "PRICE AND VALIDITY", "QUALIFYING DATA", "BID PRICE",
# "DEVIATIONS". They are all one thing: the covering letter the bidder signs, reproducing the
# tender's own form. The bid answers with the body of that form, which is why the anchors are the
# sentences a covering letter is made of rather than the headings a tender gives them.
BID_FORMS = Concept(
    key="bid_forms",
    label="Bid covering letter / declaration",
    anchors=_rx(
        r"(?:bid|tender|technical|pre\s*-?\s*qualification)\s+(?:bid\s+)?(?:covering\s+)?letter",
        r"covering\s+letter",
        r"having\s+read\s+and\s+examined",
        r"do\s+hereby\s+(?:propose|offer|submit|agree)",
        r"we\s+(?:hereby\s+)?(?:offer|propose|undertake)\s+to\s+(?:execute|provide|supply|carry)",
        r"prices?\s+and\s+validity",
        r"qualifying\s+data",
        r"\bbid\s+price\b",
        r"our\s+(?:bid|offer)\s+(?:shall\s+)?remain(?:s)?\s+valid",
    ),
    supports=_rx(r"authori[sz]ed\s+to\s+act\s+on\s+behalf", r"true,?\s+accurate,?\s+verifiable",
                 r"in\s+good\s+faith,?\s+without\s+collusion"),
    documents=_rx(r"covering\s+letter", r"bid\s+letter", r"tender\s+form"),
    requirement_cues=_rx(r"covering\s+letter", r"bid\s+letter", r"prices?\s+and\s+validity",
                         r"qualifying\s+data", r"\bbid\s+price\b", r"tender\s+form",
                         r"bid\s+submission\s+form"),
    datatype="boolean",
    negations=None,
)

# "General Details of the Bidder (Appendix-3)", "Registered office address", "Primary Contact Person
# (Name, Designation, address, mobile number, fax, email)". Anchored on the *labels* a bidder's
# particulars sheet prints, not on an address or an email — those are on every letterhead in the
# submission and would make this concept match everything.
BIDDER_PARTICULARS = Concept(
    key="bidder_particulars",
    label="Bidder particulars",
    anchors=_rx(
        r"(?:general|basic)\s+details\s+of\s+(?:the\s+)?bidder",
        r"registered\s+office\s+(?:address)?",
        r"(?:primary\s+)?contact\s+person",
        r"name\s+of\s+the\s+(?:bidder|firm|company|agency|tenderer)\s*:",
        r"correspondence\s+address",
        r"particulars\s+of\s+(?:the\s+)?(?:bidder|firm)",
        r"constitution\s+of\s+(?:the\s+)?(?:firm|bidder)",
    
        r"निविदाधारकाचे\s*नाव",
        r"ठेकेदाराचे\s*नाव",
        r"संपर्क\s*क्रमांक",
    ),
    supports=_rx(r"\bdesignation\b", r"mobile\s*(?:no|number)", r"\bfax\b", r"\be\s*-?\s*mail\b"),
    documents=_rx(r"details\s+of\s+(?:the\s+)?bidder", r"bidder'?s?\s+profile", r"appendix\s*-?\s*\d"),
    requirement_cues=_rx(r"details\s+of\s+(?:the\s+)?bidder", r"registered\s+office",
                         r"contact\s+person", r"correspondence\s+address",
                         r"constitution\s+of\s+(?:the\s+)?firm",
        r"निविदाधारकाचे\s*नाव",
        r"ठेकेदाराचे\s*नाव",
    ),
    datatype="boolean",
)

# An experience requirement worded as prose rather than as a counted threshold: "A Bidder should
# have an experience of having successfully completed Any One (01) of the following" (MahaIT),
# "List of completed/on-going assignments of similar nature" (PCMC), "Bidders Experience-Client
# Citations" (DBT). The *measured* form of this is read by `work_proof`, which returns values and
# dates; this covers the form that only asks whether the experience is evidenced at all.
PROJECT_EXPERIENCE = Concept(
    key="project_experience",
    label="Project experience",
    anchors=_rx(
        r"(?:relevant\s+)?(?:project|work)\s+experience",
        r"experience\s+of\s+having\s+(?:successfully\s+)?(?:completed|executed|implemented)",
        r"(?:list|details|statement)\s+of\s+(?:the\s+)?(?:completed|executed|similar|on\s*-?\s*going)",
        r"client\s+citations?",
        r"past\s+experience",
        r"(?:similar|comparable)\s+(?:works?|assignments?|projects?)\s+(?:executed|completed|carried)",
        r"works?\s+(?:executed|completed)\s+(?:during|in)\s+(?:the\s+)?last",
        r"details\s+for\s+work\s+undertaken",
    
        r"समकक्ष\s*काम",
        r"तत्सम\s*काम",
        r"त्याच\s*स्वरूपाच्या",
        r"तुल्यबळ",
        r"पूर्ण\s*केलेल्या\s*काम",
        r"कामाचा\s*अनुभव",
    ),
    supports=_rx(r"name\s+of\s+(?:the\s+)?(?:work|project|client)", r"agreement\s+(?:no|amount)",
                 r"date\s+of\s+(?:commencement|completion)", r"\bSTATEMENT\s*(?:NO\.?)?\s*[-–]?\s*(?:II|III)\b"),
    vetoes=_rx(r"shall\s+(?:submit|furnish|produce|enclose)", r"as\s+per\s+(?:the\s+)?format"),
    documents=_rx(r"experience", r"client\s+citation", r"list\s+of\s+works?", r"similar\s+works?"),
    requirement_cues=_rx(r"experience", r"client\s+citation", r"completed\s*/?\s*on\s*-?\s*going",
                         r"assignments?\s+of\s+similar", r"past\s+experience",
                         r"work\s+undertaken",
        r"अनुभव",
        r"समकक्ष",
        r"तत्सम",
    ),
    datatype="boolean",
)


ALL = ALL + (BID_FORMS, BIDDER_PARTICULARS, PROJECT_EXPERIENCE)
BY_KEY = {c.key: c for c in ALL}
