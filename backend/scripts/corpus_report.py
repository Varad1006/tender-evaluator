"""Run the extraction pipeline over a folder of MahaTender downloads and print what it found.

    uv run python scripts/corpus_report.py "/path/to/Dataset Tenders" [--ocr] [--only work_2291063]

Each top-level work_* folder (or .zip) is treated as one tender bundle. With --ocr the Datalab
tier runs for pages the local pass cannot read (needs DATALAB_API_KEY; results are cached).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the backend root: app/ and scripts/

from app.services.tender_processing import process_bundle  # noqa: E402
from scripts.score_corpus import entry  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--json", help="write a machine-readable report here")
    args = ap.parse_args()

    root = Path(args.root)
    tenders = sorted(p for p in root.iterdir() if p.name.startswith("work_") and (p.is_dir() or p.suffix == ".zip"))
    extra = list(root.glob("*.pdf")) + (list((root / "Archive").glob("*.pdf")) if (root / "Archive").exists() else [])
    report = []
    for t in tenders + sorted(extra):
        if args.only and args.only not in t.name:
            continue
        files = [(f.name, f) for f in sorted(t.rglob("*")) if f.is_file() and not f.name.startswith(".")] if t.is_dir() else [(t.name, t)]
        started = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            result = process_bundle(files, Path(tmp), use_ocr=args.ocr)
        ex = result.extraction
        print("=" * 110)
        print(f"{t.name}: {ex.ref_no} | {ex.title}")
        print(f"  local {result.timings.get('local', 0):.1f}s, ocr {result.timings.get('ocr', 0):.1f}s "
              f"({result.ocr_pages} pages, {result.ocr_cached} cached), total {time.time() - started:.1f}s")
        for d in ex.documents:
            print(f"  doc {d['doc_type']:<15} {d['name'][:48]:<48} {d['kinds']}")
        print(f"  award: {ex.award_mechanism} — {(ex.award_detail or {}).get('label')}")
        for f in ex.facts:
            print(f"  fact {f['label']:<34} {f['display']:<22} {f['status']:<13} "
                  f"{', '.join(sorted({s['method'] for s in f['sources']}))}{'  NOTE: ' + f['note'] if f.get('note') else ''}")
        for c in ex.criteria:
            src = f"p.{c.source.page}" if c.source else "—"
            print(f"  [{c.status:<15}] {c.label[:58]:<58} {c.threshold_display[:44]:<44} {src:<6} {c.extraction_method}")
            for n in c.notes[:3]:
                print(f"        · {n[:170]}")
        for w in ex.warnings + result.warnings:
            print(f"  ! {w[:170]}")
        report.append(entry(t.name, ex, result.warnings))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
