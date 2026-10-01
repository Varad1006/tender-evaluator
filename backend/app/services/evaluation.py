"""Verdict engine (FR4/FR5) with dual citation.

Rules carried over from the bidder-side tool:
  * Deterministic first: every arithmetic, date or presence check is plain code. The LLM is only
    consulted for the qualitative half of a `llm_judge` criterion ("is this project similar?").
  * unknown != fail: no evidence -> `unknown` (not_found); contradictory evidence -> `unknown`
    (conflict) with *both* citations kept; an unconfident judgement -> `unknown` (low_confidence).
  * Every criterion is evaluated for every bidder, even after a gating failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from app.adapters.llm import LLMError
from app.services import experience_ladder, indexation
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services.values import format_inr, format_inr_short

log = logging.getLogger(__name__)


@dataclass
class VerdictResult:
    criteria_key: str
    verdict: str  # pass | fail | unknown
    tier: str  # deterministic | ai_assisted | rule_based
    reason: str
    evidence: list[Claim]
    computation: str | None = None
    unknown_kind: str | None = None  # not_found | conflict | low_confidence | unparseable
    extra: dict = field(default_factory=dict)


def _incomplete(claims: list[Claim]) -> list[str]:
    """Figures the extractor refused as unreadable on the same certificate as the ones it read."""
    out: list[str] = []
    for c in claims:
        if c.detail.get("series_incomplete"):
            out += [str(x) for x in (c.detail.get("unreadable") or [])]
    return sorted(set(out))


def _cannot_fail(spec: CriterionSpec, claims: list[Claim], best: float, unreadable: list[str]) -> VerdictResult:
    """A shortfall measured over part of a series is not a shortfall.

    When a certificate states five years and the text layer mangled two of them, the largest of the
    three we could read is not the bidder's maximum — and failing them on it would disqualify a
    company for a scanning artefact.
    """
    return VerdictResult(
        spec.key, "unknown", "deterministic", unknown_kind="unparseable", evidence=claims[:3],
        reason=f"The best figure that could be read is {_fmt(spec, best)}, below the required "
               f"{_fmt(spec, spec.threshold_value)} — but {len(unreadable)} further figure(s) on the same "
               f"document could not be read ({'; '.join(unreadable[:2])}). One of them may be the "
               "qualifying year, so this is unknown rather than a failure.",
        extra={"unreadable": unreadable},
    )


def _fmt(spec: CriterionSpec, value: float) -> str:
    if spec.threshold_unit == "INR":
        return format_inr(value)
    unit = spec.threshold_unit or ""
    return f"{value:g} {unit}".strip()


def _not_found(spec: CriterionSpec, what: str | None = None) -> VerdictResult:
    return VerdictResult(
        criteria_key=spec.key, verdict="unknown", tier="deterministic", unknown_kind="not_found", evidence=[],
        reason=f"Not found in submission — no evidence for {what or spec.label.lower()} was located on any page. "
               "Treated as unknown, not as a failure.",
    )


def _numeric(spec: CriterionSpec, claims: list[Claim], reference_year: int | None = None) -> VerdictResult:
    # Evidence the finder marked as not comparable is a figure computed on terms this tender does
    # not use — a bid capacity worked out on another authority's formula, period or price base. It
    # is a number, so it would compare; comparing it would be arithmetic on the wrong question.
    blocked = [c for c in claims if c.detail.get("not_comparable")]
    claims = [c for c in claims if not c.detail.get("not_comparable")]
    usable = [c for c in claims if c.value_numeric is not None]
    if not usable and blocked:
        reasons = sorted({d for c in blocked for d in (c.detail.get("defects") or [])})
        return VerdictResult(
            spec.key, "unknown", "deterministic", unknown_kind="not_found", evidence=blocked[:2],
            reason="The figure submitted cannot be compared against this tender's requirement: it "
                   + "; ".join(reasons)
                   + ". Treated as unknown, not as a failure — the bidder may well qualify on a "
                     "certificate drawn to this tender's terms.",
            extra={"defects": reasons},
        )
    if not usable:
        if claims:
            return VerdictResult(spec.key, "unknown", "deterministic", "A value was found but could not be read as a number.",
                                 claims, unknown_kind="unparseable")
        return _not_found(spec)

    # "Maximum turnover in any of the last five years" is answered by a *series* of figures, one
    # per year. Five different numbers are what the tender asked for, not a contradiction — and
    # the winner is decided after indexation, because an older, smaller year can overtake a newer
    # one once it is brought to current rupees.
    if spec.params.get("basis") == "max_annual" and usable:
        # "during the last five years": a year before the window cannot be the qualifying year. The
        # window is taken one year generously (a bid early in a financial year is certified on the
        # five years to the last audited March), so no bidder fails on the boundary.
        window = spec.params.get("window_years")
        if window and reference_year:
            inside = [c for c in usable if c.detail.get("year") is None or c.detail["year"] >= reference_year - window - 1]
            dropped = [c for c in usable if c not in inside]
            usable = inside or usable
        else:
            dropped = []
    if spec.params.get("basis") == "max_annual" and len(usable) > 1:
        table = indexation.table_from_params(spec.params)
        scored = [(c, indexation.escalate(c.value_numeric, c.detail.get("year"), table)) for c in usable]
        best_claim, best = max(scored, key=lambda cs: cs[1].value if cs[1] else 0)
        threshold = spec.threshold_value
        if threshold is None:
            return VerdictResult(spec.key, "unknown", "deterministic", "Tender threshold could not be read.", usable,
                                 unknown_kind="unparseable")
        ok = best.value >= threshold
        unreadable = _incomplete(usable)
        if not ok and unreadable:
            return _cannot_fail(spec, usable, best.value, unreadable)
        detail = best.explain() if best.applied else _fmt(spec, best.value)
        return VerdictResult(
            spec.key, "pass" if ok else "fail", "deterministic", evidence=[best_claim],
            computation=f"{_fmt(spec, best.value)} {'≥' if ok else '<'} {_fmt(spec, threshold)}",
            reason=f"Best of {len(usable)} year(s) stated: {detail}, against a requirement of ≥ {_fmt(spec, threshold)}."
                   + (" The tender's multiplying factors were applied first." if best.applied else ""),
            extra={"years_considered": len(usable), "indexed": best.applied},
        )

    distinct = sorted({c.value_numeric for c in usable})
    if len(distinct) > 1:
        stated = " and ".join(f"{_fmt(spec, c.value_numeric)} (p.{c.page})" for c in usable)
        return VerdictResult(
            spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=usable,
            reason=f"The submission states {stated} — conflicting figures within one document.",
        )

    value, threshold = distinct[0], spec.threshold_value
    if threshold is None:
        return VerdictResult(spec.key, "unknown", "deterministic", "Tender threshold could not be read.", usable,
                             unknown_kind="unparseable")
    ok = value >= threshold if spec.operator == ">=" else value <= threshold
    unreadable = _incomplete(usable)
    if not ok and unreadable:
        return _cannot_fail(spec, usable, value, unreadable)
    symbol = ("≥" if ok else "<") if spec.operator == ">=" else ("≤" if ok else ">")
    computation = f"{_fmt(spec, value)} {symbol} {_fmt(spec, threshold)}"
    return VerdictResult(
        spec.key, "pass" if ok else "fail", "deterministic", evidence=usable[:1] if len(usable) > 1 else usable,
        computation=computation,
        reason=f"Stated {_fmt(spec, value)} against a requirement of {'≥' if spec.operator == '>=' else '≤'} {_fmt(spec, threshold)}.",
    )


def _exists(spec: CriterionSpec, claims: list[Claim]) -> VerdictResult:
    if not claims:
        return _not_found(spec)
    return VerdictResult(spec.key, "pass", "deterministic", evidence=claims[:1], computation="Document present",
                         reason=f"Evidence located: {claims[0].value}.")


def _exists_all(spec: CriterionSpec, claims: list[Claim]) -> VerdictResult:
    parts = spec.params.get("parts", [])
    by_part = {p: [c for c in claims if c.detail.get("part") == p] for p in parts}
    missing = [p for p, cs in by_part.items() if not cs]
    evidence: list[Claim] = []
    for cs in by_part.values():
        if cs and cs[0] not in evidence:
            evidence.append(cs[0])
    if missing:
        found = ", ".join(f"{p} on p.{cs[0].page}" for p, cs in by_part.items() if cs)
        return VerdictResult(
            spec.key, "unknown", "deterministic", unknown_kind="not_found", evidence=evidence,
            reason=f"{' and '.join(missing)} registration not found in submission"
                   + (f" ({found} was found)" if found else "")
                   + ". Treated as unknown, not as a failure — seek clarification.",
            extra={"missing_parts": missing},
        )
    return VerdictResult(spec.key, "pass", "deterministic", evidence=evidence,
                         computation=" + ".join(f"{p} ✓" for p in parts),
                         reason="All required registrations located: " + "; ".join(c.value for c in evidence) + ".")


JUDGE_PROMPT = """A tender requires past experience of this nature: "{requirement}".
A bidder states: "{claim}"
Is the bidder's project of similar nature to what the tender requires? Consider only the nature of
work, not its value or date. Return JSON: {{"similar": true|false, "confidence": 0.0-1.0, "rationale": one sentence}}"""


def _judge_nature(spec: CriterionSpec, claim: Claim, requirement_quote: str, llm) -> tuple[str, str, str]:
    """Returns (outcome, tier, rationale) where outcome is similar | not_similar | uncertain."""
    if llm is not None:
        try:
            out = llm.generate_json("judge", JUDGE_PROMPT.format(requirement=requirement_quote, claim=claim.quote))
            confidence = float(out.get("confidence", 0))
            rationale = str(out.get("rationale", ""))[:300]
            if confidence < 0.7:
                return "uncertain", "ai_assisted", rationale or "Low-confidence judgement."
            return ("similar" if out.get("similar") else "not_similar"), "ai_assisted", rationale
        except (LLMError, AttributeError, TypeError, ValueError) as exc:
            log.warning("Judge failed, falling back to rule-based: %s", exc)
    description = claim.detail.get("description")
    if description:
        # A work certificate's own description, judged against the tender's nature by work family
        # (road, drainage, building, ...), in English or Marathi.
        from app.services.nature import judge

        outcome, why = judge(str(spec.params.get("nature") or ""), str(spec.tender.get("title") or ""), description)
        return outcome, "rule_based", why
    terms = claim.detail.get("nature_terms") or []
    hits = claim.detail.get("nature_hits") or []
    if not terms:
        return "uncertain", "rule_based", "Tender does not name the nature of work precisely enough for a rule-based check."
    if len(hits) == len(terms):
        return "similar", "rule_based", f"Project description matches the required nature ({', '.join(terms)})."
    if hits:
        return "uncertain", "rule_based", f"Only partial match on nature of work ({', '.join(hits)} of {', '.join(terms)})."
    return "not_similar", "rule_based", f"Project description does not mention {', '.join(terms)}."


def _similar_project(spec: CriterionSpec, claims: list[Claim], requirement_quote: str, reference: date, llm) -> VerdictResult:
    if not claims:
        return _not_found(spec, "a completed project of the required nature")
    window = spec.params.get("window_years") or 0
    earliest = reference.year - window if window else None
    threshold = spec.threshold_value or 0

    assessed = []
    for c in claims:
        value_ok = (c.value_numeric or 0) >= threshold
        year = c.detail.get("year")
        date_ok = earliest is None or (year is not None and year >= earliest)
        # Only spend a judgement on projects that already clear the arithmetic gates.
        nature, tier, rationale = _judge_nature(spec, c, requirement_quote, llm) if value_ok and date_ok else ("n/a", "deterministic", "")
        assessed.append((c, value_ok, date_ok, nature, tier, rationale))

    for c, value_ok, date_ok, nature, tier, rationale in assessed:
        if value_ok and date_ok and nature == "similar":
            return VerdictResult(
                spec.key, "pass", tier, evidence=[c],
                computation=f"{format_inr_short(c.value_numeric)} ≥ {format_inr_short(threshold)} · {c.detail.get('year')} ≥ {earliest} · nature ✓",
                reason=f"Qualifying project found. Value and date checked deterministically; nature of work: {rationale}",
            )
    uncertain = [a for a in assessed if a[1] and a[2] and a[3] == "uncertain"]
    if uncertain:
        c, *_, tier, rationale = uncertain[0]
        return VerdictResult(spec.key, "unknown", tier, unknown_kind="low_confidence", evidence=[c],
                             reason=f"Value and date qualify, but similarity of nature needs evaluator judgement: {rationale}")

    best = max(assessed, key=lambda a: (a[1], a[2], a[0].value_numeric or 0))
    c, value_ok, date_ok, nature, tier, rationale = best
    why = []
    if not value_ok:
        why.append(f"value {format_inr_short(c.value_numeric or 0)} is below {format_inr_short(threshold)}")
    if not date_ok:
        why.append(f"completed {c.detail.get('year')}, before the {window}-year window ({earliest} onwards)")
    if value_ok and date_ok and nature == "not_similar":
        why.append(f"nature of work is not similar — {rationale}")
    return VerdictResult(spec.key, "fail", tier if nature != "n/a" else "deterministic", evidence=[c],
                         reason="No qualifying project: " + "; ".join(why) + ".")


def _in_window(c: Claim, earliest: int | None) -> str:
    """in | out | uncertain, for a work against the tender's window of years."""
    if earliest is None:
        return "in"
    lo, hi = c.detail.get("year_min"), c.detail.get("year_max")
    year, certain = c.detail.get("year"), c.detail.get("year_certain", True)
    if lo is not None and lo >= earliest:
        return "in"
    if hi is not None and hi < earliest:
        return "out"
    if lo is None and hi is None and year is not None:
        if certain:
            return "in" if year >= earliest else "out"
        return "in" if year >= earliest else "uncertain"
    return "uncertain"


