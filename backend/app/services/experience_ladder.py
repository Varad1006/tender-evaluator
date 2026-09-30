"""The experience ladder: "3 works ≥ 40%, or 2 ≥ 50%, or 1 ≥ 80%" and everything shaped like it.

Every tender in the corpus that asks for proven past experience offers a *choice* of ways to
prove it, and always with the same shape: more projects of a lower value, or fewer of a higher
one. Only the vocabulary changes with the department:

    PMC drainage works   a) Three completed works (sewer/storm water line/retaining wall) costing
                            not less than amount equal to 40% of the total estimated cost put to
                            tender.  OR  b) Two … 50%  OR  c) One … 80%
    PCMC IT services     1. At-least three different projects with each Project value of more than
                            ₹ 17 Lakhs  OR  2. … two … ₹ 21 Lakhs  OR  3. … one … ₹34 Lakhs
    PMC IT services      One project of minimum value INR 64 Lakhs …  OR  Two projects of minimum
                            value INR 40 Lakhs each …  OR  Three projects of … INR 32 Lakhs each

Reading only the first rung — which a single-sentence rule does, because it sees one rung at a
time — states a requirement three times too strict and disqualifies bidders who satisfy the
tender. So rungs are collected across sentences and pages and assembled into one criterion.

Two structural facts make this safe to do without knowing anything about a particular tender:

  * **A ladder is monotone.** More projects always cost less each. A group whose counts and
    values do not move in opposite directions is not a ladder and is refused.
  * **Every rung implies the same contract value.** 40% of E, 50% of E and 80% of E all divide
    back to one E. Measured on the three tenders above the implied bases agree to within 1.2%,
    so a rung that divides to something else was misread — a value picked out of the neighbouring
    column, a Lakh read as a Crore — and the ladder is reported inconsistent rather than used.
    This is the only check here that can catch a *wrong number*, as opposed to a missing one.

When the rungs are stated as plain amounts the percentages are not printed, but the ratios
between rungs still identify the convention (32:40:64 is 40:50:80 scaled), which recovers the
contract value an RFP never states. That inference is labelled as inferred wherever it is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.adapters.pdf_parser import Sentence
from app.services.values import format_inr, format_inr_short, parse_amounts

# Counts appear as words far more often than digits ("Three completed works"), and only small
# counts are ever used — "at least 15 projects" is a volume requirement, not a ladder rung.
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_MAX_COUNT = 6

# Adjectives an authority puts between the count and the noun. Kept as a class, not a list of the
# words one tender used: anything that is not the noun is skipped, and the noun set is closed.
# The run may not contain the noun itself — otherwise "three different projects with each Project
# value of …" lets the adjectives swallow the real noun and bind to the second one, which silently
# moves the rung's text window past the word "each" (PCMC encroachment RFP p.24).
_NOUN = r"(?:projects?|works?|assignments?|contracts?|orders?|jobs?)"
_ADJ = r"(?:(?!" + _NOUN + r"\b)[A-Za-z()/,&.\-]+\s+){0,4}?"

_RUNG = re.compile(
    r"(?:\b(?:at[\s-]?least|atleast|minimum(?:\s+of)?|min\.?)\s+)?"
    r"\b(?P<count>" + "|".join(_COUNTS) + r"|[1-9])\s+"
    r"(?P<adj>" + _ADJ + r")"
    r"(?P<noun>" + _NOUN + r")\b",
    re.IGNORECASE,
)

# The value half of a rung: an obligation phrase, then an amount or a percentage. The obligation
# is required — "three works listed in Statement II" names no threshold and is not a rung.
_OBLIGATION = re.compile(
    r"(?:of\s+)?(?:minimum|min\.?)\s+value|value\s+of\s+(?:more\s+than|not\s+less\s+than|at\s+least)"
    r"|costing\s+not\s+less\s+than|cost(?:ing)?\s+of\s+(?:not\s+less\s+than|more\s+than)"
    r"|not\s+less\s+than|no(?:t)?\s+lower\s+than|more\s+than|at\s+least|each\s+of\s+value"
    r"|of\s+value|amount\s+equal\s+to|equal\s+to|worth|costing",
    re.IGNORECASE,
)
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|per\s*cent)", re.IGNORECASE)
_EACH = re.compile(r"\beach\b|\bper\s+(?:project|work|assignment)\b|\bevery\b", re.IGNORECASE)

# A rung's own text ends where the next rung or the evidence column begins.
_RUNG_SPAN = 220

# Bases that divide out within this much of each other are the same base; hand arithmetic in the
# corpus rounds each tier independently, which moves a tier by up to a few hundredths of a percent.
_BASE_TOLERANCE_PCT = 2.5

# The standard Indian public-works ladder. Used only to recognise a convention, never to supply a
# threshold: a ladder's own printed values always govern.
_STANDARD = (0.40, 0.50, 0.80)


@dataclass(frozen=True)
class Rung:
    """One way of satisfying the requirement: `count` works, each worth at least `value`."""

    count: int
    value: float | None  # rupees, when the rung prints an amount
    pct: float | None  # fraction of the contract value, when the rung prints a percentage
    per_project: bool  # the value applies to each project rather than to the total
    page: int
    quote: str

    def min_value(self, base: float | None) -> float | None:
        if self.value is not None:
            return self.value
        if self.pct is not None and base is not None:
            return round(self.pct * base, 2)
        return None


@dataclass
class Ladder:
    rungs: list[Rung]
    implied_base: float | None = None
    base_spread_pct: float | None = None
    consistent: bool = True
    base_inferred: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return sorted({r.page for r in self.rungs})

    def options(self, base: float | None = None) -> list[dict]:
        base = base if base is not None else self.implied_base
        return [
            {"count": r.count, "min_value": r.min_value(base), "pct": round(r.pct * 100) if r.pct else None,
             "per_project": r.per_project, "page": r.page}
            for r in self.rungs
        ]

    def display(self, base: float | None = None) -> str:
        parts = []
        for o in self.options(base):
            if o["min_value"] is not None:
                parts.append(f"{o['count']} × ≥ {format_inr_short(o['min_value'])}")
            elif o["pct"] is not None:
                parts.append(f"{o['count']} × ≥ {o['pct']}% of E")
            else:
                parts.append(f"{o['count']} × (value not stated)")
        return " or ".join(parts)


def _count_of(word: str) -> int | None:
    w = word.lower()
    return int(w) if w.isdigit() else _COUNTS.get(w)


def _amount_in(text: str) -> float | None:
    """The first amount that carries a currency marker or a Lakh/Crore multiplier.

    A bare number is refused on purpose: "3 years", "Section 8.6" and "Clause a)" all sit inside
    rung text, and reading one of them as a threshold is the misread that disqualifies a bidder.
    """
    for a in parse_amounts(text):
        if re.search(r"rs\.?|inr|₹|lakh|lac|crore|cr\b|million", a.raw, re.IGNORECASE):
            return a.value
    return None


def find_rungs(sentences: list[Sentence]) -> list[Rung]:
    """Every "<count> <noun> … <threshold>" rung in the given sentences, in reading order."""
    out: list[Rung] = []
    for s in sentences:
        text = s.text
        for m in _RUNG.finditer(text):
            count = _count_of(m.group("count"))
            if count is None or count > _MAX_COUNT:
                continue
            # The adjective run must not swallow a sentence boundary or another count.
            if re.search(r"[.;:]\s", m.group("adj") or ""):
                continue
            tail = text[m.end(): m.end() + _RUNG_SPAN]
            nxt = _RUNG.search(tail)
            if nxt:  # stop before the next rung so its value is not stolen
                tail = tail[: nxt.start()]
            ob = _OBLIGATION.search(tail)
            if not ob:
                continue
            after = tail[ob.end():]
            pct_m = _PCT.search(after[:90])
            value = _amount_in(after[:120])
            if pct_m is None and value is None:
                continue
            # A percentage and an amount in the same rung ("40% of the estimated cost, i.e. Rs 19.49
            # lacs") are the same threshold stated twice; the percentage is the durable one.
            pct = float(pct_m.group(1)) / 100 if pct_m else None
            if pct is not None and not 0 < pct <= 5:  # "40%" yes, "2022-23" or "100 per cent of 3" no
                continue
            out.append(Rung(
                count=count, value=None if pct is not None else value, pct=pct,
                per_project=bool(_EACH.search(tail)) or count == 1,
                page=s.page, quote=s.text.strip()[:400],
            ))
    return out


# "during last Five years", "over last 3 years as on bid submission date", "in the last 5 years
# from the date of submission of bid". The window belongs to the requirement, not to a rung, so it
# is searched across the sentences the rungs came from and their neighbours.
_WINDOW = re.compile(
    r"(?:during|over|within|in|for)?\s*(?:the\s+)?(?:last|past|preceding|previous)\s+"
    r"(?P<n>" + "|".join(_COUNTS) + r"|\d{1,2})\s*(?:\(\d{1,2}\)\s*)?(?:financial\s+|calendar\s+)?years?\b",
    re.IGNORECASE,
)


def find_window_years(sentences: list[Sentence]) -> int | None:
    """How far back completed work is allowed to count, when the requirement says so."""
    counts: dict[int, int] = {}
    for s in sentences:
        for m in _WINDOW.finditer(s.text):
            n = _count_of(m.group("n"))
            if n and 1 <= n <= 15:
                counts[n] = counts.get(n, 0) + 1
    if not counts:
        return None
    # Ties go to the longer window: a window read too short rejects work the tender accepts.
    return max(counts, key=lambda n: (counts[n], n))


def _monotone(rungs: list[Rung]) -> bool:
    """More projects must cost less each. Anything else is a list, not a ladder."""
    keyed = [(r.count, r.pct if r.pct is not None else r.value) for r in rungs]
    if any(v is None for _, v in keyed):
        return False
    keyed.sort(key=lambda kv: kv[0])
    return all(a[1] > b[1] for a, b in zip(keyed, keyed[1:]))


def _implied_bases(rungs: list[Rung]) -> list[float]:
    """What contract value each rung divides back to, when its fraction of the whole is known."""
    bases = []
    for r in rungs:
        if r.pct and r.value:
            bases.append(r.value / r.pct)
    return bases


def _infer_fractions(rungs: list[Rung]) -> list[float] | None:
    """Recover the percentages of a ladder that prints only amounts, by their ratios.

    32 : 40 : 64 lakh are 40 : 50 : 80 percent of the same number. The convention is recognised
    only when every ratio matches; a ladder with its own spacing keeps its amounts and no base.
    """
    if any(r.value is None for r in rungs) or len(rungs) != len(_STANDARD):
        return None
    by_count = sorted(rungs, key=lambda r: -r.count)  # 3, 2, 1
    if [r.count for r in by_count] != [3, 2, 1]:
        return None
    top = by_count[-1].value
    if not top:
        return None
    ratios = [r.value / top for r in by_count]
    want = [p / _STANDARD[-1] for p in _STANDARD]
    if all(abs(a - b) / b <= _BASE_TOLERANCE_PCT / 100 for a, b in zip(ratios, want)):
        return list(_STANDARD)
    return None


def build_ladder(rungs: list[Rung], estimated_cost: float | None = None) -> Ladder | None:
    """Assemble rungs into one ladder, or return None when they do not form one.

    `estimated_cost` turns percentage rungs into amounts. It is never used to *replace* an amount
    the tender printed.
    """
    if len(rungs) < 2:
        return None
    best: dict[int, Rung] = {}
    for r in rungs:  # one rung per count; the first statement of it wins
        best.setdefault(r.count, r)
    picked = sorted(best.values(), key=lambda r: -r.count)
    if len(picked) < 2 or not _monotone(picked):
        return None

    ladder = Ladder(rungs=picked)
    fractions = None
    if all(r.pct is not None for r in picked):
        fractions = [r.pct for r in picked]
    else:
        fractions = _infer_fractions(picked)
        if fractions:
            picked = [Rung(r.count, r.value, f, r.per_project, r.page, r.quote)
                      for r, f in zip(picked, fractions)]
            ladder.rungs = picked
            ladder.base_inferred = True

    bases = _implied_bases(picked)
    if bases:
        lo, hi = min(bases), max(bases)
        ladder.base_spread_pct = round((hi - lo) / lo * 100, 3) if lo else None
        ladder.consistent = ladder.base_spread_pct is not None and ladder.base_spread_pct <= _BASE_TOLERANCE_PCT
        ladder.implied_base = round(sum(bases) / len(bases), 2)
        if not ladder.consistent:
            ladder.notes.append(
                "The options do not agree on one contract value — "
                + "; ".join(f"{r.count} × {format_inr_short(r.value)} implies {format_inr(b)}"
                            for r, b in zip(picked, bases))
                + ". One of them is misread; the committee should confirm before any bidder is judged on it."
            )
        elif ladder.base_inferred:
            ladder.notes.append(
                f"The options are 40 / 50 / 80% of {format_inr(ladder.implied_base)}, which the tender does not "
                "state directly. The printed amounts are used; the contract value is inferred from them."
            )
    elif estimated_cost:
        ladder.implied_base = estimated_cost

    if any(r.pct is not None and r.value is None for r in picked) and estimated_cost:
        ladder.implied_base = estimated_cost
    return ladder


def satisfied_by(options: list[dict], project_values: list[float]) -> dict | None:
    """The cheapest option a bidder's completed projects satisfy, or None.

    `project_values` are the values of projects that have already passed the nature and date
    gates. An option needs `count` of them at or above `min_value`.
    """
    ordered = sorted((v for v in project_values if v is not None), reverse=True)
    for o in sorted(options, key=lambda o: o["count"]):
        need, floor = o["count"], o.get("min_value")
        if floor is None:
            continue
        qualifying = [v for v in ordered if v >= floor]
        if len(qualifying) >= need:
            return {**o, "matched_values": qualifying[:need]}
    return None


def shortfall(options: list[dict], project_values: list[float]) -> str:
    """Why none of the options is met, in the tender's own terms."""
    ordered = sorted((v for v in project_values if v is not None), reverse=True)
    parts = []
    for o in sorted(options, key=lambda o: o["count"]):
        floor = o.get("min_value")
        if floor is None:
            continue
        have = len([v for v in ordered if v >= floor])
        parts.append(f"{have} of {o['count']} project(s) at ≥ {format_inr_short(floor)}")
    return "; ".join(parts)
