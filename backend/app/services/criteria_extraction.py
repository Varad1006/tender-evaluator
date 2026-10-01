"""Tender ingestion (FR1): extract a page-anchored criteria registry and tender metadata.

Deterministic first: each rule below recognises one kind of requirement from the wording of a
single sentence and parses its threshold with plain code. Every criterion therefore carries the
verbatim sentence and page it came from. An optional LLM pass can add criteria the rules do not
recognise, but its output is only accepted when the quoted sentence exists verbatim on the page
it claims (quote-then-extract).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from app.adapters.llm import LLMError
from app.adapters.pdf_parser import ParsedDocument, Sentence, quote_on_page
from app.services.values import (
    NUMBER_WORDS,
    format_inr,
    format_inr_short,
    parse_amounts,
    parse_durations,
)

log = logging.getLogger(__name__)

# What makes a sentence a requirement rather than a description. Marathi states the obligation as a
# verb ending rather than a modal — "सादर करणे आवश्यक आहे" (must be submitted), "भरावी लागेल" (shall
# have to be paid), "बंधनकारक" (binding) — so there is no single word to look for and the stems are
# matched instead. Without these every rule below refuses every Marathi sentence, which is why a
# wholly Marathi tender produced no criteria at all.
REQUIRE = re.compile(
    r"\b(shall|must|mandatory|required|should)\b"
    r"|आवश्यक|अनिवार्य|बंधनकारक|पाहिजे"
    r"|कराव[ीेा]|लागेल|राहील|असणे\s*आवश्यक|सादर\s*कर|जोडणे|भरावी",
    re.IGNORECASE)
GENERIC_NATURE_WORDS = {
    "installation", "installations", "supply", "commissioning", "work", "works", "project", "projects",
    "similar", "nature", "of", "the", "and", "a", "an", "value", "system", "systems",
}


@dataclass
class CriterionDraft:
    key: str
    label: str
    bucket: str
    datatype: str
    operator: str
    tier: str
    threshold_display: str
    source: Sentence | None  # None only for criteria derived from a standard, never for rule matches
    threshold_value: float | None = None
    threshold_unit: str | None = None
    params: dict = field(default_factory=dict)
    gating: bool = True
    extraction_method: str = "deterministic"  # deterministic | llm | pmc_standard | checklist
    status: str = "located"  # located | located_marathi | derived
    derivation: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    source_document_id: str | None = None


@dataclass
class TenderExtraction:
    title: str | None
    ref_no: str | None
    authority: str | None
    estimated_value: float | None
    issue_date: str | None
    award_mechanism: str
    award_detail: dict | None
    criteria: list[CriterionDraft]
    warnings: list[str] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)  # per-document type + readability summary


def _required(s: Sentence) -> bool:
    """The sentence states an obligation, itself or through the intro of the list it is an item of
    ("the tenderer shall … furnish the information detailed out below- / 2 Valid PF code …")."""
    return bool(REQUIRE.search(s.text) or (s.lead and REQUIRE.search(s.lead)))


def _count(word_or_digits: str) -> int | None:
    w = word_or_digits.lower()
    return int(w) if w.isdigit() else NUMBER_WORDS.get(w)


def _title(phrase: str) -> str:
    return " ".join(w if w.isupper() else w.capitalize() for w in phrase.split())


# --------------------------------------------------------------------------------------
# Requirement rules: Sentence -> CriterionDraft | None
# --------------------------------------------------------------------------------------

_MR_TURNOVER = re.compile(r"उलाढाल")
_MR_AVERAGE = re.compile(r"सरासरी")
_MR_MAX = re.compile(r"जास्तीत\s*जास्त|कमाल")


def rule_turnover(s: Sentence) -> CriterionDraft | None:
    t = s.text.lower()
    if ("turnover" not in t and not _MR_TURNOVER.search(s.text)) or not _required(s):
        return None
    amounts = parse_amounts(s.text)
    if not amounts:
        return None
    years_m = re.search(
        r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:(?:preceding|previous|last|consecutive)\s+)?(?:financial|fiscal)\s+years",
        s.text, re.IGNORECASE,
    )
    years = _count(years_m.group(1)) if years_m else None
    if years is None:
        # "मागील ५ वर्षातील", "गेल्या ५ (पाच) वर्षातील" — the window as Marathi states it.
        windows = parse_durations(s.text, unit="years")
        years = windows[0].value if windows else None
    average = "average" in t or bool(_MR_AVERAGE.search(s.text))
    label = "Minimum Average Annual Turnover" if average else "Minimum Annual Turnover"
    if years:
        label += f" (last {years} FY)"
    segment = re.search(r"\bfrom\s+(.{3,50}?)\s+only\b", s.text, re.I)
    if segment:  # e.g. RFPs ask for total turnover and, separately, turnover from one line of business
        label += f" — {_title(segment.group(1).lower())}"
    value = amounts[0].value
    return CriterionDraft(
        key="avg_turnover", label=label, bucket="Financial", datatype="currency", operator=">=",
        tier="deterministic", threshold_value=value, threshold_unit="INR",
        threshold_display=f"≥ {format_inr(value)}", params={"window_years": years, "average": average}, source=s,
    )


def rule_experience(s: Sentence) -> CriterionDraft | None:
    t = s.text.lower()
    if not re.search(r"\b(completed|executed)\b", t) or not re.search(r"\b(projects?|works?)\b", t):
        return None
    if not _required(s):
        return None
    amounts = parse_amounts(s.text)
    windows = parse_durations(s.text, unit="years")
    if not amounts or not windows:
        return None
    nature_m = re.search(
        r"at least\s+(?:one|two|three|\d+)\s+(?P<nature>[\w\s-]+?)\s+(?:projects?|works?)\b", s.text, re.IGNORECASE
    )
    nature_terms = []
    if nature_m:
        nature_terms = [w for w in nature_m.group("nature").lower().split() if w not in GENERIC_NATURE_WORDS and len(w) > 2]
    value, window = amounts[0].value, windows[0].value
    return CriterionDraft(
        key="similar_experience", label="Prior Similar Completed Project", bucket="Technical",
        datatype="composite", operator="similar_project", tier="llm_judge",
        threshold_value=value, threshold_unit="INR",
        threshold_display=f"≥ {format_inr_short(value)}, last {window} yrs",
        params={"window_years": window, "nature_terms": nature_terms}, source=s,
    )


def rule_gst(s: Sentence) -> CriterionDraft | None:
    if not re.search(r"\bGST\b", s.text) or not re.search(r"registration|certificate", s.text, re.IGNORECASE):
        return None
    if not _required(s):
        return None
    return CriterionDraft(
        key="gst_registration", label="Valid GST Registration", bucket="Eligibility", datatype="boolean",
        operator="exists", tier="deterministic", threshold_display="Required", params={"terms": ["GST"]}, source=s,
    )


# "PF code", "EPF", "Provident Fund"; "ESIC", "ESI", and the run-together "ESICertificate".
LABOUR_PARTS = {"EPF": r"\bE?PF\b|provident\s*fund", "ESIC": r"\bESIC?\b|\bESI(?=Certificate)|state\s*insurance"}


def rule_epf_esic(s: Sentence) -> CriterionDraft | None:
    parts = [p for p, pat in LABOUR_PARTS.items() if re.search(pat, s.text, re.IGNORECASE)]
    if not parts or not re.search(r"registration|certificate|code", s.text, re.IGNORECASE):
        return None
    if not _required(s):
        return None
    return CriterionDraft(
        key="epf_esic_registration", label=f"Valid {' / '.join(parts)} Registration", bucket="Eligibility",
        datatype="boolean", operator="exists_all" if len(parts) > 1 else "exists", tier="deterministic",
        threshold_display="Required" if len(parts) == 1 else "Both required", params={"parts": parts}, source=s,
    )


def rule_manufacturer(s: Sentence) -> CriterionDraft | None:
    if not re.search(r"manufacturer", s.text, re.IGNORECASE) or not re.search(r"authori[sz]ation", s.text, re.IGNORECASE):
        return None
    if not _required(s):
        return None
    obj = re.search(r"\bfor the\s+(.+?)\s+(?:proposed|to be|offered|being)\b", s.text, re.IGNORECASE)
    label = "Manufacturer Authorization"
    if obj:
        label += f" — {_title(obj.group(1))}"
    return CriterionDraft(
        key="manufacturer_authorization", label=label, bucket="Technical", datatype="boolean",
        operator="exists", tier="deterministic", threshold_display="Required", params={}, source=s,
    )


def rule_warranty(s: Sentence) -> CriterionDraft | None:
    if "warranty" not in s.text.lower() or not _required(s):
        return None
    years = parse_durations(s.text, unit="years")
    if not years:
        return None
    return CriterionDraft(
        key="warranty_years", label="Warranty Period Offered", bucket="Technical", datatype="number",
        operator=">=", tier="deterministic", threshold_value=years[0].value, threshold_unit="years",
        threshold_display=f"≥ {years[0].value} years", params={}, source=s,
    )


def rule_completion(s: Sentence) -> CriterionDraft | None:
    t = s.text.lower()
    if "within" not in t or not re.search(r"commission|complet|deliver", t) or not _required(s):
        return None
    days = parse_durations(s.text, unit="days")
    if not days:
        return None
    return CriterionDraft(
        key="completion_days", label="Delivery & Commissioning Timeline", bucket="Commercial", datatype="number",
        operator="<=", tier="deterministic", threshold_value=days[0].value, threshold_unit="days",
        threshold_display=f"≤ {days[0].value} days", params={}, source=s,
    )


def rule_site_engineer(s: Sentence) -> CriterionDraft | None:
    m = re.search(r"\b(certified|qualified|licensed|registered)\s+(?:(\w+)\s+)?engineer\b", s.text, re.IGNORECASE)
    if not m or not re.search(r"deploy|site|station", s.text, re.IGNORECASE) or not _required(s):
        return None
    discipline = (m.group(2) or "").lower()
    label = f"{m.group(1).capitalize()} {discipline.capitalize() + ' ' if discipline else ''}Engineer On-Site"
    return CriterionDraft(
        key="site_engineer", label=label, bucket="Technical", datatype="boolean", operator="exists",
        tier="deterministic", threshold_display="Required", params={"discipline": discipline}, source=s,
    )


_GENERIC_DOC = re.compile(
    r"\b(?:valid|copy of(?: the)?|self-attested copy of)\s+"
    r"(?P<name>[A-Z][\w:./&-]*(?:\s+[A-Za-z0-9][\w:./&-]*){0,4}?)\s+(?P<kind>certificate|registration|licen[cs]e)\b"
)


def rule_generic_document(s: Sentence) -> CriterionDraft | None:
    """Fallback: 'shall submit a valid <X> certificate' style requirements not covered above."""
    if not _required(s) or not re.search(r"\b(submit|furnish|attach|enclose|produce)\b", s.text, re.IGNORECASE):
        return None
    m = _GENERIC_DOC.search(s.text)
    if not m:
        return None
    name, kind = m.group("name").strip(), m.group("kind").capitalize()
    terms = [w for w in re.split(r"\s+", name) if len(w) > 1][:2]
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return CriterionDraft(
        key=f"doc_{slug}", label=f"Valid {name} {kind}", bucket="Eligibility", datatype="boolean",
        operator="exists", tier="deterministic", threshold_display="Required", params={"terms": terms}, source=s,
    )


def rule_net_worth(s: Sentence) -> CriterionDraft | None:
    if not re.search(r"positive\s+net\s*worth", s.text, re.I) or not _required(s):
        return None
    years_m = re.search(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:financial|fiscal)\s+years", s.text, re.I)
    years = _count(years_m.group(1)) if years_m else None
    return CriterionDraft(
        key="positive_net_worth", label="Positive Net Worth" + (f" (each of last {years} FY)" if years else ""),
        bucket="Financial", datatype="currency", operator=">=", tier="deterministic", threshold_value=0.01,
        threshold_unit="INR", threshold_display="> ₹0 each year", params={"window_years": years}, source=s,
    )


def rule_staff_strength(s: Sentence) -> CriterionDraft | None:
    # "…of minimum 30 IT professionals", "…of a minimum of 30 IT Professionals" (PMC DBT RFP p.15),
    # "…of at least 30 …": the article and the second "of" are optional decoration around the count.
    # The descriptor between the count and the noun is optional — "30 IT Professionals" names one,
    # "40 employees" does not, and requiring it dropped the second shape entirely.
    m = re.search(r"staff\s+strength\s+of\s+(?:a\s+)?(?:minimum|at\s+least|not\s+less\s+than)\s*(?:of\s+)?"
                  r"(\d+)\s+([A-Za-z ]{0,40}?)(?:professionals?|employees|staff)", s.text, re.I)
    if not m or not _required(s):
        return None
    n = int(m.group(1))
    return CriterionDraft(
        key="min_staff_strength", label=f"Minimum {m.group(2).strip()} Staff on Payroll".replace("  ", " "),
        bucket="Technical", datatype="number", operator=">=", tier="deterministic", threshold_value=n,
        threshold_unit="people", threshold_display=f"≥ {n} people", params={}, source=s,
    )


def rule_project_count(s: Sentence) -> CriterionDraft | None:
    m = re.search(r"executed\s+(?:a\s+)?minimum\s+(?:of\s+)?(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(.{3,60}?)projects?", s.text, re.I)
    if not m or not _required(s):
        return None
    count = _count(m.group(1)) or 1
    amounts = parse_amounts(s.text)
    windows = re.search(r"last\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:financial\s+)?years", s.text, re.I)
    window = _count(windows.group(1)) if windows else None
    value = amounts[0].value if amounts else None
    return CriterionDraft(
        key="min_similar_projects", label=f"Minimum {count} {_title(m.group(2).strip())} Projects".replace("  ", " "),
        bucket="Technical", datatype="composite", operator="similar_works_any", tier="llm_judge",
        threshold_value=value, threshold_unit="INR" if value else None,
        threshold_display=f"{count} × > {format_inr_short(value)}" + (f", last {window} yrs" if window else "") if value
        else f"{count} projects",
        params={"options": [{"count": count, "min_value": value}], "window_years": window}, source=s,
    )


def rule_existence(s: Sentence) -> CriterionDraft | None:
    m = re.search(r"existing\s+for\s+(?:the\s+)?(?:past|last)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\s+years", s.text, re.I)
    if not m or not _required(s):
        return None
    years = _count(m.group(1))
    return CriterionDraft(
        key="years_in_existence", label="Registered Entity Existing for Required Years", bucket="Eligibility",
        datatype="number", operator=">=", tier="deterministic", threshold_value=years, threshold_unit="years",
        threshold_display=f"≥ {years} years", params={}, source=s,
    )


def _context(s: Sentence) -> str:
    """A table cell's meaning often depends on its row ('v) Staff' | 'One B.E. Civil / DCE ... 5 Years')."""
    if s.in_table and s.row:
        return " ".join(s.row)
    return f"{s.lead} {s.text}" if s.lead and len(s.lead) < 120 else s.text  # "21. STAFF :- / a) One B.E. Civil …"