def _similar_works_any(spec: CriterionSpec, claims: list[Claim], requirement_quote: str,
                       reference: date, llm) -> VerdictResult:
    """The experience ladder: any one of "3 works ≥ X, or 2 ≥ Y, or 1 ≥ Z" qualifies.

    Two rules govern what this may conclude, and both exist because a wrong disqualification is
    the worst thing this tool can produce:

      * **Values are indexed first.** When the tender prints multiplying factors, a project
        completed in an earlier year counts at its escalated value. Comparing the raw figure
        understates what the tender itself offers the bidder.
      * **It never returns `fail`.** Passing needs `count` qualifying projects; failing would need
        proof that no further qualifying project exists anywhere in the submission, and an
        extractor that missed one cannot tell that apart from a bidder that has none. So a
        shortfall is `unknown`, naming exactly how many were found against each option.
    """
    options = [o for o in (spec.params.get("options") or []) if o.get("min_value") is not None]
    if not options:
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="unparseable", evidence=claims,
                             reason="The tender's options for this requirement could not be read as values.")
    if not spec.params.get("options_consistent", True):
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="unparseable", evidence=claims,
                             reason="The tender's options do not agree on one contract value, so no bidder is "
                                    "judged against them until the committee confirms the figures.")
    if not claims:
        return _not_found(spec, "completed projects of the required nature")

    table = indexation.table_from_params(spec.params)
    window = spec.params.get("window_years") or 0
    earliest = reference.year - window if window else None
    lowest = min(o["min_value"] for o in options)

    qualifying: list[tuple[Claim, float, str]] = []
    pending: list[tuple[Claim, float, str]] = []  # would count, once the committee confirms one fact
    rejected: list[str] = []
    tier = "deterministic"
    for c in claims:
        if c.value_numeric is None:
            continue
        year = c.detail.get("year")
        if c.detail.get("foreign"):
            rejected.append(f"{format_inr_short(c.value_numeric)} is certified to another contractor "
                            f"({c.detail.get('contractor')})")
            continue
        # A completion column that says only "March -25" gives a year by inference, not by
        # statement. Excluding a work on an inferred year would reject real experience over a
        # two-digit abbreviation, so only a year read in full can put a work outside the window —
        # and a work whose year is only bounded counts towards a pass only once it is confirmed.
        where = _in_window(c, earliest)
        if where == "out":
            rejected.append(f"{format_inr_short(c.value_numeric)} completed {year}, before the {window}-year window")
            continue
        esc = indexation.escalate(c.value_numeric, year, table)
        value = esc.value if esc else c.value_numeric
        if value < lowest:
            rejected.append(f"{format_inr_short(value)} is below the lowest option ({format_inr_short(lowest)})")
            continue
        nature, judged_tier, rationale = _judge_nature(spec, c, requirement_quote, llm)
        if judged_tier != "deterministic":
            tier = judged_tier
        shown = esc.explain() if esc and esc.applied else format_inr_short(value)
        if nature == "similar" and where == "in":
            qualifying.append((c, value, shown))
        elif nature == "not_similar":
            rejected.append(f"{format_inr_short(value)} is not of the required nature — {rationale}")
        else:
            why = rationale if nature == "uncertain" else f"completion year not certain ({c.detail.get('year_note') or year})"
            pending.append((c, value, shown))
            rejected.append(f"{format_inr_short(value)} needs confirmation — {why}")

    met = experience_ladder.satisfied_by(options, [v for _, v, _ in qualifying])
    if met:
        floor = met["min_value"]
        used = [(c, v, shown) for c, v, shown in qualifying if v >= floor][: met["count"]]
        indexed = [shown for _, _, shown in used if "×" in shown]
        return VerdictResult(
            spec.key, "pass", tier, evidence=[c for c, _, _ in used],
            computation=f"{met['count']} × ≥ {format_inr_short(floor)} ✓",
            reason=f"Qualifies on the {met['count']}-project option: "
                   + ", ".join(shown for _, _, shown in used)
                   + f" against ≥ {format_inr_short(floor)} each."
                   + (" Earlier years were brought to current price level first." if indexed else ""),
            extra={"option": met["count"], "indexed": bool(indexed)},
        )

    found = experience_ladder.shortfall(options, [v for _, v, _ in qualifying])
    maybe = experience_ladder.satisfied_by(options, [v for _, v, _ in qualifying + pending])
    if maybe:
        floor = maybe["min_value"]
        used = [(c, v, shown) for c, v, shown in qualifying + pending if v >= floor][: maybe["count"]]
        return VerdictResult(
            spec.key, "unknown", tier, unknown_kind="low_confidence", evidence=[c for c, _, _ in used],
            reason=f"Would qualify on the {maybe['count']}-project option ("
                   + ", ".join(shown for _, _, shown in used)
                   + f" against ≥ {format_inr_short(floor)} each) once the evaluator confirms: "
                   + "; ".join(r for r in rejected if "needs confirmation" in r)[:600] + ".",
            extra={"option": maybe["count"], "pending": len(pending)},
        )
    return VerdictResult(
        spec.key, "unknown", tier, unknown_kind="not_found",
        evidence=[c for c, _, _ in qualifying][:3] or claims[:2],
        reason=f"No option is met by the projects located: {found}. "
               + (f"Set aside: {'; '.join(rejected[:3])}. " if rejected else "")
               + "Treated as unknown, not as a failure — a project the extraction missed would change this.",
        extra={"shortfall": found},
    )


