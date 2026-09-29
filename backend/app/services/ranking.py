"""Ranked shortlist (FR8) by the tender's own award mechanism. Pure deterministic code.

The ranking is a recommendation that shows its work, never a selection (NFR2): each entry states
its basis, and every excluded bidder carries the gate that excluded it with its citations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.values import format_inr


@dataclass
class BidderStanding:
    bidder_id: str
    name: str
    status: str  # eligible | pending | disqualified
    price: float | None
    price_citation: dict | None
    issues: list[dict] = field(default_factory=list)  # failing / unknown criteria with citations


def compute_ranking(mechanism: str, mechanism_label: str | None, standings: list[BidderStanding]) -> dict:
    warning = None
    if mechanism == "QCBS":
        warning = ("Tender uses QCBS. The technical-scoring rubric is outside PoC scope, so eligible bidders are "
                   "listed by quoted price for reference only — confirm scores with the committee.")
    elif mechanism != "L1":
        warning = "Award mechanism unclear — ranking shown as raw prices only; confirm with the committee."

    eligible = [s for s in standings if s.status == "eligible"]
    priced = sorted((s for s in eligible if s.price is not None), key=lambda s: s.price)
    unpriced = [s for s in eligible if s.price is None]

    entries = []
    for i, s in enumerate(priced):
        label = f"L{i + 1}" if mechanism == "L1" else f"#{i + 1}"
        if mechanism == "L1":
            basis = ("Lowest quoted price among technically qualified bidders" if i == 0
                     else f"Technically qualified; {format_inr(s.price - priced[0].price)} above L1")
        else:
            basis = "Eligible; ordered by quoted price"
        entries.append(_entry(s, label, "qualified", basis, is_top=i == 0 and mechanism == "L1"))
    for s in unpriced:
        entries.append(_entry(s, "?", "pending", "Eligible, but the quoted price was not found in the submission"))
    for s in standings:
        if s.status == "pending":
            n = len(s.issues)
            entries.append(_entry(s, "?", "pending",
                                  f"Held pending clarification — {n} {'criterion' if n == 1 else 'criteria'} could not be determined"))
    for s in standings:
        if s.status == "disqualified":
            gates = ", ".join(i["criterion_label"] for i in s.issues if i["verdict"] == "fail")
            entries.append(_entry(s, "✕", "disqualified", f"Disqualified — failed gating criterion: {gates}"))

    return {
        "mechanism": mechanism,
        "mechanism_label": mechanism_label,
        "warning": warning,
        "recommended_bidder_id": priced[0].bidder_id if priced and mechanism == "L1" else None,
        "entries": entries,
    }


def _entry(s: BidderStanding, rank_label: str, kind: str, basis: str, is_top: bool = False) -> dict:
    return {
        "rank_label": rank_label,
        "kind": kind,
        "is_top": is_top,
        "bidder_id": s.bidder_id,
        "name": s.name,
        "price": s.price,
        "price_display": format_inr(s.price) if s.price is not None else "Not found",
        "price_citation": s.price_citation,
        "basis": basis,
        "issues": s.issues,
    }
