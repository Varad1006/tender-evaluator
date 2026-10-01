"""The reading stage: a model finds the figure, and this module refuses to believe it on its word.

Deterministic finders locate every page that could carry a fact and every figure on it. What they
cannot do is decide which of nine candidate figures on a chartered accountant's certificate is the
one the requirement asks for, when the certificate calls it something the requirement never says.
That judgement is what a model is for, and it is the only thing this stage asks one to do.

Everything a model returns is treated as a claim about the submission, and checked against the
submission before it is used:

  1. **It may only cite evidence it was given.** The prompt carries numbered spans pulled from the
     bid by the finders; a reply naming anything else is discarded. A page number is never asked
     for, so one can never be invented — the page comes from the span the model selected.
  2. **Its quote must be in that span, verbatim.** Checked by substring on normalised whitespace.
     This is the gate that makes a fabricated figure impossible to cite: there is nowhere for it to
     come from.
  3. **It never does arithmetic.** It reports the figure as printed ("407") and names the unit the
     document states ("lakhs"); this module multiplies. A model that cannot multiply cannot produce
     ₹407 crore from ₹4.07 crore, and the one case where a unit error is catastrophic is closed.
  4. **The comparison stays in Python.** The model supplies a value, never a verdict. Pass and
     not-matched are decided by `>=` on numbers, which is what lets the result say "deterministic
     check — no AI in the comparison" truthfully.
  5. **A negative has to be confirmed twice.** Before `not_matched` is issued, the reading is put
     to a second, stronger model. Disagreement yields `unknown`, never a shortfall. A bidder wrongly
     disqualified is the worst output this system can produce, and this is where that is paid for.

A reply that fails any gate is not retried into compliance: the criterion comes back `unknown` with
the reason recorded, which is the honest answer when the submission could not be read.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.adapters.llm import LLMError
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services.values import format_inr, words_to_amount

log = logging.getLogger(__name__)

# Enough candidates to hold the answer, few enough to keep a bidder's reading under a cent. The
# finders rank by specificity, so the figure the requirement wants is near the top when present.
MAX_SPANS = 14
MAX_SPAN_CHARS = 320
MIN_CONFIDENCE = 0.60

_UNITS = {"rupees": 1.0, "rupee": 1.0, "none": 1.0, "": 1.0, "unit": 1.0,
          "thousand": 1_000.0, "thousands": 1_000.0,
          "lakh": 100_000.0, "lakhs": 100_000.0, "lac": 100_000.0, "lacs": 100_000.0,
          "crore": 10_000_000.0, "crores": 10_000_000.0, "million": 1_000_000.0}


@dataclass
class Reading:
    """One figure, read from one span, with everything needed to show the committee why."""

    value: float | None
    raw_figure: str
    unit: str
    quote: str
    page: int
    year: int | None
    confidence: float
    reasoning: str
    model: str
    claim: Claim | None = None


@dataclass
class ReadingOutcome:
    readings: list[Reading] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    found: bool = False
    model: str = ""

    @property
    def ok(self) -> bool:
        return self.found and bool(self.readings)


PROMPT = """You are reading one bidder's tender submission to locate a single fact. You are not \
deciding whether the bidder qualifies.

THE FACT WANTED: {concept_label}
THE TENDER'S REQUIREMENT, VERBATIM: "{requirement}"

The requirement is worded by the tender authority. The submission is worded by the bidder and will \
NOT use the same words. "On-roll staff strength of IT Professionals" may appear as "technically \
qualified professionals on the company's payroll"; "annual turnover" may appear as "Revenue from \
Operations", "Gross Receipts" or an unlabelled column of a certificate. Match on meaning.

CANDIDATE EVIDENCE, taken verbatim from the submission:
{spans}

Return JSON only:
{{"found": true|false,
  "items": [{{"id": <evidence id you used>,
              "quote": "<exact substring of that evidence, copied character for character>",
              "raw_figure": "<the figure exactly as printed there, e.g. 512 or 4,12,35,600>",
              "unit": "rupees|thousand|lakh|crore|none",
              "year": <financial year it starts in, or null>,
              "confidence": 0.0-1.0}}],
  "reasoning": "<one short sentence>"}}

