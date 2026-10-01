"""One end-to-end run through the API, exactly as the UI drives it: upload tender -> wait for criteria
(+ OCR) -> upload bidder -> wait for the bid to be read (+ OCR) -> run evaluation. Records wall-clock
times, OCR pages and billed cost, and every result. Usage:

    uv run python scripts/e2e_run.py <label> <out.json> --tender f1 [f2 ...] --bid f1 [f2 ...] [--base URL]

For real time and cost figures, point it at a backend started with an empty data folder (and so an
empty OCR cache): TE_DATA_DIR=/tmp/e2e uv run uvicorn app.main:app --port 8011. Then read the run
with scripts/e2e_metrics.py.
"""

import argparse
import json
import mimetypes
import time
from pathlib import Path

import httpx



def files_field(paths):
    return [("files", (p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")) for p in paths]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("out")
    ap.add_argument("--tender", nargs="+", required=True)
    ap.add_argument("--bid", nargs="+", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8011/api/v1")
    a = ap.parse_args()
    BASE = a.base.rstrip("/")
    tender_files, bid_files = [Path(p) for p in a.tender], [Path(p) for p in a.bid]
    c = httpx.Client(timeout=900)
    log, marks = [], {}

    def mark(name):
        marks[name] = time.time()
        log.append(f"{time.strftime('%H:%M:%S')} {name}")
        print(log[-1], flush=True)

    mark("start")
    r = c.post(f"{BASE}/tenders", files=files_field(tender_files))
    r.raise_for_status()
    tid = r.json()["data"]["id"]
    mark("tender_uploaded")
    seen_ready = False
    while True:
        t = c.get(f"{BASE}/tenders/{tid}").json()["data"]
        if t["extraction_status"] == "ready" and not seen_ready:
            seen_ready = True
            mark("tender_criteria_ready")
        if t["extraction_status"] == "failed":
            mark("tender_failed")
            break
        if seen_ready and t.get("ocr_status") not in ("queued", "running"):
            mark("tender_ocr_done")
            break
        time.sleep(2)
    tender = c.get(f"{BASE}/tenders/{tid}").json()["data"]

    r = c.post(f"{BASE}/tenders/{tid}/bidders", files=files_field(bid_files))
    r.raise_for_status()
    bid_id = r.json()["data"]["id"]
    mark("bid_uploaded")
    while True:
        t = c.get(f"{BASE}/tenders/{tid}").json()["data"]
        b = next(x for x in t["bidders"] if x["id"] == bid_id)
        if b["status"] in ("ClaimsReady", "Failed") and b.get("ocr_status") not in ("queued", "running"):
            mark("bid_read" if b["status"] == "ClaimsReady" else "bid_failed")
            break
        if b.get("ocr_status") == "running" and "bid_ocr_started" not in marks:
            mark("bid_ocr_started")
        time.sleep(2)
    tender_after = c.get(f"{BASE}/tenders/{tid}").json()["data"]
    bidder = next(x for x in tender_after["bidders"] if x["id"] == bid_id)

    r = c.post(f"{BASE}/tenders/{tid}/evaluate")
    mark("evaluated")
    evaluation = r.json().get("data")
    out = {"label": a.label, "tender_files": [str(p) for p in tender_files], "bid_files": [str(p) for p in bid_files],
           "tender_id": tid, "bidder_id": bid_id, "marks": marks, "log": log,
           "tender": tender, "bidder": bidder, "evaluation": evaluation,
           "evaluate_status": r.status_code, "evaluate_message": r.json().get("message")}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("saved", a.out, flush=True)


if __name__ == "__main__":
    main()
