"""Hybrid pipeline units: readability, bundles, OCR adapter/conversion, fact reconciliation, PMC rules."""

import io
import json
import zipfile

import httpx
import pytest

from app.adapters.datalab_ocr import DatalabOcr, OcrJob, _to_range, split_pages
from app.adapters.ocr_text import ocr_page, page_sentences
from app.adapters.readability import classify_page
from app.services.bundle import classify_document, expand
from app.services.pmc_facts import FactCollector
from app.services.values import words_to_amount


# --- values -----------------------------------------------------------------------------

def test_indian_amount_in_words():
    assert words_to_amount("Five Lakhs Twelve Thousand Eight Hundred Thirty Rupees") == 512830
    assert words_to_amount("Two Crore Ten Lakhs Five Thousand Three Hundred Seven Rupees and "
                           "Fifty Paise") == pytest.approx(21005307.50)
    assert words_to_amount("Rupees Five Crore only") == 50_000_000
    assert words_to_amount("no numbers here") is None


# --- readability --------------------------------------------------------------------------

def test_page_readability_classes():
    english = "The bidder shall submit the tender with the earnest money deposit and all documents. " * 3
    assert classify_page(english).kind == "english"
    assert classify_page("").kind == "image"
    # a bad OCR layer on a scan, and Marathi set in a legacy ASCII-mapped font (invented strings of
    # the same character mix)
    assert classify_page("vbtq .{yjkqe {' ;u1 88c8ogz dlW-pW 8{2-gu gtgo( ywwwpw f opwwpwy wdbwd+ zgy'd " * 3).kind == "garbled"
    assert classify_page("P|Ta J« _n¨J . hX² SpJ pYnbnNp Ynd e oËWn J$n_nNp Ynd P|Ta ~n~uNs ozd©K[VW bŠJ _ " * 3).kind == "garbled"
    assert classify_page("निविदाधारकाने मागील तीन वर्षांतील कामांची यादी व प्रमाणपत्रे सादर करावीत " * 3).kind == "marathi"


# --- bundles ----------------------------------------------------------------------------

def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_bundle_expands_nested_archives_and_skips_junk(tmp_path):
    inner = _zip({"DATASHEET.pdf": b"%PDF-1.4 datasheet", "notes.txt": b"x", "copy_of_205.pdf": b"%PDF-1.4 notice"})
    outer = _zip({"work/205.pdf": b"%PDF-1.4 notice", "work/BOQ_1.xls": b"\xd0\xcf\x11\xe0",
                  "work/Tenderdocuments.zip": inner, "__MACOSX/work/._205.pdf": b"junk",
                  "../../evil.pdf": b"%PDF-1.4 evil", "work/noext": b"%PDF-1.4 no extension"})
    up = tmp_path / "work_1.zip"
    up.write_bytes(outer)
    files, warnings = expand([("work_1.zip", up)], tmp_path / "x")
    names = sorted(f.name for f in files)
    assert names == ["205.pdf", "BOQ_1.xls", "DATASHEET.pdf", "noext.pdf"]  # type sniffed from content
    assert any(".txt" in w for w in warnings)
    assert any("copy_of_205.pdf" in w and "identical" in w for w in warnings)  # duplicates read once
    assert any("escapes the archive" in w for w in warnings)
    assert all((tmp_path / "x").resolve() in f.path.resolve().parents for f in files)  # zip-slip blocked
    assert next(f for f in files if f.name == "DATASHEET.pdf").origin == "work_1.zip › work/Tenderdocuments.zip › DATASHEET.pdf"


def test_document_roles():
    assert classify_document("155.pdf", "Tender Abstract Work Name") == "notice"  # ward notices open with the abstract
    assert classify_document("TERMSCONDHION.pdf", "Tender No. : PMC/DRAINAGE/2026/12 FINAL Tender Conditions") == "conditions"
    assert classify_document("x.pdf", "REQUEST FOR PROPOSAL for Selection of Agency") == "rfp"
    assert classify_document("ALL_SOP.pdf", "Chapter: 1 -SOP for wiring work") == "specifications"
    assert classify_document("BOQ_1.xls", "") == "boq"


# --- OCR adapter ------------------------------------------------------------------------------

def test_page_range_and_pagination():
    assert _to_range([4, 5, 6, 9]) == "3-5,8" and _to_range([1]) == "0"
    md = "\n\n{3}------------------------------------------------\n\nA\n\n{4}------------------------------------------------\n\nB"
    assert split_pages(md) == {4: "A", 5: "B"}


