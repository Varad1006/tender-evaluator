"""Golden end-to-end test: the seeded tender + three fabricated bids, through the real API.

This is the demo script as a test. If it passes, the five-minute demo works.
"""

import pytest

BIDS = {
    "sunrise_energy_technical_financial_bid.pdf": "Sunrise Energy Systems Pvt Ltd",
    "greenvolt_infra_combined_bid.pdf": "GreenVolt Infra Solutions",
    "urja_solar_tech_combined_bid.pdf": "Urja Solar Tech Pvt Ltd",
}

EXPECTED_CRITERIA = {  # key -> (tender page, clause, threshold display)
    "gst_registration": (9, "2.1", "Required"),
    "epf_esic_registration": (9, "2.2", "Both required"),
    "completion_days": (11, "4.1", "≤ 90 days"),
    "avg_turnover": (14, "5.1", "≥ ₹5,00,00,000"),
    "similar_experience": (16, "6.1", "≥ ₹2 Cr, last 5 yrs"),
    "manufacturer_authorization": (18, "7.2", "Required"),
    "warranty_years": (19, "7.4", "≥ 10 years"),
    "site_engineer": (20, "8.1", "Required"),
}

# Everything passes except these (bidder, criterion) -> (verdict, unknown_kind)
EXCEPTIONS = {
    ("GreenVolt Infra Solutions", "avg_turnover"): ("fail", None),
    ("Urja Solar Tech Pvt Ltd", "warranty_years"): ("unknown", "conflict"),
    ("Urja Solar Tech Pvt Ltd", "epf_esic_registration"): ("unknown", "not_found"),
}


@pytest.fixture(scope="module")
def evaluated(client, seed_dir):
    with open(seed_dir / "PMC_SolarRooftop_Tender_2026.pdf", "rb") as fh:
        r = client.post("/api/v1/tenders", files={"file": ("PMC_SolarRooftop_Tender_2026.pdf", fh, "application/pdf")})
    assert r.status_code == 201, r.text
    tender_id = r.json()["data"]["id"]
    for name in BIDS:
        with open(seed_dir / name, "rb") as fh:
            r = client.post(f"/api/v1/tenders/{tender_id}/bidders", files={"file": (name, fh, "application/pdf")})
        assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/tenders/{tender_id}/evaluate")
    assert r.status_code == 200, r.text
    return tender_id, r.json()["data"]


def test_tender_metadata_and_criteria(client, evaluated):
    tender_id, _ = evaluated
    t = client.get(f"/api/v1/tenders/{tender_id}").json()["data"]
    assert t["extraction_status"] == "ready"
    assert t["ref_no"] == "PMC/ELEC/2026/0417"
    assert t["authority"] == "Pune Municipal Corporation — Electrical Department"
    assert t["award_mechanism"] == "L1"
    assert t["award_mechanism_citation"]["page"] == 21
    assert t["status"] == "Under Evaluation"
    got = {c["key"]: (c["source_page"], c["source_clause"], c["threshold_display"]) for c in t["criteria"]}
    assert got == EXPECTED_CRITERIA
    assert {b["name"] for b in t["bidders"]} == set(BIDS.values())
    assert {b["name"]: b["price"] for b in t["bidders"]} == {
        "Sunrise Energy Systems Pvt Ltd": 4_250_000,
        "GreenVolt Infra Solutions": 3_990_000,
        "Urja Solar Tech Pvt Ltd": 4_480_000,
    }


def test_verdict_matrix(evaluated):
    _, data = evaluated
    crit = {c["id"]: c["key"] for c in data["criteria"]}
    names = {b["id"]: b["name"] for b in data["bidders"]}
    assert len(data["verdicts"]) == 8 * 3
    for v in data["verdicts"]:
        expected = EXCEPTIONS.get((names[v["bidder_id"]], crit[v["criterion_id"]]), ("pass", None))
        assert (v["verdict"], v["unknown_kind"]) == expected, (names[v["bidder_id"]], crit[v["criterion_id"]], v["reason"])


def test_every_decided_verdict_has_dual_citation(evaluated):
    """FSD success metric: 100% of pass/fail verdicts carry requirement + evidence citations."""
    _, data = evaluated
    for v in data["verdicts"]:
        assert v["requirement"]["page"] and v["requirement"]["quote"]
        if v["verdict"] in ("pass", "fail"):
            ev = v["evidence"][0]
            assert ev["page"] and ev["quote"] and ev["document_id"] and ev["document_name"]


def test_contradiction_keeps_both_citations(evaluated):
    _, data = evaluated
    crit = {c["key"]: c["id"] for c in data["criteria"]}
    urja = next(b["id"] for b in data["bidders"] if b["name"].startswith("Urja"))
    v = next(v for v in data["verdicts"] if v["bidder_id"] == urja and v["criterion_id"] == crit["warranty_years"])
    assert sorted(e["page"] for e in v["evidence"]) == [9, 27]
    esic = next(v for v in data["verdicts"] if v["bidder_id"] == urja and v["criterion_id"] == crit["epf_esic_registration"])
    assert "ESIC" in esic["reason"] and [e["page"] for e in esic["evidence"]] == [20]


def test_ranking(evaluated):
    _, data = evaluated
    ranking = data["ranking"]
    assert ranking["mechanism"] == "L1" and ranking["warning"] is None
    kinds = [(e["rank_label"], e["name"], e["kind"]) for e in ranking["entries"]]
    assert kinds == [
        ("L1", "Sunrise Energy Systems Pvt Ltd", "qualified"),
        ("?", "Urja Solar Tech Pvt Ltd", "pending"),
        ("✕", "GreenVolt Infra Solutions", "disqualified"),
    ]
    greenvolt = ranking["entries"][2]
    assert "Turnover" in greenvolt["basis"]
    assert greenvolt["issues"][0]["evidence"][0]["page"] == 5
    assert data["eligibility"] == {"eligible": 1, "pending": 1, "disqualified": 1}


def test_pdf_serving_and_isolation(client, evaluated):
    tender_id, data = evaluated
    assert client.get(f"/api/v1/tenders/{tender_id}/document").headers["content-type"] == "application/pdf"
    doc_id = data["bidders"][0]["document"]["id"]
    assert client.get(f"/api/v1/documents/{doc_id}/file").content.startswith(b"%PDF")
    r = client.get("/api/v1/tenders/does-not-exist")
    assert r.status_code == 404 and r.json() == {"success": False, "error": {"code": "AUTH_003", "message": "Not found."}}


def test_rejects_non_pdf(client, evaluated):
    tender_id, _ = evaluated
    r = client.post(f"/api/v1/tenders/{tender_id}/bidders", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VAL_001"
