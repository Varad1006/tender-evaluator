"""Fetching a bidder's documents by what the tender requires (document_fetch), on invented bids.

Every page here is written for the test: the shapes are the ones real bids have — a merged scan of
many documents, certificates back to back, a supplier's invoice carrying someone else's numbers —
but no real document's text, name or figure."""

from pathlib import Path

from app.adapters.pdf_parser import PageScan, ParsedDocument, ParsedPage, Sentence
from app.adapters.readability import PageQuality
from app.services import bidder_extraction, document_fetch
from app.services.bidder_documents import classify_pages
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services.bidder_processing import extract
from app.services.tender_processing import ProcessedDoc


def _page(n: int, text: str) -> ParsedPage:
    return ParsedPage(number=n, text=text, lines=[l for l in text.splitlines() if l.strip()],
                      quality=PageQuality("english"), raw_text=text)


def _parsed(*texts: str) -> ParsedDocument:
    pages = [_page(i, t) for i, t in enumerate(texts, start=1)]
    sentences = [Sentence(page=p.number, text=line, section="") for p in pages for line in p.lines]
    return ParsedDocument(pages=pages, sentences=sentences)


def _bid(*texts: str, name: str = "bid.pdf", doc_id: str = "d1") -> ProcessedDoc:
    parsed = _parsed(*texts)
    scans = [PageScan(p.number, p.text, p.quality, False) for p in parsed.pages]
    return ProcessedDoc(doc_id, name, name, Path(name), "combined", "h", scans, parsed)


FILLER = " The details stated above are correct to the best of our knowledge and records." * 2
REGISTRATION = "CONTRACTOR'S REGISTRATION CERTIFICATE\nRegistered to M/s Example Constructions, Proprietor/Partners A B in Class III" + FILLER
COMPLETION_1 = "WORK COMPLETION CERTIFICATE\nM/s Example Constructions completed the road work satisfactorily." + FILLER
COMPLETION_2 = "WORK COMPLETION CERTIFICATE\nM/s Example Constructions completed the drain work satisfactorily." + FILLER
COMPLETION_OTHER = "WORK COMPLETION CERTIFICATE\nM/s Other Builders, completed the bridge work satisfactorily." + FILLER
GST = "GOODS AND SERVICES TAX — Registration Certificate (GST REG-06)\nGSTIN 27ABCDE1234F1Z5 Legal name: Example Constructions" + FILLER
INVOICE = "TAX INVOICE\nSupplier: Cement Traders\nMarker: supplier's figure" + FILLER


def _spec(key: str, label: str, requirement: str = "", **params) -> CriterionSpec:
    return CriterionSpec(key=key, label=label, operator="exists", params=params, requirement=requirement)


# --- the plan ----------------------------------------------------------------------------------

def test_each_requirement_is_planned_by_criterion_concept_or_its_own_words():
    needs = {n.key: n for n in document_fetch.plan([
        _spec("pmc_emd", "Earnest Money Deposit"),
        _spec("doc_blacklist", "Declaration of non-blacklisting", "The bidder shall not be blacklisted by any government"),
        _spec("doc_fire", "Copy of fire safety licence", terms=["fire safety licence"]),
        _spec("bid_validity", "Bid Validity Period"),
    ])}
    assert needs["pmc_emd"].primary == ("emd_receipt",) and needs["pmc_emd"].basis == "criterion"
    assert needs["doc_blacklist"].basis == "concept" and "affidavit" in needs["doc_blacklist"].primary
    assert needs["doc_fire"].basis == "wording" and {"fire", "safety", "licence"} <= set(needs["doc_fire"].terms)
    assert needs["bid_validity"].basis == "none"


# --- the inventory -----------------------------------------------------------------------------

def test_a_bid_is_split_into_its_documents():
    parsed = _parsed(REGISTRATION, COMPLETION_1, COMPLETION_2, "Annexure: measurements continued on this page." + FILLER)
    docs = document_fetch.inventory([("d1", "bid.pdf", parsed, classify_pages(parsed))])
    assert [(d.kind, d.first, d.last) for d in docs] == [
        ("registration", 1, 1),
        ("completion_certificate", 2, 2),  # two certificates back to back are two documents
        ("completion_certificate", 3, 4),  # and a heading-less continuation belongs to the one before it
    ]
    assert docs[1].firm == "Example Constructions"


# --- fetch -------------------------------------------------------------------------------------

def test_each_requirement_gets_its_documents_and_a_status():
    parsed = _parsed(REGISTRATION, COMPLETION_1, COMPLETION_OTHER)
    files = [("d1", "bid.pdf", parsed, classify_pages(parsed))]
    inv = document_fetch.inventory(files)
    needs = document_fetch.plan([_spec("pmc_registration", "Registration"), _spec("pmc_similar_works", "Similar works"),
                                 _spec("pmc_emd", "EMD"), _spec("bid_validity", "Validity")])
    got = {r.key: r for r in document_fetch.fetch(needs, inv, files)}
    assert got["pmc_registration"].status == "found" and got["pmc_registration"].documents[0].doc.first == 1
    works = got["pmc_similar_works"]
    assert works.status == "found" and [f.doc.first for f in works.documents] == [2, 3]
    assert [f.doc.firm for f in works.documents] == ["Example Constructions", "Other Builders"]  # shown, never judged here
    assert got["pmc_emd"].status == "not_found" and got["bid_validity"].status == "not_a_gate"


