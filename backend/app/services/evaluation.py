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


def _numeric(spec: CriterionSpec, claims: list[Claim]) -> VerdictResult:
    usable = [c for c in claims if c.value_numeric is not None]
    if not usable:
        if claims:
            return VerdictResult(spec.key, "unknown", "deterministic", "A value was found but could not be read as a number.",
                                 claims, unknown_kind="unparseable")
        return _not_found(spec)

    # "Maximum turnover in any of the last five years" is answered by a *series* of figures, one
    # per year. Five different numbers are what the tender asked for, not a contradiction — and
    # the winner is decided after indexation, because an older, smaller year can overtake a newer
    # one once it is brought to current rupees.
    if spec.params.get("basis") == "max_annual" and len(usable) > 1:
        table = indexation.table_from_params(spec.params)
        scored = [(c, indexation.escalate(c.value_numeric, c.detail.get("year"), table)) for c in usable]
        best_claim, best = max(scored, key=lambda cs: cs[1].value if cs[1] else 0)
        threshold = spec.threshold_value
        if threshold is None:
            return VerdictResult(spec.key, "unknown", "deterministic", "Tender threshold could not be read.", usable,
                                 unknown_kind="unparseable")
        ok = best.value >= threshold
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
    rejected: list[str] = []
    tier = "deterministic"
    for c in claims:
        if c.value_numeric is None:
            continue
        year = c.detail.get("year")
        if earliest is not None and year is not None and year < earliest:
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
        if nature == "similar":
            qualifying.append((c, value, esc.explain() if esc and esc.applied else format_inr_short(value)))
        elif nature == "uncertain":
            rejected.append(f"{format_inr_short(value)} needs evaluator judgement on nature — {rationale}")
        else:
            rejected.append(f"{format_inr_short(value)} is not of the required nature — {rationale}")

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
    if not claims and spec.operator != "exists_all":
        return _not_found(spec)
    try:
        if spec.operator in (">=", "<="):
            return _numeric(spec, claims)
        if spec.operator == "exists":
            return _exists(spec, claims)
        if spec.operator == "exists_all":
            return _exists_all(spec, claims)
        if spec.operator == "similar_project":
            return _similar_project(spec, claims, requirement_quote, reference, llm)
        if spec.operator == "similar_works_any":
            return _similar_works_any(spec, claims, requirement_quote, reference, llm)
    except (ArithmeticError, TypeError, ValueError) as exc:  # FSD 7.3: never crash the batch
        log.exception("Evaluation error for %s", spec.key)
        return VerdictResult(spec.key, "unknown", "deterministic", "This value could not be evaluated automatically.",
                             claims, unknown_kind="unparseable", extra={"error": str(exc)})
    return VerdictResult(spec.key, "unknown", "deterministic",
                         "Evidence was found, but bidder-side evaluation of this criterion type is not built yet "
                         "(bidder phase). Evaluator to assess.", claims, unknown_kind="low_confidence")


def overall_status(verdicts: list[tuple[str, bool]]) -> str:
    """verdicts: (verdict, gating). Disqualified on any gating fail; pending on any unknown."""
    if any(v == "fail" and gating for v, gating in verdicts):
        return "disqualified"
    if any(v == "unknown" for v, _ in verdicts):
        return "pending"
    return "eligible"