def evaluate_criterion(spec: CriterionSpec, claims: list[Claim], requirement_quote: str,
                       reference: date | None = None, llm=None) -> VerdictResult:
    reference = reference or date.today()
    if spec.key in _BID_CONDITIONS:
        return _bid_condition(spec)
    if not claims and spec.operator != "exists_all":
        return _not_found(spec)
    try:
        handler = _BY_KEY.get(spec.key)
        if handler is not None:
            return handler(spec, claims, reference)
        if spec.operator in (">=", "<="):
            return _numeric(spec, claims, reference.year)
        if spec.operator == "exists":
            return _exists(spec, claims)
        if spec.operator == "exists_all":
            return _exists_all(spec, claims)
        if spec.operator == "similar_project":
            return _similar_project(spec, claims, requirement_quote, reference, llm)
        if spec.operator == "similar_works_any":
            return _similar_works_any(spec, claims, requirement_quote, reference, llm)
    except Exception as exc:  # FSD 7.3: one criterion's error must never crash the batch
        log.exception("Evaluation error for %s", spec.key)
        return VerdictResult(spec.key, "unknown", "deterministic", "This value could not be evaluated automatically.",
                             claims, unknown_kind="unparseable", extra={"error": str(exc)})
    return VerdictResult(spec.key, "unknown", "deterministic",
                         "Evidence was found, but bidder-side evaluation of this criterion type is not built yet "
                         "(bidder phase). Evaluator to assess.", claims, unknown_kind="low_confidence")


