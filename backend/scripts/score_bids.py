"""Score a bid_report.py --json run against hand-verified ground truth for those bids.

    uv run python scripts/score_bids.py report.json tests/local/<truth>.json

The truth file is written by reading the bids themselves (verdict per criterion, and the figure a
verdict rests on). It holds bidders' data, so it lives in tests/local/, which git ignores: the
repository never carries any bidder's documents or figures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def score(report: dict, truth: dict) -> tuple[int, list[str]]:
    checks, misses = 0, []
    for bid, want in truth["bids"].items():
        got = report["bids"].get(bid)
        if got is None:
            misses.append(f"{bid}: not in report")
            checks += 1
            continue
        for key, verdict in want.get("verdicts", {}).items():
            checks += 1
            v = got["verdicts"].get(key, {}).get("verdict")
            if v != verdict:
                misses.append(f"{bid} {key}: want {verdict}, got {v} — {got['verdicts'].get(key, {}).get('reason', '')[:160]}")
        for key, (lo, hi) in want.get("figures", {}).items():
            checks += 1
            v = got["verdicts"].get(key, {})
            nums = [e.get("num") for e in v.get("evidence", []) if e.get("num") is not None]
            rec = (v.get("extra") or {}).get("recomputed")
            candidates = nums + ([rec] if rec is not None else [])
            if not any(lo <= n <= hi for n in candidates):
                misses.append(f"{bid} {key}: figure should be within [{lo:,.0f}, {hi:,.0f}], got {candidates}")
        if "status" in want:
            checks += 1
            if got["status"] != want["status"]:
                misses.append(f"{bid} status: want {want['status']}, got {got['status']}")
    return checks, misses


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text())
    truth = json.loads(Path(sys.argv[2]).read_text())
    checks, misses = score(report, truth)
    for m in misses:
        print("  ✗", m)
    print(f"\n{checks - len(misses)}/{checks} checks correct ({100 * (checks - len(misses)) / max(1, checks):.1f}%)")


if __name__ == "__main__":
    main()
