"""Edge cases found while scoring the MahaTender corpus against hand-verified ground truth
(tests/data/corpus_truth.json, scripts/score_corpus.py). Each test is a pattern seen in a real
tender, reduced to the smallest input that shows it."""

from pathlib import Path

import pytest

from app.adapters.pdf_parser import ParsedDocument, ParsedPage, PageScan, Sentence, UnreadablePDF, _paragraph_blocks, quick_scan
from app.adapters.readability import PageQuality
from app.services import checklist_extraction as ce
from app.services.bundle import classify_document
from app.services.criteria_extraction import rule_epf_esic, rule_gst, rule_key_personnel
from app.services.pmc_eligibility import DocView, Located, _listed_quantity_items
from app.services.pmc_facts import FactCollector


def _lines(*texts: str) -> list[dict]:
    """pdfplumber-like lines, 12 pt apart (no paragraph gap), regular weight."""
    return [{"text": t, "chars": [{"text": c, "fontname": "Times"} for c in t], "top": 12.0 * i, "bottom": 12.0 * i + 10}
            for i, t in enumerate(texts)]


def _doc(*pages: ParsedPage) -> ParsedDocument:
    return ParsedDocument(pages=list(pages), sentences=[])


# --- numbered lists (road volume 269, clause 15(h)) ------------------------------------------

def test_numbered_list_items_are_separate_paragraphs_carrying_their_intro():
    blocks, open_lead = _paragraph_blocks(_lines(
        "h) To qualify for award of this work, the tenderer shall have in",
        "its name and furnish the information detailed outbelow-",
        "1 Valid Registration Certificate under GST –2017.",
        "2 Valid PF code and ESICertificate",
        "3 Valid Registration certificate under Works Contract Act",
        "1989 or as per updated Govt. VAT rules &Regulations",
    ))
    texts = [t for _, t, _ in blocks]
    assert texts[1] == "1 Valid Registration Certificate under GST –2017."
    assert texts[2] == "2 Valid PF code and ESICertificate"
    assert texts[3].endswith("VAT rules &Regulations")  # a wrapped line ("1989 or …") is not item 1989
    assert all(lead and "shall have" in lead for _, _, lead in blocks[1:])
    assert open_lead and "furnish" in open_lead  # the list may continue on the next page


def test_list_items_inherit_the_obligation_of_their_intro():
    lead = "To qualify for award of this work, the tenderer shall have in its name and furnish the information below-"
    gst = rule_gst(Sentence(11, "1 Valid Registration Certificate under GST –2017.", "", lead=lead))
    assert gst and gst.key == "gst_registration"
    labour = rule_epf_esic(Sentence(11, "2 Valid PF code and ESICertificate", "", lead=lead))
    assert labour and labour.params["parts"] == ["EPF", "ESIC"]
    assert rule_gst(Sentence(11, "Valid Registration Certificate under GST", "")) is None  # no obligation anywhere
    staff = rule_key_personnel(Sentence(19, "a) One B.E. Civil / DCE of having experience min. 5Years", "", lead="21. STAFF :-"))
    assert staff and staff.params["min_years"] == 5


def test_run_on_claimed_sentence_does_not_swallow_checklist_items():
    run_on = ("To qualify … furnish the information detailed outbelow-1 Valid Registration Certificate under GST –2017. "
              "2 Valid PF code and ESICertificate 3 Valid Registration certificate under Works Contract Act")
    assert not ce._claimed("Valid PF code and ESICertificate", [run_on])
    assert ce._claimed("A certificate of one successfully completed single work of similar nature",
                       ["15 A certificate of one successfully completed single work of similar nature"])


# --- checklist noise -------------------------------------------------------------------------

def test_data_sheets_and_fill_in_formats_are_not_requirement_lists():
    data_sheet = [["1", "Project Name", "Selection of Agency for Enhancement of Property Tax Survey Application"],
                  ["2", "RFP reference No and Date", "PMC/IT/2021/19"], ["3", "Tender Fee", "Rs.1359/-"],
                  ["4", "Earnest Money Deposit", "INR 15,000/-"], ["5", "Submission Type", "Open Tender, https://mahatenders.gov.in"]]
    finance_form = [["Sr", "Particulars", "2018-19", "2019-20", "2020-21"], ["1", "Annual Turnover (in", "", "", ""],
                    ["2", "Net Worth", "", "", ""]]
    real = [["1", "The bidder should have positive net worth", "Audited balance sheets for the last three financial years"],
            ["2", "Copy of GST registration", "Certificate of GST registration issued by the department"],
            ["3", "List of works in hand", "Statement No I along with requisite certificates from the employer"]]
    assert ce._form_or_data_sheet(data_sheet) and ce._form_or_data_sheet(finance_form)
    assert not ce._form_or_data_sheet(real)