def overall_status(verdicts: list[tuple[str, bool]]) -> str:
    """verdicts: (verdict, gating). Disqualified on any gating fail; pending on any gating unknown.
    A non-gating condition (rate analysis, bid validity) informs the committee but decides nothing."""
    if any(v == "fail" and gating for v, gating in verdicts):
        return "disqualified"
    if any(v == "unknown" and gating for v, gating in verdicts):
        return "pending"
    return "eligible"


# --------------------------------------------------------------------------------------------
# PMC works criteria with their own rules
# --------------------------------------------------------------------------------------------

_BID_CONDITIONS = {"bid_rate_analysis", "bid_additional_security", "bid_validity"}


def _bid_condition(spec: CriterionSpec) -> VerdictResult:
    return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="low_confidence", evidence=[],
                         reason="It applies once prices are opened (rate analysis, additional security) or to the "
                                "offer itself (validity), so there is nothing to check in the technical envelope.")


def _emd(spec: CriterionSpec, claims: list[Claim], reference: date) -> VerdictResult:
    """Paid, for this tender, and at least the tender's amount. The tender prints its EMD rounded
    (₹67,750 on the B-1 form for ₹67,749.58) and the portal charges it rounded down (₹67,749), so a
    rupee of rounding is not a shortfall."""
    required = spec.threshold_value
    usable = [c for c in claims if c.value_numeric is not None]
    if not usable:
        return _not_found(spec, "an EMD payment receipt")
    wrong = [c for c in usable if c.detail.get("for_this_tender") is False]
    right = [c for c in usable if c.detail.get("for_this_tender") is not False]
    if not right:
        c = wrong[0]
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=wrong[:2],
                             reason=f"The EMD receipt enclosed is for tender {c.detail.get('tender_ref')!r}, not this one. "
                                    "Treated as unknown — the bidder may have paid against this tender separately.")
    failed = [c for c in right if (c.detail.get("payment_status") or "").lower() not in ("", "success", "completed successfully")]
    paid = [c for c in right if c not in failed]
    if not paid:
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=failed[:1],
                             reason=f"The payment receipt reports status {failed[0].detail.get('payment_status')!r}.")
    best = max(paid, key=lambda c: c.value_numeric)
    if required is None:
        return VerdictResult(spec.key, "pass", "deterministic", evidence=[best], computation="EMD paid",
                             reason=f"EMD of {format_inr(best.value_numeric)} paid for this tender (tender amount not read).")
    ok = best.value_numeric >= required - 1.0
    return VerdictResult(
        spec.key, "pass" if ok else "fail", "deterministic", evidence=[best],
        computation=f"{format_inr(best.value_numeric)} {'≥' if ok else '<'} {format_inr(required)}"
                    + (" (within ₹1 of rounding)" if ok and best.value_numeric < required else ""),
        reason=(f"EMD of {format_inr(best.value_numeric)} paid online for this tender"
                + (f" (receipt: tender {best.detail.get('tender_ref')})" if best.detail.get("tender_ref") else "")
                + f", against the tender's {format_inr(required)}."
                + (f" The ₹{required - best.value_numeric:.2f} difference is rounding of the tender's figure, not a shortfall."
                   if ok and best.value_numeric < required else "")),
    )