def test_datalab_adapter_submits_polls_and_caches(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    calls = {"post": 0, "get": 0}

    def handler(request: httpx.Request):
        assert request.headers["X-API-Key"] == "k"
        if request.method == "POST":
            calls["post"] += 1
            if calls["post"] == 1:
                return httpx.Response(429, json={})  # rate limited once
            assert b'name="page_range"' in request.content and b"3-4" in request.content
            return httpx.Response(200, json={"success": True, "request_id": "r", "request_check_url": "https://x/check"})
        calls["get"] += 1
        if calls["get"] == 1:
            return httpx.Response(200, json={"status": "processing"})
        return httpx.Response(200, json={"status": "complete", "success": True,
                                         "markdown": "{3}----------\n\nपान चार\n\n{4}----------\n\nपान पाच"})

    ocr = DatalabOcr("k", tmp_path / "cache", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, requests_per_minute=10_000)
    results, errors = ocr.run([OcrJob(pdf, "hash", [4, 5], "balanced")])
    assert not errors and [p.markdown for p in results[0][1]] == ["पान चार", "पान पाच"]
    hit = ocr.cached("hash", 5, ("balanced", "accurate"))
    assert hit and hit.cached and hit.markdown == "पान पाच"
    assert ocr.cached("hash", 5, ("accurate",)) is None  # a cheaper mode never satisfies a request for a better one


def test_chandra_markdown_keeps_handwriting_and_drops_struck_text():
    md = ("# Parameters for Qualification Criteria\n\n| Sr. No. | Criteria | Parameters |\n|---|---|---|\n"
          "| 1 | Maximum Turnover of contractor in last five years | Rs. <u>48.0</u> Lacs |\n"
          "| 2 | similar works (<del>water supply line</del>) <i>Repair of drainage chambers</i> | "
          "a) Rs. <u>12</u> lacs<br>b) Rs. <u>15</u> lacs |\n\n"
          "Bid Capacity = $\\frac{\\text{A} \\times N}{1}$ **Rs. 30.0 lacs**")
    page = ocr_page(3, md, "balanced")
    assert page.quality.kind == "ocr_en"
    assert page.struck == ["water supply line"] and page.handwritten == ["Repair of drainage chambers"]
    row = page.tables[0][2]
    assert "water supply" not in " ".join(row) and "Repair of drainage chambers" in row[1]
    assert "Rs. 48.0 Lacs" in page.tables[0][1][2]
    assert "(A × N) / (1)" in page.text and "**" not in page.text
    sentences, _ = page_sentences(page)
    assert any(s.in_table and "48.0" in s.text for s in sentences)


# --- facts ----------------------------------------------------------------------------------

def test_security_deposit_4_plus_1_is_not_a_conflict():
    fc = FactCollector()
    fc.add("emd", 5000, "d", "123.pdf", 1, "EMD", "Tender Notice")
    fc.add("security_deposit", 20000, "d", "123.pdf", 1, "SD 4%", "Tender Notice")
    fc.add("security_deposit", 25000, "d", "123.pdf", 5, "SD 5%", "B-1 form")
    facts = fc.reconcile()
    assert facts["security_deposit"].status == "consistent" and facts["security_deposit"].value == 25000
    assert "EMD converted" in facts["security_deposit"].note


def test_typed_sources_outrank_ocr_and_truncated_names_agree():
    fc = FactCollector()
    fc.add("total_estimated_cost", 4567800, "a", "TERMS.pdf", 5, "row", "Conditions tender table")
    fc.add("total_estimated_cost", 4567800, "a", "TERMS.pdf", 7, "row", "form table")
    fc.ocr = True
    fc.add("total_estimated_cost", 4561800, "b", "DATASHEET.pdf", 1, "handwritten", "form table")
    fc.ocr = False
    fc.add("work_name", "WARD 9 DRAINAGE LINE", "c", "123.pdf", 4, "line", "Work Input Form")
    fc.add("work_name", "WARD 9 DRAINAGE LINE SAFAI KARNE.", "c", "123.pdf", 4, "table", "form table")
    facts = fc.reconcile()
    assert facts["total_estimated_cost"].value == 4567800 and "OCR read ₹45,61,800" in facts["total_estimated_cost"].note
    assert facts["work_name"].status == "consistent" and facts["work_name"].value.endswith("KARNE.")


def test_portal_form_tables_map_labels_to_values():
    fc = FactCollector()
    fc.scan_tables([[["Type of Contract:", "Completion Month:", "Rainy Season:", "Months:"], ["B1", "12", "Excluded", "12"]],
                    [["3", "Form of Contract:", "PERCENTAGE"], ["", "EMD Fee:", "120000"], ["", "Security Deposit (5%)", "5%"]]],
                   "d", "notice.pdf", 3)
    facts = fc.reconcile()
    assert facts["completion_months"].value == 12 and facts["rainy_season"].value == "Excluded"
    assert facts["form_of_contract"].value == "Percentage" and facts["emd"].value == 120000
    assert facts["security_deposit"].status == "missing"  # "5%" is a rate, never an amount


def test_image_scans_become_pdfs_and_word_files_are_explained(tmp_path):
    from PIL import Image

    img = tmp_path / "goshwara.jpg"
    Image.new("RGB", (200, 280), "white").save(img)
    doc = tmp_path / "terms.docx"
    doc.write_bytes(b"PK\x03\x04 fake docx")
    files, warnings = expand([("goshwara.jpg", img), ("terms.docx", doc)], tmp_path / "x")
    assert [f.name for f in files] == ["goshwara.pdf"] and files[0].path.read_bytes().startswith(b"%PDF")
    assert any("export it to PDF" in w for w in warnings)


def test_zip_bomb_and_password_members_are_refused(tmp_path):
    import zipfile as zf

    bomb = tmp_path / "bomb.zip"
    with zf.ZipFile(bomb, "w", compression=zf.ZIP_DEFLATED) as z:
        z.writestr("big.pdf", b"%PDF" + b"0" * (60 << 20))
    files, warnings = expand([("bomb.zip", bomb)], tmp_path / "x")
    assert not files and any("compression" in w for w in warnings)