def test_table_of_contents_and_chapter_headings_are_not_list_items():
    pages = [ParsedPage(3, "", lines=["Contents", "4) Pre-Qualification Criteria ........ 8"]),
             ParsedPage(4, "", lines=["1) Glossary", "EMD Earnest Money Deposit"]),
             ParsedPage(5, "", lines=["2) Invitation for Proposal", "2.1 RFP Notice", "This RFP document is published"]),
             ParsedPage(6, "", lines=["Payment milestones are listed below.",
                                      "h. Provision of resources should be strictly as per the qualification criteria",
                                      "mentioned at section 6.4 of this RFP.", "1 Resource CVs of the team proposed"])]
    assert ce._from_lines(_doc(*pages), {3, 4, 5, 6}) == []


def test_boxed_clauses_and_page_split_rows():
    p1 = ParsedPage(1, "CHECK LIST", tables=[[
        ["1", "AWARD CRITERIA The Owner may award the contract to the successful bidder whose bid is responsive"],
        ["7", "Undertaking by contractor", "Following undertaking on Rs. 100/- stamp paper. 1) Contractor has to upload a "
                                           "undertaking stating that no relative works in PMC. 2) Contractor has to upload a "
                                           "undertaking stating that he will complete the work with"]]])
    p2 = ParsedPage(2, "", tables=[[["", "", "", "quality within the time period."],
                                    ["8", "DLP Period", "", "1) Footpath work-2 yrs"]]])
    items = ce._from_tables(_doc(p1, p2), {1, 2})
    assert not any("AWARD CRITERIA" in it.text for it in items)
    assert items[1].text.endswith("complete the work with quality within the time period.")


def test_summary_rows_and_truncated_repeats_dedupe():
    page = ParsedPage(4, "CHECK LIST", tables=[[
        ["5", "List of Technical Personnel available with the tenderer (Statement No IV)"],
        ["6", "No arbitration clause for this work. The contractor should give undertaking that he will not claim arbitration"],
        ["9", "Staff", "List of Technical Personnel available with the tenderer (Statement No IV)"],
        ["11", "No arbitration clause for this workThe"]]])
    labels = [label for label, *_ in ce.extract_checklist(_doc(page), [])]
    assert len(labels) == 2 and "Staff" not in labels


# --- corrigenda -----------------------------------------------------------------------------

def test_corrigendum_is_a_short_titled_document_not_a_volume_that_mentions_one():
    assert classify_document("x.pdf", "CORRIGENDUM No. 1\nTender No. PMC/ROAD/2026/269", page_count=2) == "corrigendum"
    assert classify_document("shuddhipatrak_235.pdf", "", page_count=1) == "corrigendum"
    toc = "Contents VOLUME-I Check List & Standard Proforma Corrigendum I Tender Notice & Detailed Tender Notice"
    assert classify_document("scancopies_269.pdf", toc, page_count=108) == "conditions"


def test_corrigendum_values_supersede_the_tender():
    fc = FactCollector()
    fc.add("estimated_cost", 4164895, "a", "TERMS.pdf", 5, "row", "Conditions tender table")
    fc.add("estimated_cost", 4164895, "b", "BOQ.xls", None, "total", "BOQ spreadsheet")
    fc.amending = True
    fc.scan_corrigendum("", [[["Sr", "Particulars", "Existing", "Revised"],
                              ["1", "Estimated Cost put to the tender", "Rs. 41,64,895/-", "Rs. 42,50,000/-"]]],
                        "c", "Corrigendum 1.pdf", 1)
    fc.scan_corrigendum("The completion period of the work is revised to 9 months.", [], "c", "Corrigendum 1.pdf", 1)
    facts = fc.reconcile()
    assert facts["estimated_cost"].status == "amended" and facts["estimated_cost"].value == 4250000
    assert "₹41,64,895" in facts["estimated_cost"].note
    assert facts["completion_months"].value == 9