def _registration(spec: CriterionSpec, claims: list[Claim], reference: date) -> VerdictResult:
    """Valid on the day the bid was made, and for works of this value. Any one registration that
    satisfies both is enough; a bidder often encloses several (PWD, Zilla Parishad, PMC)."""
    bid_day = _bid_day(spec, reference)
    cost = spec.tender.get("estimated_cost")
    valid, expired, too_small, undated = [], [], [], []
    for c in claims:
        to = c.detail.get("valid_to")
        cap = c.detail.get("capacity")
        if to and date.fromisoformat(to) < bid_day:
            expired.append(c)
        elif cap and cost and cap < float(cost):
            too_small.append(c)
        elif not to:
            undated.append(c)
        else:
            valid.append(c)
    if valid:
        c = valid[0]
        return VerdictResult(spec.key, "pass", "deterministic", evidence=[c],
                             computation=f"valid to {date.fromisoformat(c.detail['valid_to']).strftime('%d-%m-%Y')} ≥ bid date "
                                         f"{bid_day.strftime('%d-%m-%Y')}",
                             reason=f"{c.value}. Valid on the bid date"
                                    + (f" and covers works up to {format_inr_short(c.detail['capacity'])} against this "
                                       f"tender's {format_inr_short(float(cost))}" if c.detail.get("capacity") and cost else "")
                                    + ".")
    if undated:
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="unparseable", evidence=undated[:1],
                             reason=f"A registration is enclosed ({undated[0].value}) but its validity could not be read. "
                                    "Evaluator to confirm it was valid on the bid date.")
    if too_small:
        c = too_small[0]
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=too_small[:2],
                             reason=f"{c.value}: the registration covers works up to {format_inr_short(c.detail['capacity'])}, "
                                    f"below this tender's {format_inr_short(float(cost))}. The committee decides whether "
                                    "another registration or a class upgrade applies.")
    c = expired[0]
    return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=expired[:2],
                         reason=f"{c.value}: expired before the bid date ({bid_day.strftime('%d-%m-%Y')}). "
                                "Treated as unknown — a renewal may be in process; seek clarification.")