Rules you must follow:
- Use ONLY the evidence above. If none of it states {concept_label}, return {{"found": false, \
"items": [], "reasoning": "..."}}.
- "quote" must be copied from the evidence exactly. Do not tidy, reword, or join separated text.
- "raw_figure" must appear inside your own "quote".
- Do NOT convert, add, average or compute anything. Report the figure as printed and name its unit.
- "unit" is what the DOCUMENT says the figure is in (a heading such as "Amt (Rs. in lakhs)" makes \
it "lakh"). If the figure is already a full rupee amount, use "rupees". {count_note}
- One item per financial year when the evidence states a year-by-year series. Otherwise one item.
"""

_COUNT_NOTE = 'This fact is a count of people, not money: use "none" as the unit.'


def _spans(claims: list[Claim]) -> list[Claim]:
    """Candidate evidence, de-duplicated, most specific first."""
    seen: set[str] = set()
    out: list[Claim] = []
    for c in sorted(claims, key=lambda c: (c.detail.get("source") != "table", c.page)):
        key = re.sub(r"\s+", " ", c.quote).strip().lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= MAX_SPANS:
            break
    return out


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _digits(text: str) -> str:
    return re.sub(r"[^\d]", "", text or "")


def _verify(item: dict, span: Claim, want_count: bool) -> tuple[Reading | None, str | None]:
    """A reply item turned into a Reading, or a refusal saying which gate it failed."""
    quote = str(item.get("quote") or "")
    raw = str(item.get("raw_figure") or "").strip()
    if not quote or not raw:
        return None, "reply named no quote or no figure"

    # Gate 2: the quote has to be in the span the model selected.
    if _normalise(quote) not in _normalise(span.quote):
        return None, f"quoted text is not on page {span.page}: {quote[:60]!r}"

    # Gate: the figure has to be inside the model's own quote.
    if _digits(raw) and _digits(raw) not in _digits(quote):
        return None, f"figure {raw!r} does not appear in the quoted text"

    unit_word = str(item.get("unit") or "").strip().lower().rstrip(".")
    if want_count:
        unit_word = "none"
    if unit_word not in _UNITS:
        return None, f"unit {unit_word!r} is not one of {sorted(set(_UNITS))}"

    # Gate 3: the multiplication is ours.
    bare = re.sub(r"[,\s₹]|(?:rs\.?|inr)|/-", "", raw, flags=re.IGNORECASE)
    try:
        number = float(bare)
    except ValueError:
        number = words_to_amount(raw) or float("nan")
        if number != number:  # NaN
            return None, f"figure {raw!r} is not a number"
    value = number * _UNITS[unit_word]

    confidence = float(item.get("confidence") or 0)
    if confidence < MIN_CONFIDENCE:
        return None, f"confidence {confidence:.2f} below {MIN_CONFIDENCE:.2f}"

    year = item.get("year")
    year = int(year) if isinstance(year, (int, float)) and 1990 < int(year) < 2100 else None
    return Reading(value=value, raw_figure=raw, unit=unit_word, quote=quote, page=span.page,
                   year=year, confidence=confidence, reasoning="", model="", claim=span), None


def read(spec: CriterionSpec, concept, claims: list[Claim], requirement: str, llm,
         role: str = "reader") -> ReadingOutcome:
    """Ask a model which of the candidate spans states the fact, and verify every word of the reply."""
    out = ReadingOutcome()
    spans = _spans(claims)
    if not spans or llm is None:
        return out
    want_count = (getattr(concept, "datatype", "") == "count") or spec.threshold_unit not in (None, "INR")
    listing = "\n".join(
        f"  [{i}] (page {c.page}) {re.sub(r'[ \t]+', ' ', c.quote)[:MAX_SPAN_CHARS]}"
        for i, c in enumerate(spans))
    prompt = PROMPT.format(
        concept_label=getattr(concept, "label", spec.label), requirement=requirement[:600],
        spans=listing, count_note=_COUNT_NOTE if want_count else "")
    try:
        reply = llm.generate_json(role, prompt)
    except LLMError as exc:
        out.refusals.append(f"reading stage unavailable: {exc}")
        return out
    out.model = getattr(llm, "last_model", "") or role
    if not isinstance(reply, dict):
        out.refusals.append("reply was not a JSON object")
        return out
    out.found = bool(reply.get("found"))
    reasoning = str(reply.get("reasoning") or "")[:300]
    for item in (reply.get("items") or [])[:12]:
        if not isinstance(item, dict):
            continue
        try:
            span = spans[int(item.get("id"))]
        except (TypeError, ValueError, IndexError):
            out.refusals.append(f"reply cited evidence {item.get('id')!r}, which was not supplied")
            continue
        reading, refusal = _verify(item, span, want_count)
        if reading is None:
            out.refusals.append(refusal or "unverifiable")
            continue
        reading.reasoning = reasoning
        reading.model = out.model
        out.readings.append(reading)
    if out.readings:
        out.found = True
    return out


# ----------------------------------------------------------------------------------------------
# What the requirement does with the figures it asked for
# ----------------------------------------------------------------------------------------------

_EACH = re.compile(r"\b(?:in\s+each|each\s+of|every\s+(?:one\s+of\s+)?the|all\s+(?:of\s+)?the)\b", re.IGNORECASE)
_AVERAGE = re.compile(r"\baverage\b|\bmean\b", re.IGNORECASE)


def aggregation(spec: CriterionSpec, requirement: str) -> str:
    """average | each | best — how a year-by-year series is measured against one threshold.

    Read off the requirement's own words, so it is the same decision for any tender that words it
    the same way: "an average annual turnover of at least ₹4 Cr over the last three years"
    averages; "a positive net worth in each of the last three years" takes the worst year;
    anything else lets the best year answer, which is how an experience or maximum-turnover clause
    works and is the reading that cannot wrongly disqualify.
    """
    if spec.params.get("average") or _AVERAGE.search(requirement):
        return "average"
    if _EACH.search(requirement):
        return "each"
    return "best"


def measure(readings: list[Reading], how: str) -> tuple[float | None, str, list[Reading]]:
    """(value compared, how it was arrived at, the readings it rests on)."""
    byyear: dict[int, Reading] = {}
    for r in readings:
        if r.value is None:
            continue
        if r.year is None:
            byyear.setdefault(-len(byyear) - 1, r)  # unyeared figures keep their own slots
        elif r.year not in byyear or (r.value or 0) > (byyear[r.year].value or 0):
            byyear[r.year] = r
    usable = [r for r in byyear.values() if r.value is not None]
    if not usable:
        return None, "", []
    if how == "average" and len(usable) > 1:
        total = sum(r.value or 0 for r in usable)
        years = sorted(r.year for r in usable if r.year is not None)
        span = f"FY {years[0]}-{str(years[-1] + 1)[-2:]}" if years else f"{len(usable)} figures"
        return total / len(usable), f"average of {len(usable)} years ({span})", usable
    if how == "each":
        worst = min(usable, key=lambda r: r.value or 0)
        return worst.value, f"lowest of {len(usable)} year(s)", usable
    best = max(usable, key=lambda r: r.value or 0)
    return best.value, "best figure stated", [best]
