"""Score a corpus_report.py --json output against hand-verified ground truth.

    uv run python scripts/corpus_report.py "/path/to/Dataset Tenders" --ocr --json /tmp/report.json
    uv run python scripts/score_corpus.py /tmp/report.json [tests/data/corpus_truth.json]

Checks, per tender: each fact value, title/award, each expected criterion (present, located or
derived, threshold value — or none where an input is missing — similar-works options, key-quantity
items, personnel years, escalation rate), required checklist labels, and criteria that must NOT be
extracted. Prints every miss and an overall accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TRUTH = Path(__file__).resolve().parents[1] / "tests" / "data" / "corpus_truth.json"


def _close(a, b, tol=0.006) -> bool:
    if a is None or b is None:
        return False
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()
    return abs(a - b) <= max(1.0, abs(b) * tol)


def entry(name: str, extraction, warnings: list[str] | None = None) -> dict:
    """The per-tender record scored here (also what corpus_report.py --json writes)."""
    ex = extraction
    return {"tender": name, "ref_no": ex.ref_no, "title": ex.title, "facts": ex.facts,
            "award": ex.award_mechanism, "award_detail": ex.award_detail, "warnings": ex.warnings + (warnings or []),
            "criteria": [{"key": c.key, "label": c.label, "status": c.status, "threshold": c.threshold_display,
                          "value": c.threshold_value, "params": c.params, "notes": c.notes, "gating": c.gating,
                          "page": c.source.page if c.source else None} for c in ex.criteria]}


def score_tender(out: dict, truth: dict) -> tuple[int, list[str]]:
    """(checks run, misses) for one tender."""
    checks, misses = 0, []

    def check(ok: bool, what: str):
        nonlocal checks
        checks += 1
        if not ok:
            misses.append(what)

    facts = {f["key"]: f for f in out.get("facts", [])}
    for key, want in truth.get("facts", {}).items():
        got = facts.get(key)
        check(got is not None and _close(got["value"], want), f"fact {key}: want {want!r}, got {got['value'] if got else None!r}")
    if "title_contains" in truth:
        check(truth["title_contains"].lower() in (out.get("title") or "").lower(),
              f"title should contain {truth['title_contains']!r}: {out.get('title')!r}")
    if truth.get("title_not_lossy"):
        title = out.get("title") or ""
        check(title and not any("" <= ch <= "" for ch in title), f"title is lossy/empty: {title!r}")
    if "award" in truth:
        check(out.get("award") == truth["award"], f"award: want {truth['award']}, got {out.get('award')}")

    crit = {c["key"]: c for c in out.get("criteria", [])}
    for key, want in truth.get("criteria", {}).items():
        c = crit.get(key)
        check(c is not None, f"criterion {key} missing")
        if c is None:
            continue
        if "value" in want:
            check(_close(c["value"], want["value"]), f"{key} value: want {want['value']}, got {c['value']} ({c['threshold']})")
        if "options" in want:
            got = sorted(o.get("min_value") or 0 for o in c["params"].get("options", []))
            check(len(got) == len(want["options"]) and all(_close(g, w) for g, w in zip(got, sorted(want["options"]))),
                  f"{key} options: want {sorted(want['options'])}, got {got}")
        if "nature_contains" in want:
            check(want["nature_contains"].lower() in str(c["params"].get("nature", "")).lower(),
                  f"{key} nature should contain {want['nature_contains']!r}: {c['params'].get('nature')!r}")
        if "items" in want:
            got_items = c["params"].get("items", [])
            for name, qty in want["items"].items():
                hit = next((i for i in got_items if name.lower() in i["item"].lower()), None)
                check(hit is not None and _close(hit["qty"], qty, tol=0.001),
                      f"{key} item {name}: want {qty}, got {hit['qty'] if hit else None} (items {[i['item'] for i in got_items]})")
        if "status" in want:
            check(c["status"] == want["status"], f"{key} status: want {want['status']}, got {c['status']}")
        if want.get("no_value"):
            check(c["value"] is None, f"{key} must state no number (an input it needs is not in the bundle), got {c['value']}")
        if "index_rate" in want:
            got_rate = c["params"].get("index_rate")
            check(got_rate is not None and abs(got_rate - want["index_rate"]) < 0.005,
                  f"{key} escalation rate: want {want['index_rate']}, got {got_rate}")
        if "min_years" in want:
            check(c["params"].get("min_years") == want["min_years"],
                  f"{key} min_years: want {want['min_years']}, got {c['params'].get('min_years')}")
    labels = " | ".join(c["label"] for c in out.get("criteria", [])).lower()
    for term in truth.get("labels_contain", []):
        check(term.lower() in labels, f"no criterion label mentions {term!r}")
    for label in truth.get("must_not_labels", []):
        # exact label, or a prefix when written with a trailing "…"
        hit = (lambda lab: lab.startswith(label[:-1].lower())) if label.endswith("…") else (lambda lab: lab == label.lower())
        check(not any(hit(c["label"].strip().lower()) for c in out.get("criteria", [])),
              f"duplicate/noise criterion {label!r} should not be listed")
    for key in truth.get("must_not", []):
        check(key not in crit, f"criterion {key} should not be extracted ({crit.get(key, {}).get('threshold')})")
    return checks, misses


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text())
    truth = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else TRUTH).read_text())
    by_name = {r["tender"]: r for r in report}
    total_checks = total_misses = 0
    for name, want in truth.items():
        if name.startswith("_"):
            continue
        out = by_name.get(name)
        if out is None:
            print(f"{name}: not in report")
            continue
        checks, misses = score_tender(out, want)
        total_checks += checks
        total_misses += len(misses)
        print(f"{name:<34} {checks - len(misses):>3}/{checks:<3}" + ("" if misses else "  ok"))
        for m in misses:
            print(f"    ✗ {m}")
    pct = 100 * (total_checks - total_misses) / max(1, total_checks)
    print(f"\n{total_checks - total_misses}/{total_checks} checks correct ({pct:.1f}%)")


if __name__ == "__main__":
    main()