_ROLE = (r"(Project\s*Manager|Site\s*Engineer|Site\s*In-?charge|Resident\s*Engineer|Quality\s*Engineer"
         r"|Safety\s*Officer|Supervisor|Staff|Civil\s*Engineers?|Electrical\s*Engineers?|Engineers?)")
_QUAL = r"\b(B\.?\s*E\.?|B\.?\s*Tech|M\.?\s*E\.?|M\.?\s*Tech|Graduat\w*|Diploma|D\.?\s*C\.?\s*E\.?|Degree)(?![a-z])"


# Stationing people at the site is something the *contractor does during the work*, not something
# the *bidder must already be* to qualify. The two read almost alike — both name a role, a degree
# and a number of years — but only one is a gate, and a works volume states both: PMC drainage
# bundle 2286446 asks for "two nos of experienced Civil Engineers B.E (civil) at each site … 3
# years' experience of execution" in its execution clauses, while its qualification table asks for
# "Engineer B.E. Civil / DCE / Technical Staff … min. 5 Years". Reading the execution clause as the
# criterion sets the bar at 3 years when the tender requires 5, and passes a bidder the committee
# would reject.
_DEPLOYMENT = re.compile(
    r"\b(?:keep|depute|deploy|station|post|engage|appoint)\b[^.]{0,90}\bat\s+(?:each|every|the|all)?\s*sites?\b"
    r"|\bat\s+(?:each|every)\s+site\b"
    r"|\bon\s+(?:the\s+)?site\s+of\s+(?:the\s+)?works?\b",
    re.IGNORECASE,
)