def test_a_document_nothing_else_covers_is_found_by_the_tenders_own_words():
    parsed = _parsed("FIRE SAFETY LICENCE\nissued to Example Constructions for the storage yard." + FILLER, INVOICE)
    files = [("d1", "bid.pdf", parsed, classify_pages(parsed))]
    needs = document_fetch.plan([_spec("doc_fire", "Copy of fire safety licence", terms=["fire safety licence"])])
    [req] = document_fetch.fetch(needs, document_fetch.inventory(files), files)
    assert req.status == "found" and [(f.role, f.doc.first) for f in req.documents] == [("heading", 1)]


def test_the_masked_view_keeps_page_numbers_and_hides_the_rest():
    parsed = _parsed(REGISTRATION, COMPLETION_1, GST)
    view = document_fetch.masked(parsed, {2})
    assert [p.number for p in view.pages] == [1, 2, 3]
    assert view.pages[0].text == "" and view.pages[2].text == "" and "drain" not in view.pages[1].text
    assert {s.page for s in view.sentences} == {2}


# --- reading the fetched documents first -------------------------------------------------------

def _marker_finder(doc, spec):
    """A stand-in reader that reports every page carrying the word "Marker" or a GSTIN."""
    return [Claim(spec.key, "seen", p.number, p.text.splitlines()[1], "test") for p in doc.pages
            if "Marker" in (p.text or "") or "GSTIN" in (p.text or "")]


def test_evidence_is_read_from_the_fetched_documents_before_the_rest_of_the_bid(monkeypatch):
    monkeypatch.setitem(bidder_extraction.FINDERS, "probe", _marker_finder)
    monkeypatch.setitem(document_fetch.BY_KEY, "probe", (("gst",), ()))
    result = extract([_bid(REGISTRATION, GST, INVOICE)], [_spec("probe", "GST registration")])
    probe = [c for c in result.claims if c.criteria_key == "probe"]
    assert [c.page for c in probe] == [2]  # the supplier's invoice on p.3 is never read for this
    assert probe[0].detail["fetch_tier"] == "documents" and probe[0].detail["fetched_from"]["kind"] == "gst"
    [req] = [r for r in result.document_map["requirements"] if r["key"] == "probe"]
    assert req["status"] == "found" and req["documents"][0]["from"] == 2


def test_without_the_document_the_whole_bid_is_read_and_says_so(monkeypatch):
    monkeypatch.setitem(bidder_extraction.FINDERS, "probe", _marker_finder)
    monkeypatch.setitem(document_fetch.BY_KEY, "probe", (("gst",), ()))
    result = extract([_bid(REGISTRATION, INVOICE)], [_spec("probe", "GST registration")])
    probe = [c for c in result.claims if c.criteria_key == "probe"]
    assert [c.page for c in probe] == [2] and probe[0].detail["fetch_tier"] == "whole_bid"
    assert "fetched_from" not in probe[0].detail


def test_an_absence_is_always_checked_across_the_whole_bid():
    """"No joint venture" is answered by what is NOT in the bid: an agreement two documents away
    from the registration certificate must still be seen."""
    jv = "JOINT VENTURE AGREEMENT between Example Constructions and Partner Works for this tender." + FILLER
    result = extract([_bid(REGISTRATION, COMPLETION_1, jv)],
                     [CriterionSpec(key="no_joint_venture", label="Sole bidder", operator="exists", params={"terms": ["sole"]})])
    claims = [c for c in result.claims if c.criteria_key == "no_joint_venture"]
    assert any(c.detail.get("jv") for c in claims) and any(c.detail.get("constitution") for c in claims)
    [req] = [r for r in result.document_map["requirements"] if r["key"] == "no_joint_venture"]
    assert req["reads"] == "whole_bid" and req["documents"][0]["kind"] == "registration"


def test_the_firm_named_on_a_page_is_read_in_either_script_and_never_across_lines():
    marathi = "कामाचा दाखला\nमे. उदाहरण कन्स्ट्रक्शन यांनी खालील काम पूर्ण केलेले आहे." + FILLER
    letterhead = "M/s Example & Co.\nCHARTERED ACCOUNTANTS\nTURNOVER CERTIFICATE" + FILLER
    parsed = _parsed(marathi, letterhead)
    docs = document_fetch.inventory([("d1", "bid.pdf", parsed, classify_pages(parsed))])
    assert [d.firm for d in docs] == ["उदाहरण कन्स्ट्रक्शन", "Example & Co"]


def test_a_turnover_statement_that_is_only_a_grid_is_recognised():
    from app.services.bidder_documents import page_type

    grid = "Example & Co., Chartered Accountants\nFinancial Year | Turnover (Rs)\n2023-2024 | 1,23,45,000/-\n2024-2025 | 1,56,70,500/-"
    assert page_type(grid + FILLER) == "turnover_certificate"
