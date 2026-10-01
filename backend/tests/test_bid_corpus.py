"""Real bids, scored against ground truth written by reading them — on the machine that has them.

Every ground-truth file in tests/local/ (git-ignored: it holds bidders' figures) names a tender, its
bid envelopes and the verdict each criterion should reach. The run replays the OCR cache; a page
missing from it stays unread rather than reaching the network. Skips wherever the files are absent.
"""

import json
import os
from datetime import date
from pathlib import Path

import pytest

LOCAL = Path(__file__).resolve().parent / "local"
TRUTHS = sorted(LOCAL.glob("*_truth.json")) if LOCAL.exists() else []
CACHE = Path(os.environ.get("TE_OCR_CACHE_DIR", Path(__file__).resolve().parents[1] / "data" / "ocr_cache"))


@pytest.mark.skipif(not TRUTHS or not CACHE.exists(), reason="no local bid corpus on this machine")
@pytest.mark.parametrize("truth_file", TRUTHS, ids=lambda p: p.stem)
def test_bid_verdicts_match_ground_truth(truth_file):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.bid_report import cache_only_ocr, run
    from scripts.score_bids import score
    from app.services.bidder_processing import bid_ref_from_names

    truth = json.loads(truth_file.read_text())
    root = Path(truth["root"]).expanduser()
    tender = [root / t for t in truth["tender"]]
    if not all(p.exists() for p in tender):
        pytest.skip("tender files not on this machine")
    bids: dict = {}
    for p in sorted(root.glob(truth["bids_glob"])):
        bids.setdefault(bid_ref_from_names([p.name]) or p.stem, []).append(p)
    bids = {k: v for k, v in bids.items() if k in truth["bids"]}
    if len(bids) != len(truth["bids"]):
        pytest.skip("bid envelopes not on this machine")
    report = run(tender, bids, cache_only_ocr(CACHE), date.fromisoformat(truth["reference"]))
    checks, misses = score(report, truth)
    assert not misses, f"{len(misses)}/{checks} wrong:\n" + "\n".join(misses)