def rule_key_personnel(s: Sentence) -> CriterionDraft | None:
    ctx = _context(s)
    if _DEPLOYMENT.search(ctx):
        return None
    role = re.search(_ROLE, ctx, re.I)
    years = re.search(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)['’]?\s*(?:of\s*)?(?:total\s*|relevant\s*)?experience"
                      r"|experience\s*(?:of\s*)?(?:min(?:imum)?\.?\s*)?(\d{1,2})\s*(?:years?|yrs?)", ctx, re.I)
    qual = re.search(_QUAL, ctx, re.I)
    if not role or not years or not (qual or re.search(r"experienced\s+\w*\s*engineers?", ctx, re.I)):
        return None
    if not (REQUIRE.search(ctx) or _required(s) or s.in_table or re.search(r"\bmust\s*have|having\b", ctx, re.I)):
        return None
    n_years = int(years.group(1) or years.group(2))
    discipline = re.search(r"\b(civil|electrical|mechanical)\b", ctx, re.I)
    age = re.search(r"age[^.]{0,50}?(?:not\s*be\s*more\s*than|not\s*exceed(?:ing)?|below|up\s*to|≤)\s*(\d{2})", ctx, re.I)
    count = re.search(r"\b(one|two|three|four|\d)\s*(?:nos?\.?\s*of\s*)?(?:experienced\s*)?(?:civil\s*|electrical\s*)?engineers?", ctx, re.I)
    role_name = _title(re.sub(r"\s+", " ", role.group(1)).lower()).rstrip("s")
    if role_name.lower() in ("engineer", "staff") and discipline:
        role_name = f"{discipline.group(1).capitalize()} Engineer"
    parts = []
    if qual:
        parts.append(re.sub(r"\s+", "", qual.group(1)).upper().replace("GRADUATION", "Graduate").replace("GRADUATE", "Graduate")
                     + (f" {discipline.group(1).capitalize()}" if discipline else ""))
    parts.append(f"≥ {n_years} yrs experience")
    if age:
        parts.append(f"age ≤ {age.group(1)}")
    if count and count.group(1).lower() not in ("one", "1"):
        parts.insert(0, f"{count.group(1)} nos")
    slug = re.sub(r"[^a-z0-9]+", "_", role_name.lower()).strip("_")
    return CriterionDraft(
        key=f"personnel_{slug}", label=f"Key Personnel — {role_name}", bucket="Technical", datatype="composite",
        operator="exists", tier="deterministic", threshold_display=", ".join(parts),
        params={"role": role_name, "min_years": n_years, "qualification": qual.group(1) if qual else None,
                "discipline": discipline.group(1).lower() if discipline else None,
                "max_age": int(age.group(1)) if age else None, "terms": [role_name.split()[0]]},
        source=s,
    )