# --- run-together prose (road volume text layers) -------------------------------------------------

def test_prose_facts_with_words_run_together():
    fc = FactCollector()
    raw = ("3. ESTIMATED COST PUTTOTENDER: Rs. 35612654/-\n4. EARNESTMONEYDEPOSIT : a) An earnest money depositofRs. "
           "178063/- shouldbe deposited\n7. TIME LIMIT FOR COMPLETION OFWORK The time limit for completion ofwork is "
           "12 calendarmonths including monsoon")
    fc.scan_page(raw, "d", "vol.pdf", 7)
    facts = fc.reconcile()
    assert facts["estimated_cost"].value == 35612654 and facts["emd"].value == 178063
    assert facts["completion_months"].value == 12


def test_blank_key_quantity_grid_names_its_items():
    page = ParsedPage(13, "", tables=[[["BC (cum)", "DBM (cum)", "GSB/ MMM (cum)"],
                                       ["Earthwork in Embankment (Cum)", "DLC (cum)", "Murum (Cum)"],
                                       ["Concrete Cum", "Pever Blocks Sqm", "Steel (MT)"]]])
    hit = Located(DocView("d", "vol.pdf", "conditions", _doc(*[ParsedPage(n, "") for n in range(1, 13)], page)), 13, "17 …")
    assert _listed_quantity_items(hit) == ["BC", "DBM", "GSB/WMM", "Earthwork in Embankment", "DLC", "Murum",
                                           "Concrete", "Paver Blocks", "Steel"]


# --- blank scans and OCR hallucination -----------------------------------------------------------

def test_blank_scans_are_recognised_locally_and_never_sent_to_ocr(tmp_path):
    from PIL import Image, ImageDraw

    blank = Image.new("L", (1240, 1754), 250)
    ImageDraw.Draw(blank).ellipse((1150, 500, 1175, 525), fill=40)  # a punch hole
    text = Image.new("L", (1240, 1754), 250)
    draw = ImageDraw.Draw(text)
    for y in range(150, 1600, 40):
        draw.text((120, y), "The tenderer shall furnish the documents detailed below " * 2, fill=20)
    pdf = tmp_path / "scan.pdf"
    blank.save(pdf, save_all=True, append_images=[text], resolution=150)
    kinds = [s.quality.kind for s in quick_scan(pdf)]
    assert kinds == ["blank", "image"]


def test_repetitive_ocr_output_is_rejected():
    from app.services.tender_processing import looks_hallucinated

    invented = "## पुस्तक\n\nपृष्ठ संख्या\n\nलेखक का नाम\n\n" + "पुस्तक का विवरण  \n" * 10
    assert looks_hallucinated(invented)
    table = "| Sr | Item | Qty |\n|---|---|---|\n" + "".join(f"| {i} | Excavation in soft murum | {i * 3} cum |\n" for i in range(12))
    assert not looks_hallucinated(table)


# --- unreadable input ---------------------------------------------------------------------------

def test_damaged_and_empty_pdfs_explain_themselves(tmp_path):
    truncated = tmp_path / "cut.pdf"
    truncated.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pa")
    with pytest.raises(UnreadablePDF, match="damaged|no pages"):
        quick_scan(truncated)


def test_unread_pages_are_reported_not_silently_missing(tmp_path):
    from app.services.tender_processing import ProcessedDoc, coverage_warnings

    scans = [PageScan(1, "The tenderer shall submit " * 10, PageQuality("english"), False),
             PageScan(2, "", PageQuality("image"), True), PageScan(3, "", PageQuality("image"), True),
             PageScan(4, "Page 4 of 4", PageQuality("image"), False)]  # a blank vector page is not "unread"
    d = ProcessedDoc("id", "DATASHEET.pdf", "DATASHEET.pdf", Path(tmp_path / "DATASHEET.pdf"), "datasheet", "h", scans,
                     ParsedDocument([ParsedPage(s.number, s.text, quality=s.quality, raw_text=s.text) for s in scans], []))
    warnings = coverage_warnings([d], ocr_available=False)
    assert warnings == ["2 page(s) of DATASHEET.pdf could not be read (p. 2–3) — OCR is not configured (set DATALAB_API_KEY). "
                        "Requirements on those pages are not extracted; check them manually."]
