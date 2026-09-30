"""Golden checks against the real PMC / MahaTender corpus (local tier only — no network, no cost).

Runs only when the corpus is available: set TE_CORPUS_DIR, or keep it at the default path.
Expected values were verified by reading the tender pages (including the scanned ones).
"""

import os
import tempfile
from pathlib import Path

import pytest

CORPUS = Path(os.environ.get("TE_CORPUS_DIR", Path.home() / "Desktop" / "Dataset Tenders"))
pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason="PMC corpus not available")


def _run(name: str):
    from app.services.tender_processing import process_bundle

    files = [(f.name, f) for f in sorted((CORPUS / name).rglob("*")) if f.is_file() and not f.name.startswith(".")]
    with tempfile.TemporaryDirectory() as tmp:
        return process_bundle(files, Path(tmp), use_ocr=False)


def _facts(result):
    return {f["key"]: f for f in result.extraction.facts}


def test_ward_notice_facts_and_lakhs_template_error():
    r = _run("work_2287082")
    f = _facts(r)
    assert f["ref_no"]["value"] == "PMC/DMC-5/VISHRAM/2026/205"
    assert f["estimated_cost"]["value"] == 423647 and f["estimated_cost"]["status"] == "consistent"
    assert len({s["method"] for s in f["estimated_cost"]["sources"]}) >= 5  # notice box, WIF, B-1, abstract, BOQ...
    assert f["security_deposit"]["value"] == 21182 and "EMD converted" in f["security_deposit"]["note"]
    assert f["completion_months"]["value"] == 9
    c = {x.key: x for x in r.extraction.criteria}
    turnover = c["pmc_max_turnover"]
    assert turnover.threshold_value == 423647 and turnover.status == "located" and turnover.source.page == 10
    assert any("template unit error" in n for n in turnover.notes)
    assert c["pmc_similar_works"].status == "located_marathi"  # Marathi clause cited by its Devanagari digits
    assert r.extraction.award_mechanism == "L1"


def test_turnover_formula_uses_completion_period():
    c = {x.key: x for x in _run("work_2288563").extraction.criteria}
    assert c["pmc_max_turnover"].threshold_value == pytest.approx(844085 * 0.75 / (7 / 12), rel=1e-6)


def test_scanned_bundle_is_reported_not_guessed():
    r = _run("work_2289779")
    doc = next(d for d in r.docs if d.name == "scancopies_235.pdf")
    assert {s.quality.kind for s in doc.scans} == {"image", "blank"}  # blank = "श्री गणेशाय नमः"-only sheets, never OCR'd
    assert [s.number for s in doc.scans if s.quality.kind == "blank"] == [36, 48]
    assert all(c.status == "derived" for c in r.extraction.criteria)  # honest: nothing located without OCR
    assert _facts(r)["estimated_cost"]["value"] == 24066515  # still known from the BOQ spreadsheet


def test_drainage_conditions_volume_checklist_and_rar():
    r = _run("work_2291063")
    assert any(d.name == "TERMSCONDHION.pdf" and "Tenderdocuments.rar" in d.origin for d in r.docs)
    f = _facts(r)
    assert f["estimated_cost"]["value"] == 4164895 and f["total_estimated_cost"]["value"] == 4999970
    assert f["completion_months"]["value"] == 12
    labels = [c.label for c in r.extraction.criteria if c.extraction_method == "checklist"]
    assert any("Works in Hand" in x for x in labels) and any("PAN Card" in x for x in labels)
    assert not any(c.key == "completion_days" for c in r.extraction.criteria)  # execution clauses are not eligibility


def test_it_rfp_prequalification():
    from app.services.tender_processing import process_bundle

    rfp = CORPUS / "Archive" / "FinalPTSurvey RFP New.pdf"
    if not rfp.exists():
        pytest.skip("RFP not in corpus")
    with tempfile.TemporaryDirectory() as tmp:
        r = process_bundle([(rfp.name, rfp)], Path(tmp), use_ocr=False)
    c = {x.key: x for x in r.extraction.criteria}
    assert c["avg_turnover"].threshold_value == 10_000_000
    assert c["min_staff_strength"].threshold_value == 25
    assert c["years_in_existence"].threshold_value == 3
    assert "positive_net_worth" in c and "min_similar_projects" in c
    assert r.extraction.award_detail["technical_min_pct"] == 70


def test_accuracy_against_ground_truth_with_cached_ocr():
    """Every hand-verified fact, threshold, quantity and label in tests/data/corpus_truth.json,
    with the OCR tier replayed from the local cache (a page missing from the cache just stays
    unread: the network is never called)."""
    import json
    import sys

    import httpx

    from app.adapters.datalab_ocr import DatalabOcr
    from app.services.bundle import expand
    from app.services.tender_processing import extract, local_pass, run_ocr

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.score_corpus import TRUTH, entry, score_tender

    # The suite points TE_DATA_DIR at a temp dir; the cache to replay is the real one (read only).
    cache = Path(os.environ.get("TE_OCR_CACHE_DIR", Path(__file__).resolve().parents[1] / "data" / "ocr_cache"))
    if not cache.exists():
        pytest.skip("no OCR cache on this machine")

    def offline(request):
        raise httpx.ConnectError("offline test", request=request)

    ocr = DatalabOcr("cache-only", cache, client=httpx.Client(transport=httpx.MockTransport(offline)),
                     sleep=lambda s: None, requests_per_minute=10_000)
    truth = json.loads(TRUTH.read_text())
    misses = []
    scored, absent = 0, []
    for name, want in truth.items():
        if name.startswith("_"):
            continue
        root = CORPUS / name if (CORPUS / name).exists() else CORPUS / "Archive" / name
        # The ground truth grows as tenders are collected, and no two machines hold the same
        # download folder. A tender that is not on this one is skipped, not counted as a miss.
        if not root.exists():
            absent.append(name)
            continue
        scored += 1
        files = [(f.name, f) for f in sorted(root.rglob("*")) if f.is_file() and not f.name.startswith(".")] \
            if root.is_dir() else [(root.name, root)]
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _ = expand(files, Path(tmp))
            docs = local_pass(bundle)
            run_ocr(docs, ocr)
            _, missed = score_tender(entry(name, extract(docs)), want)
        misses += [f"{name}: {m}" for m in missed]
    if not scored:
        pytest.skip(f"none of the {len(absent)} ground-truth tenders are in {CORPUS}")
    assert not misses, "\n".join(misses) + (f"\n(skipped, not on this machine: {', '.join(absent)})" if absent else "")