def _bid_day(spec: CriterionSpec, reference: date) -> date:
    raw = spec.tender.get("bid_date")
    try:
        return date.fromisoformat(raw) if raw else reference
    except ValueError:
        return reference


def _bid_capacity(spec: CriterionSpec, claims: list[Claim], reference: date) -> VerdictResult:
    """A bid-capacity certificate is a calculation. When it is drawn on another period or multiplier,
    its own A (the best year's work, brought to current prices) and B (work in hand) are facts,
    and (A × N × 1.5) − B on this tender's N is this tender's answer."""
    required = spec.threshold_value
    if required is None:
        return VerdictResult(spec.key, "unknown", "deterministic", "Tender threshold could not be read.", claims,
                             unknown_kind="unparseable")
    rows = [c for c in claims if c.detail.get("recomputed") is not None or c.value_numeric is not None]
    if not rows:
        return _not_found(spec, "a bid-capacity certificate")
    n = spec.tender.get("completion_years")
    for c in rows:
        restricted = c.detail.get("restricted") and c.detail.get("addressed_to") and             not re.search(r"pune\s*municipal|PMC", c.detail.get("addressed_to") or "", re.I)
        rec = c.detail.get("recomputed")
        if rec is not None and rec < 100_000:  # a lakh-denominated working read as rupees
            rec = None
        defects = [d for d in (c.detail.get("defects") or []) if "UDIN" not in d]
        if rec is not None and not restricted:
            ok = rec >= required
            how = (f"(A {format_inr_short(c.detail['A'])} × N {n:g} × 1.5) − B {format_inr_short(c.detail.get('B') or 0)}")
            return VerdictResult(
                spec.key, "pass" if ok else "fail", "deterministic", evidence=[c],
                computation=f"{how} = {format_inr(rec)} {'≥' if ok else '<'} {format_inr(required)}",
                reason=("Recomputed on this tender's terms from the certificate's own A and B"
                        + (f" — the certificate {'; '.join(defects)}" if defects else "")
                        + f": {format_inr(rec)} against the required {format_inr(required)}."
                        + (f" The certificate itself states {format_inr(c.value_numeric)}." if c.value_numeric else "")),
                extra={"recomputed": rec},
            )
        if not defects and not restricted and c.value_numeric is not None:
            ok = c.value_numeric >= required
            return VerdictResult(spec.key, "pass" if ok else "fail", "deterministic", evidence=[c],
                                 computation=f"{format_inr(c.value_numeric)} {'≥' if ok else '<'} {format_inr(required)}",
                                 reason=f"Certified bid capacity {format_inr(c.value_numeric)} against the required {format_inr(required)}.")
    c = rows[0]
    reasons = sorted({d for x in rows for d in (x.detail.get("defects") or [])})
    rec = c.detail.get("recomputed")
    return VerdictResult(
        spec.key, "unknown", "deterministic", unknown_kind="not_found", evidence=rows[:2],
        reason="The bid-capacity certificate cannot be compared against this tender as it stands: it "
               + ("; ".join(reasons) or "does not state its A and B")
               + (f". On this tender's terms its figures would give {format_inr(rec)}" if rec else "")
               + ". Treated as unknown — the committee decides whether this certificate is acceptable.",
        extra={"defects": reasons},
    )