def rule_machinery(s: Sentence) -> CriterionDraft | None:
    ctx = _context(s)
    if not re.search(r"machinery|equipment", ctx, re.I):
        return None
    if not re.search(r"own(?:ed)?\s*/\s*hire|owned\s*(?:or|/)\s*hired|hire\s*(?:purchase\s*)?agreement|on\s*owned|rental\s*basis", ctx, re.I):
        return None
    stamp = re.search(r"stamp\s*paper", ctx, re.I)
    notarised = re.search(r"notari[sz]ed", ctx, re.I)
    display = "Owned or hired" + ("; hire agreement on ₹100 stamp paper" if stamp else "; hire agreement") \
        + (", notarised" if notarised else "")
    return CriterionDraft(
        key="plant_machinery", label="Plant & Machinery Available (owned / hired)", bucket="Technical",
        datatype="boolean", operator="exists", tier="deterministic", threshold_display=display,
        params={"terms": ["machinery", "hire"]}, source=s,
    )


RULES: list[Callable[[Sentence], CriterionDraft | None]] = [
    rule_gst, rule_epf_esic, rule_completion, rule_turnover, rule_experience,
    rule_manufacturer, rule_warranty, rule_site_engineer,
    rule_net_worth, rule_staff_strength, rule_project_count, rule_existence,
    rule_key_personnel, rule_machinery,
]

