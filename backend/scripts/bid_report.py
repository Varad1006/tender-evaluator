"""Run one tender and its bid envelopes through the same pipeline the API uses, and print (and save)
the verdict matrix. No database; OCR comes from the page cache unless --online.

    uv run python scripts/bid_report.py --tender 04.pdf --bids "Fee_PreQual_Technical_*.zip" \
        --root ~/Downloads [--online] [--json out.json] [--reference 2026-05-04]

Nothing here is specific to any tender or bidder: point it at any tender bundle and its bids.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.adapters.datalab_ocr import DatalabOcr, get_ocr  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import bidder_processing as bp  # noqa: E402
from app.services import tender_processing as tp  # noqa: E402
from app.services.bidder_extraction import CriterionSpec  # noqa: E402
from app.services.bundle import expand  # noqa: E402
from app.services.evaluation import evaluate_criterion, overall_status  # noqa: E402


def cache_only_ocr(cache: Path) -> DatalabOcr:
    def refuse(request):
        raise httpx.ConnectError("offline: page not in the OCR cache", request=request)
    return DatalabOcr("cache-only", cache, client=httpx.Client(transport=httpx.MockTransport(refuse)),
                      sleep=lambda s: None, requests_per_minute=100_000)


class _TenderRow:
    """The slice of a Tender row that tender_context() reads."""

    def __init__(self, ex):
        self.tender_ref_no, self.title, self.estimated_value, self.facts = ex.ref_no, ex.title, ex.estimated_value, ex.facts


def run(tender_files: list[Path], bids: dict[str, list[Path]], ocr, reference: date) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="bid-report-"))
    files, _ = expand([(f.name, f) for f in tender_files], tmp / "tender")
    tdocs = tp.local_pass(files)
    tp.run_ocr(tdocs, ocr)
    tex = tp.extract(tdocs)
    ctx = bp.tender_context(_TenderRow(tex))
    specs = [CriterionSpec(key=c.key, label=c.label, operator=c.operator, params=c.params, threshold_value=c.threshold_value,
                           threshold_unit=c.threshold_unit, extraction_method=c.extraction_method, tender=ctx)
             for c in tex.criteria]
    gating = {c.key: c.gating for c in tex.criteria}

    extracted = {}
    for name, paths in bids.items():
        files, warnings = expand([(p.name, p) for p in paths], tmp / name)
        docs = tp.local_pass(files, role="bidder")
        total, cached, errors = bp.run_ocr(docs, ocr)
        extracted[name] = (bp.extract(docs, specs), total, cached, errors)
    dates = [r.identity.get("bid_date") for r, *_ in extracted.values() if r.identity.get("bid_date")]
    fallback = max(dates) if dates else None

    out = {"tender": {"ref_no": tex.ref_no, "title": tex.title, **{k: v for k, v in ctx.items() if k not in ("title", "ref_no")}},
           "criteria": [{"key": s.key, "threshold": s.threshold_value, "gating": gating[s.key]} for s in specs], "bids": {}}
    for name, (res, total, cached, errors) in extracted.items():
        bctx = {**ctx, "bid_date": res.identity.get("bid_date") or fallback}
        verdicts = {}
        for s in specs:
            claims = [c for c in res.claims if c.criteria_key == s.key]
            v = evaluate_criterion(replace(s, tender=bctx), claims, s.label, reference)
            verdicts[s.key] = {"verdict": v.verdict, "unknown_kind": v.unknown_kind, "computation": v.computation,
                               "reason": v.reason, "evidence": [{"page": e.page, "value": e.value, "num": e.value_numeric, "quote": e.quote,
                                                                 "fetch_tier": e.detail.get("fetch_tier"),
                                                                 "fetched_from": e.detail.get("fetched_from")}
                                                                for e in v.evidence], "extra": v.extra}
        out["bids"][name] = {
            "firm": (res.identity.get("firm_name") or {}).get("value"), "bid_date": res.identity.get("bid_date"),
            "status": overall_status([(v["verdict"], gating[k]) for k, v in verdicts.items()]),
            "ocr": {"pages": total, "cached": cached, "errors": len(errors)}, "verdicts": verdicts,
            "documents": res.document_map,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tender", nargs="+", required=True, help="tender file(s), relative to --root")
    ap.add_argument("--bids", nargs="+", required=True, help="bid envelopes (globs allowed), relative to --root")
    ap.add_argument("--root", default=".")
    ap.add_argument("--online", action="store_true", help="send pages missing from the cache to Datalab")
    ap.add_argument("--reference", default=None, help="evaluation date, YYYY-MM-DD (default: today)")
    ap.add_argument("--json")
    args = ap.parse_args()
    root = Path(args.root).expanduser()
    tender = [root / t for t in args.tender]
    bids: dict[str, list[Path]] = {}
    for pattern in args.bids:
        for p in sorted(root.glob(pattern)):
            ref = bp.bid_ref_from_names([p.name]) or p.stem
            bids.setdefault(ref, []).append(p)
    ocr = get_ocr() if args.online else cache_only_ocr(settings.OCR_CACHE_DIR)
    reference = date.fromisoformat(args.reference) if args.reference else date.today()
    report = run(tender, bids, ocr, reference)

    keys = [c["key"] for c in report["criteria"] if c["gating"]]
    print(f"{report['tender']['ref_no']}  ·  E = {report['tender'].get('estimated_cost')}")
    print(f"{'bid':<10}{'status':<13}" + "".join(f"{k.replace('pmc_', '')[:12]:<13}" for k in keys) + "firm")
    for name, b in report["bids"].items():
        print(f"{name:<10}{b['status']:<13}" + "".join(f"{b['verdicts'][k]['verdict']:<13}" for k in keys) + (b["firm"] or ""))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