def _sole_bidder(spec: CriterionSpec, claims: list[Claim], reference: date) -> VerdictResult:
    jv = [c for c in claims if c.detail.get("jv")]
    firm = [c for c in claims if c.detail.get("constitution")]
    if jv:
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="conflict", evidence=jv[:2] + firm[:1],
                             reason="A joint-venture / consortium agreement is enclosed; this tender does not allow joint "
                                    "ventures. Evaluator to confirm whether the bid is made by a JV or the agreement belongs "
                                    "to past work.")
    if firm:
        return VerdictResult(spec.key, "pass", "deterministic", evidence=firm[:1], computation="single firm, no JV agreement",
                             reason=f"The bid is made by a single firm ({firm[0].detail['constitution']}), and no joint-venture "
                                    "or consortium agreement is enclosed.")
    return _not_found(spec, "the bidding firm's constitution")


def _key_quantities(spec: CriterionSpec, claims: list[Claim], reference: date) -> VerdictResult:
    """Each item's executed quantity in one financial year, against the tender's minimum. One work
    that alone meets the minimum settles the item wherever in the window it fell; several works are
    added up only within a stated financial year. Never a fail: a certificate the extraction missed
    would change the sum."""
    items = spec.params.get("items") or []
    if not items:
        return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="unparseable", evidence=claims[:2],
                             reason="The tender does not state the minimum quantities, so they cannot be checked.")
    window = spec.params.get("window_years") or 5
    earliest = reference.year - window
    met, short, evidence = [], [], []
    for req in items:
        need = float(req["qty"])
        label = f"{req['item']} ≥ {need:g} {req.get('unit') or ''}".strip()
        rows = [c for c in claims if c.detail.get("item") == req["item"]]
        inside = []
        for c in rows:
            y, floor = c.detail.get("year"), c.detail.get("tender_year")
            if y is not None:
                if y >= earliest:
                    inside.append(c)
            elif floor is not None and floor >= earliest:
                inside.append(c)  # tendered within the window, so completed within it
        single = max(inside, key=lambda c: c.value_numeric or 0, default=None)
        by_year: dict = {}
        for c in inside:
            if c.detail.get("year") is not None:
                by_year.setdefault(c.detail["year"], []).append(c)
        best_year, best_sum = max(((y, sum(c.value_numeric or 0 for c in cs)) for y, cs in by_year.items()),
                                  key=lambda t: t[1], default=(None, 0.0))
        if single is not None and (single.value_numeric or 0) >= need:
            met.append(f"{label}: {single.value_numeric:g} in one work (p.{single.page})")
            evidence.append(single)
        elif best_sum >= need:
            met.append(f"{label}: {best_sum:g} across works in FY {best_year}-{str(best_year + 1)[-2:]}")
            evidence += by_year[best_year][:2]
        elif rows:
            short.append(f"{label}: at most {max(best_sum, single.value_numeric if single else 0):g} shown in one year"
                         + ("" if inside else " (none dated within the window)"))
        else:
            short.append(f"{label}: not found")
    if not short:
        return VerdictResult(spec.key, "pass", "deterministic", evidence=evidence[:4], computation="all key items met",
                             reason="Every key item was executed within one financial year of the window: " + "; ".join(met) + ".")
    return VerdictResult(spec.key, "unknown", "deterministic", unknown_kind="not_found", evidence=evidence[:4],
                         reason="Not every key item is shown executed in one financial year: " + "; ".join(short)
                                + (". Met: " + "; ".join(met) if met else "")
                                + ". Treated as unknown — a certificate the extraction missed would change the sum.")


_BY_KEY = {"pmc_emd": _emd, "emd": _emd, "pmc_registration": _registration, "pmc_bid_capacity": _bid_capacity,
           "no_joint_venture": _sole_bidder, "pmc_key_quantities": _key_quantities}