# Rules that may legitimately fire more than once per tender (e.g. an RFP asking for total
# turnover and, separately, turnover from one line of business).
MULTI = {"avg_turnover"}


# --------------------------------------------------------------------------------------
# Tender metadata
# --------------------------------------------------------------------------------------

def _metadata(doc: ParsedDocument) -> dict:
    front = "\n".join(doc.page_text(n) for n in range(1, min(3, doc.page_count) + 1))
    ref = re.search(r"Tender\s+(?:No|Ref(?:erence)?(?:\s+No)?)\.?\s*:?\s*([A-Z0-9][A-Z0-9/\-_.]*[A-Z0-9])", front)
    title = re.search(r"Name of (?:the )?Work\s*:?\s*(.+)", front)
    issue = re.search(r"Date of Issue\s*:?\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})", front, re.IGNORECASE)

    authority = None
    headings = doc.pages[0].headings if doc.pages else []
    org = next((h for h in headings if re.search(r"corporation|authority|council|board|nigam|parishad|municipal", h, re.I)), None)
    if org:
        authority = _title(org.lower())
        dept = next((h for h in headings if h != org and re.search(r"department", h, re.I)), None)
        if dept:
            authority += f" — {_title(dept.lower())}"

    estimated = None
    for line in front.split("\n"):
        if re.search(r"estimated\s+(cost|value)", line, re.IGNORECASE):
            amounts = parse_amounts(line)
            if amounts:
                estimated = amounts[0].value
                break

    return {
        "ref_no": ref.group(1) if ref else None,
        "title": title.group(1).strip() if title else None,
        "issue_date": issue.group(1) if issue else None,
        "authority": authority,
        "estimated_value": estimated,
    }


