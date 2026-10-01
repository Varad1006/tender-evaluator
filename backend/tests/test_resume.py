"""A server stop mid-job (the desktop app stops its preview servers when it closes) must not leave
a tender 'extracting' or a bidder 'Extracting' forever — and a job that dies every time must not
turn every restart into another crash."""

from app.db import SessionLocal
from app.models import Bidder, Meta, Tender, TenderDocument
from app.services.pipeline import RESUME_LIMIT, resume_interrupted, run_jobs


def _upload(client, seed_dir):
    with open(seed_dir / "PMC_SolarRooftop_Tender_2026.pdf", "rb") as fh:
        r = client.post("/api/v1/tenders", files={"file": ("PMC_SolarRooftop_Tender_2026.pdf", fh, "application/pdf")})
    tender_id = r.json()["data"]["id"]
    with open(seed_dir / "greenvolt_infra_combined_bid.pdf", "rb") as fh:
        r = client.post(f"/api/v1/tenders/{tender_id}/bidders",
                        files={"file": ("greenvolt_infra_combined_bid.pdf", fh, "application/pdf")})
    return tender_id, r.json()["data"]["id"]


def _interrupt(tender_id: str, bidder_id: str) -> None:
    with SessionLocal() as s:
        s.get(Tender, tender_id).extraction_status = "extracting"
        s.get(Bidder, bidder_id).status = "Extracting"
        s.commit()


def test_interrupted_jobs_are_resumed_cleanly(client, seed_dir):
    tender_id, bidder_id = _upload(client, seed_dir)
    before = client.get(f"/api/v1/tenders/{tender_id}").json()["data"]
    _interrupt(tender_id, bidder_id)

    jobs = resume_interrupted()
    assert {arg for _, arg in jobs} >= {tender_id, bidder_id}
    run_jobs([j for j in jobs if j[1] in (tender_id, bidder_id)])

    after = client.get(f"/api/v1/tenders/{tender_id}").json()["data"]
    assert after["extraction_status"] == "ready"
    assert [c["key"] for c in after["criteria"]] == [c["key"] for c in before["criteria"]]
    with SessionLocal() as s:  # rebuilt, not duplicated
        assert s.query(TenderDocument).filter_by(tender_id=tender_id).count() == len(before["documents"])
        assert s.get(Bidder, bidder_id).status == "ClaimsReady"


def test_a_job_that_keeps_dying_is_failed_not_retried_forever(client, seed_dir):
    tender_id, bidder_id = _upload(client, seed_dir)
    for _ in range(RESUME_LIMIT):
        _interrupt(tender_id, bidder_id)
        assert tender_id in {arg for _, arg in resume_interrupted()}
    _interrupt(tender_id, bidder_id)
    assert not {tender_id, bidder_id} & {arg for _, arg in resume_interrupted()}
    with SessionLocal() as s:
        t, b = s.get(Tender, tender_id), s.get(Bidder, bidder_id)
        assert t.extraction_status == "failed" and "repeated server restarts" in t.extraction_error
        assert b.status == "Failed"


def test_only_consecutive_interruptions_count(client, seed_dir):
    tender_id, bidder_id = _upload(client, seed_dir)
    for _ in range(RESUME_LIMIT + 2):  # each resumed run finishes before the next stop
        _interrupt(tender_id, bidder_id)
        jobs = [j for j in resume_interrupted() if j[1] in (tender_id, bidder_id)]
        assert {arg for _, arg in jobs} == {tender_id, bidder_id}
        run_jobs(jobs)
    with SessionLocal() as s:
        assert s.get(Bidder, bidder_id).status == "ClaimsReady"
        assert s.get(Meta, f"rb:{bidder_id}") is None and s.get(Meta, f"rt:{tender_id}") is None


def test_counters_of_deleted_bids_are_dropped(client, seed_dir):
    tender_id, bidder_id = _upload(client, seed_dir)
    _interrupt(tender_id, bidder_id)
    resume_interrupted()  # counted, then the server stops again before the jobs run
    client.delete(f"/api/v1/tenders/{tender_id}")
    resume_interrupted()
    with SessionLocal() as s:
        assert s.get(Meta, f"rb:{bidder_id}") is None and s.get(Meta, f"rt:{tender_id}") is None
