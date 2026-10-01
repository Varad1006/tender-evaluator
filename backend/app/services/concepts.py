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


def _rx(*alts: str) -> re.Pattern:
    return re.compile("|".join(alts), re.IGNORECASE)


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


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    anchors: re.Pattern
    supports: re.Pattern | None = None
    vetoes: re.Pattern | None = None
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
        """The cue this text states the fact under, or None. Veto wins."""
        if CHECKLIST.search(text):
            return None
        if self.vetoes is not None and self.vetoes.search(text):
            return None
        m = self.anchors.search(text)
        if m:
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
        r"उलाढाल",  # Marathi: turnover
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
    requirement_cues=_rx(r"\bnet\s*-?\s*worth\b", r"\bnetworth\b", r"positive\s+net\s*-?\s*worth"),
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
    anchors=_rx(r"\bISO\s*[/:]?\s*(?:IEC\s*)?\d{4,5}", r"\bCMMI\b", r"\bcapability\s+maturity\b"),
    supports=_rx(r"\bquality\s+management\s+system\b", r"\bcertificate\s+of\s+registration\b"),
    vetoes=_rx(r"\bshall\s+(?:submit|obtain|furnish)\b", r"\bis\s+required\s+to\s+(?:have|possess)\b"),
    documents=_rx(r"ISO\s*\d{4,5}", r"quality\s+management"),
    requirement_cues=_rx(r"\bISO\b", r"\bCMMI\b", r"quality\s+certificat"),
    datatype="boolean",
)

NON_BLACKLIST = Concept(
    key="non_blacklisting",
    label="Non-blacklisting declaration",
    anchors=_rx(
        r"\bnot\s+been\s+(?:black\s*-?\s*listed|blacklisted|debarred|banned)\b",
        r"\banti\s*-?\s*black\s*-?\s*listing\b",
        r"\bnon\s*-?\s*black\s*-?\s*listing\b",
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
    requirement_cues=_rx(r"black\s*-?\s*list", r"\bdebarr", r"non\s*-?\s*blacklisting"),
    datatype="boolean",
)

LEGAL_ENTITY = Concept(
    key="legal_entity",
    label="Legal entity / incorporation",
    anchors=_rx(
        r"\bcertificate\s+of\s+incorporation\b",
        r"\bcertificate\s+of\s+registration\b",
        r"\bC\.?I\.?N\.?\s*[:.]?\s*[A-Z]\d{5}[A-Z]{2}\d{4}",
        r"\bincorporated\s+under\b",
        r"\bpartnership\s+deed\b",
        r"\bLLP\s*I?N\b",
        r"\budyam\s+registration\b",
    ),
    supports=_rx(r"\bcompanies\s+act\b", r"\bLLP\s+act\b", r"\bregistrar\s+of\s+companies\b"),
    vetoes=_rx(r"should\s+be\s+an\s+entity\s+registered", r"sub\s*-?\s*contracting"),
    documents=_rx(r"incorporation", r"registration\s+certificate", r"partnership\s+deed"),
    requirement_cues=_rx(r"legal\s+entity", r"incorporat", r"registered\s+in\s+india"),
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
    text = f"{label}\n{requirement_text}"
    scored: list[tuple[int, Concept]] = []
    for concept in ALL:
        if concept.requirement_cues is None:
            continue
        hits = len(set(m.group(0).lower() for m in concept.requirement_cues.finditer(text)))
        if hits:
            # A count requirement and a currency requirement can share vocabulary ("manpower
            # strength 20" vs "turnover of 4 Cr"); the datatype the tender assigned breaks the tie.
            bonus = 1 if datatype and concept.datatype == datatype else 0
            scored.append((hits + bonus, concept))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    return scored[0][1]