# --------------------------------------------------------------------------------------
# LLM supplement (optional)
# --------------------------------------------------------------------------------------

LLM_CRITERIA_PROMPT = """You are extracting bidder eligibility and qualification requirements from a government tender.
Already extracted (do NOT repeat these): {existing}

Return a JSON array of ADDITIONAL mandatory requirements a bidder must meet. For each item:
{{"label": short name, "bucket": "Financial"|"Technical"|"Eligibility"|"Commercial",
  "datatype": "currency"|"number"|"boolean", "operator": ">="|"<="|"exists",
  "threshold_value": number or null, "threshold_unit": "INR"|"years"|"days"|other or null,
  "search_terms": [1-3 keywords a bidder document would use],
  "page": page number, "quote": the exact sentence from that page, copied verbatim}}
Only include requirements that are explicit in the text. Return [] if there are none.

TENDER TEXT:
{pages}"""


def _pages_block(doc: ParsedDocument, limit: int = 120_000) -> str:
    out, size = [], 0
    for p in doc.pages:
        chunk = f"=== PAGE {p.number} ===\n{p.text}\n"
        if size + len(chunk) > limit:
            break
        out.append(chunk)
        size += len(chunk)
    return "".join(out)


def llm_supplement(llm, doc: ParsedDocument, existing: list[CriterionDraft]) -> tuple[list[CriterionDraft], list[str]]:
    prompt = LLM_CRITERIA_PROMPT.format(existing=", ".join(c.label for c in existing) or "none", pages=_pages_block(doc))
    try:
        items = llm.generate_json("extraction_default", prompt)
    except LLMError as exc:
        log.warning("LLM criteria supplement failed: %s", exc)
        return [], ["Some criteria may need manual entry (LLM-assisted extraction unavailable)."]

    drafts, rejected = [], 0
    used_quotes = {c.source.text for c in existing if c.source}
    for item in items if isinstance(items, list) else []:
        try:
            page, quote = int(item["page"]), str(item["quote"]).strip()
            operator = item["operator"] if item["operator"] in (">=", "<=", "exists") else None
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if not operator or quote in used_quotes or not quote_on_page(quote, doc, page):
            rejected += 1
            continue
        label = str(item.get("label") or "Requirement")[:120]
        threshold = item.get("threshold_value")
        unit = item.get("threshold_unit")
        if operator == "exists":
            display = "Required"
        elif unit == "INR" and threshold is not None:
            display = f"{'≥' if operator == '>=' else '≤'} {format_inr(float(threshold))}"
        else:
            display = f"{'≥' if operator == '>=' else '≤'} {threshold} {unit or ''}".strip()
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40]
        drafts.append(CriterionDraft(
            key=f"llm_{slug}", label=label, bucket=item.get("bucket") if item.get("bucket") in
            ("Financial", "Technical", "Eligibility", "Commercial") else "Eligibility",
            datatype=item.get("datatype", "boolean"), operator=operator, tier="deterministic",
            threshold_value=float(threshold) if threshold is not None and operator != "exists" else None,
            threshold_unit=unit, threshold_display=display,
            params={"terms": [str(t) for t in item.get("search_terms", [])][:3]},
            source=Sentence(page=page, text=quote, section="", clause=None),
            extraction_method="llm",
        ))
        used_quotes.add(quote)
    warnings = [f"{rejected} LLM-suggested criteria were discarded because their quote could not be verified on the cited page."] if rejected else []
    return drafts, warnings


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------

def extract_tender(doc: ParsedDocument, llm=None) -> TenderExtraction:
    """Single-PDF tender (kept for the seeded demo and simple uploads); bundles use bundle_extraction."""
    from app.services.bundle_extraction import extract_single

    return extract_single(doc, llm=llm)
