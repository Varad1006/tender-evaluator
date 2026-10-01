"""Metrics for one e2e run, and a citation check: every quote the system shows (tender requirement and
bidder evidence) is looked up on the page it cites — in that page's OCR output (from the run's own
cache) or the PDF's text layer. A quote not found on its page is a citation the documents do not support.

    uv run python scripts/e2e_metrics.py <e2e.json> <the backend's TE_DATA_DIR>
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber

DEV = str.maketrans("०१२३४५६७८९", "0123456789")


def norm(s: str) -> str:
    s = (s or "").translate(DEV)
    s = re.sub(r"<[^>]+>|[|*#`_\\]", " ", s)
    return re.sub(r"\s+", "", s).lower()


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Pages:
    def __init__(self, files: list[Path], cache: Path):
        self.by_name = {p.name: p for p in files}
        self.cache, self._text, self._hash = cache, {}, {}

    def text(self, name: str | None, page: int | None) -> str:
        if not name or not page or name not in self.by_name:
            return ""
        key = (name, page)
        if key in self._text:
            return self._text[key]
        p = self.by_name[name]
        parts = []
        if p.suffix.lower() == ".pdf":
            h = self._hash.setdefault(name, sha(p))
            d = self.cache / h[:2] / h
            parts += [f.read_text() for f in d.glob(f"*-p{page}.md")] if d.exists() else []
            try:
                with pdfplumber.open(p) as pdf:
                    if 0 < page <= len(pdf.pages):
                        parts.append(pdf.pages[page - 1].extract_text() or "")
            except Exception:
                pass
        self._text[key] = "\n".join(parts)
        return self._text[key]


def found(quote: str, page_text: str) -> bool:
    """The quote (or each of its ' … '-joined parts) on the page, ignoring spacing and table marks."""
    if not quote:
        return True
    page = norm(page_text)
    if not page:
        return False
    for part in [q for q in re.split(r"\s…\s|; ", quote) if q.strip()]:
        n = norm(part)
        probe = n[:50] if len(n) > 50 else n
        if len(probe) >= 8 and probe not in page:
            return False
    return True


def main():
    run = json.loads(Path(sys.argv[1]).read_text())
    data = Path(sys.argv[2])
    cache = data / "ocr_cache"
    m = run["marks"]
    t, b, ev = run["tender"], run["bidder"], run["evaluation"] or {}
    tender_pages = Pages([Path(x) for x in run["tender_files"]], cache)
    bid_pages = Pages([Path(x) for x in run["bid_files"]], cache)

    def dt(a, z):
        return round(m[z] - m[a], 1) if a in m and z in m else None

    to, bo = t.get("ocr_progress") or {}, b.get("ocr_progress") or {}
    print(f"# {run['label']}")
    print("## time (s)")
    print(f"  tender upload request      {dt('start', 'tender_uploaded')}")
    print(f"  criteria ready (local)     {dt('tender_uploaded', 'tender_criteria_ready')}  (local tier {(t.get('timings') or {}).get('local_seconds')})")
    print(f"  tender OCR done            {dt('tender_criteria_ready', 'tender_ocr_done')}  (ocr {(t.get('timings') or {}).get('ocr_seconds')})")
    print(f"  bid upload request         {dt('tender_ocr_done', 'bid_uploaded')}")
    print(f"  bid read (local + OCR)     {dt('bid_uploaded', next((k for k in ('bid_read', 'bid_failed') if k in m), 'bid_read'))}  (timings {b.get('timings')})")
    print(f"  evaluation                 {dt(next((k for k in ('bid_read', 'bid_failed') if k in m), 'bid_read'), 'evaluated')}")
    print(f"  TOTAL                      {dt('start', 'evaluated')}")
    print("## OCR")
    print(f"  tender: {to.get('total')} pages planned, {to.get('cached')} cached, billed {to.get('pages_billed')} pages, "
          f"{to.get('requests')} requests, {to.get('cost_cents')}¢, errors {to.get('errors')}")
    print(f"  bid:    {bo.get('total')} pages planned, {bo.get('cached')} cached, billed {bo.get('pages_billed')} pages, "
          f"{bo.get('requests')} requests, {bo.get('cost_cents')}¢, errors {bo.get('errors')}")
    total_cents = (to.get("cost_cents") or 0) + (bo.get("cost_cents") or 0)
    print(f"  TOTAL OCR COST: {total_cents:.1f}¢ = ${total_cents / 100:.3f}  (LLM: disabled, $0)")
    print("## tender")
    print(f"  {t.get('ref_no')} | {t.get('title')}")
    for f in t.get("facts", []):
        print(f"  fact {f.get('label')}: {f.get('display')} ({f.get('status')})")
    print(f"  award: {t.get('award_mechanism')}")
    for c in t.get("criteria", []):
        print(f"  [{c['status']:<9}] {c['label'][:62]:<62} {c['threshold_display'][:46]:<46} p.{c.get('source_page')} "
              f"{'' if c.get('gating', True) else '(not a gate)'}")
    for w in t.get("warnings", [])[:8]:
        print(f"  ! {w[:200]}")
    print("## bidder")
    docs = b.get("documents", [])
    print(f"  name: {b.get('name')} | identity: {json.dumps(b.get('identity'), ensure_ascii=False)[:300]}")
    print(f"  bid date: {(b.get('facts') or {}).get('bid_date')} | docs: " + "; ".join(
        f"{d['file_name']} {d.get('page_count')}p kinds={ {k: (d.get('page_kinds') or []).count(k) for k in set(d.get('page_kinds') or [])} }" for d in docs))
    for w in b.get("warnings", [])[:10]:
        print(f"  ! {w[:220]}")
    print("## verdicts")
    crit = {c["id"]: c for c in ev.get("criteria", [])}
    bad_req, bad_ev, checked = [], [], 0
    for v in ev.get("verdicts", []):
        c = crit.get(v["criterion_id"], {})
        print(f"  {v['verdict'].upper():<8} {c.get('label', '')[:60]:<60} | {v.get('computation') or ''}")
        print(f"           reason: {v['reason'][:330]}")
        req = v.get("requirement") or {}
        if req.get("quote"):
            checked += 1
            name = c.get("source_document_name")
            if not found(req["quote"], tender_pages.text(name, req.get("page"))):
                bad_req.append((c.get("label"), name, req.get("page"), req["quote"][:160]))
        for e in v.get("evidence", []):
            if e.get("quote"):
                checked += 1
                if not found(e["quote"], bid_pages.text(e.get("document_name"), e.get("page"))):
                    bad_ev.append((c.get("label"), e.get("document_name"), e.get("page"), e["quote"][:160]))
            print(f"           ev p.{e.get('page')} {e.get('value')}: {(e.get('quote') or '')[:150]}")
    print(f"  overall: {[x.get('overall') for x in ev.get('bidders', [])]}")
    print("## citation check")
    print(f"  {checked} quotes checked; requirement quotes not on cited page: {len(bad_req)}; evidence quotes not on cited page: {len(bad_ev)}")
    for x in bad_req + bad_ev:
        print(f"   ✗ {x}")


if __name__ == "__main__":
    main()
