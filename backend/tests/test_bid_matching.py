"""Bid-side edge cases, each reduced from a layout seen in real MahaTender bid envelopes.

All inputs here are synthetic — invented firms, figures and places in the shapes the documents
use. No bidder's data is kept in the repository (the corpus check lives in test_bid_corpus.py and
reads the envelopes from the user's disk).
"""

from datetime import date

import pytest

from app.adapters.pdf_parser import ParsedDocument, ParsedPage
from app.adapters.readability import PageQuality
from app.services.bidder_documents import page_type
from app.services.bidder_evidence import read_bid_capacity
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services.evaluation import evaluate_criterion, overall_status
from app.services.key_quantities import _horizontal_rows, executed_rows, matches
from app.services.nature import judge
from app.services.work_proof import _amounts, _from_table, find_completed_works, in_progress, page_context
from app.services.year_tables import cell_year, read_year_series


def _page(n, text, tables=(), kind="ocr_mr"):
    return ParsedPage(number=n, text=text, tables=[list(map(list, t)) for t in tables], quality=PageQuality(kind), raw_text=text)


def _doc(*pages, types=None):
    d = ParsedDocument(pages=list(pages), sentences=[])
    d.page_types = types or [page_type(p.text) for p in pages]
    return d


def _claim(key, value, page=1, **detail):
    return Claim(criteria_key=key, value=str(value), page=page, quote="q", section="s", value_numeric=value, detail=detail)


# --- what each page of a merged scan is -----------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("STATE PUBLIC WORKS DEPARTMENT — CONTRACTOR'S REGISTRATION — valid for works in Class III", "registration"),
    ("Registration of Unemployed Engineers — certificate — Class II", "registration"),
    ("जिल्हा परिषद बांधकाम विभाग — ठेकेदार नोंदणी प्रमाणपत्र — वर्ग-३", "registration"),
    ("दुकाने व आस्थापना नोंदणी — आस्थापनेचे नाव व पत्ता", "constitution"),
    ("TURNOVER CERTIFICATE — we, the firm's chartered accountants, report its turnover below", "turnover_certificate"),
    ("कार्यकारी अभियंता कार्यालय जावक क्र. दिनांक कामाचा दाखला मे. उदाहरण कन्स्ट्रक्शन यांनी खालील काम पूर्ण केलेले आहेत", "completion_certificate"),
    ("Bank Response On Payment Details Tender Ref No. : PMC/X/2026/1 EMD Fee in ₹ : 10,000", "emd_receipt"),
])
def test_pages_are_typed_by_what_they_are(text, kind):
    assert page_type(text) == kind


def test_a_passing_mention_does_not_type_a_page():
    affidavit = ("हमीपत्र ... मी निविदेतील सर्व अटी मान्य करतो व कामाच्या अनुभवाची कागदपत्रे, टर्नओव्हर सर्टिफिकेट, "
                 "टर्नओव्हर, बीड कॅपॅसिटी, बीओक्यू व सॉलवन्सी प्रमाणपत्रे जोडली आहेत. " * 3)
    assert page_type(affidavit) != "bid_capacity"


# --- Marathi work certificates ------------------------------------------------------------

CERT = ("कार्यकारी अभियंता कार्यालय\nदिनांक: १२/०५/२०२५\nकामाचा दाखला\nमे. उदाहरण कन्स्ट्रक्शन यांनी आर्थिक वर्ष सन २०२४-२५ मध्ये "
        "खालील कामे पूर्ण केलेली आहेत.")
CERT_TABLE = [
    ["अ. क्र.", "टेंडर क्र.", "कामाचे नाव", "पुगप तरतूद (लक्ष)", "ठेकेदाराची रक्कम", "वर्क आर्डर जा.क्र.व दि.", "शेरा"],
    ["1", "४१ - २०२४", "प्रभाग क्र. १७ मधील अंतर्गत रस्त्यांचे डांबरीकरण करणे.", "४० लक्ष", "३३,४४,५५०/-", "१४ दि ०३.०२.२५", "काम पूर्ण"],
    ["2", "४२ - २०२४", "प्रभाग क्र. ९ मध्ये ड्रेनेज लाईन टाकणे.", "२५ लक्ष", "१८,५०,०००/-", "१५ दि ०४.०२.२५", "काम चालू (७०% काम पूर्ण)"],
]


def test_a_marathi_certificate_is_read_with_the_accepted_amount_and_the_preamble_year():
    got = _from_table(CERT_TABLE, page=5, ctx=page_context(CERT))
    assert len(got) == 1, "the running work (काम चालू) is not a completed work"
    r = got[0]
    assert r.value == 3344550 and r.value_label == "ठेकेदाराची रक्कम"  # never the budget provision
    assert r.year == 2024 and (r.year_min, r.year_max) == (2024, 2025)
    assert r.contractor and "उदाहरण" in r.contractor


def test_status_words_decide_completion():
    assert in_progress("काम चालू (७५% काम पूर्ण)") and in_progress("१ ले रनिंग बिल") and in_progress("काम सुरु, २ रे अंतिम देय बाकी")
    assert in_progress("70% completed")
    assert not in_progress("काम पूर्ण") and not in_progress("100 % completed") and not in_progress("काम पूर्ण ३ रे अंतिम बिल दिले")


def test_a_key_value_certificate_for_a_running_bill_is_not_completed_work():
    running = _page(7, "मुख्य अभियंता (पथ) कार्यालय\nदि. ०१/०२/२०२६\nकामाचा दाखला\n", tables=[[
        ["१", "कामाचे नाव", "शहरातील रस्त्यांवर खोदाईचे रिईनस्टेटमेंट करणे."], ["२", "टेंडर क्र.", "पीएमसी/रोड/२०२५/१"],
        ["३", "बिल", "१ ले रनिंग बिल"], ["४", "ठेकेदारास एकूण अदा र.रु.", "१८,४२,६०५.००"], ["५", "काम पूर्ण/काम चालू", "काम चालू"]]])
    done = _page(9, "मुख्य अभियंता (पथ) कार्यालय\nदि. ०१/०८/२०२५\nकाम पूर्णत्वाचा दाखला\nवरील काम समाधानकारक पूर्ण केलेले आहे.", tables=[[
        ["१", "कामाचे नाव", "चौक ते चौक रस्ता डांबरीकरण करणे."], ["२", "टेंडर क्र.", "पीएमसी/रोड/२०२३/९"],
        ["३", "निविदा र.रु.", "र.रु. ४८,१५,२६०=००"], ["४", "काम पूर्ण करण्याची दिनांक", "दि. २२.०४.२०२५"],
        ["५", "काम पूर्ण रक्कम रुपये", "र.रु. ४७,९२,३१५.००"], ["६", "काम पूर्ण", "काम पूर्ण, ३ रे अंतिम बिल दिले"]]])
    got = find_completed_works(_doc(running, done))
    assert [(r.page, r.value) for r in got] == [(9, 4792315.0)]


def test_a_key_value_certificate_is_quoted_by_the_rows_that_decided_it():
    rows = [["१", "कामाचे नाव", "चौक ते चौक रस्ता डांबरीकरण करणे. " + "पदपथ दुरुस्ती " * 20], ["२", "टेंडर क्र.", "पीएमसी/रोड/२०२३/९"],
            ["३", "निविदा र.रु.", "र.रु. ४८,१५,२६०=००"], ["४", "काम पूर्ण करण्याची दिनांक", "दि. २२.०४.२०२५"],
            ["५", "काम पूर्ण रक्कम रुपये", "र.रु. ४७,९२,३१५.००"], ["६", "काम पूर्ण", "काम पूर्ण, ३ रे अंतिम बिल दिले"]]
    # The same rows read again from the text layer, and a label over the quantity table that states no amount.
    text = ("काम पूर्णत्वाचा दाखला\nवरील काम समाधानकारक पूर्ण केलेले आहे.\n" + "\n".join(f"{n} {k} : {v}" for n, k, v in rows)
            + "\nकाम पूर्ण रक्कम : खालीलप्रमाणे")
    [record] = find_completed_works(_doc(_page(9, text, tables=[rows])))
    parts = record.quote.split(" … ")
    assert record.value == 4792315.0
    assert record.quote.count("४७,९२,३१५") == 1 and record.quote.count("पीएमसी/रोड/२०२३/९") == 1
    assert "खालीलप्रमाणे" not in record.quote
    assert parts[0].startswith("१ | कामाचे नाव |") and parts[0].endswith("…") and len(parts[0]) <= 161
    assert parts[-1] == "६ | काम पूर्ण | काम पूर्ण, ३ रे अंतिम बिल दिले"  # page order, in the certificate's own words
    # The form's status line says which applies: here the work is still running.
    running = rows[:5] + [["६", "काम पूर्ण", "काम चालू"]]
    assert find_completed_works(_doc(_page(9, "काम पूर्णत्वाचा दाखला\nवरील काम समाधानकारक पूर्ण केलेले आहे.", tables=[running]))) == []


def test_certificates_in_another_contractors_name_are_flagged():
    def cert(n, firm, work):
        text = f"कार्यकारी अभियंता\nदि २५/०१/२०२३\nप्रमाणित करण्यात येते की मे {firm} यांनी या कार्यालयाचे खालील काम पूर्ण केलेले आहे"
        table = [["अ क्र", "कामाचे वर्णन", "टेंडर र.", "टेंडर मान्यता र", "शेरा"], ["1", work, "१०-२०२२", "४०,००,०००/-", "काम पूर्ण"]]
        return _page(n, text, tables=[table])
    doc = _doc(cert(1, "सुयोग इंटरप्रायजेस", "रस्ता काँक्रीटीकरण करणे."), cert(2, "सुयोग एंटरप्रायझेस", "ड्रेनेज लाईन टाकणे."),
               cert(3, "रमाकांत गोविंद पाटील", "नाला साफसफाई करणे."))
    got = {r.page: r for r in find_completed_works(doc)}
    assert not got[1].foreign and not got[2].foreign and got[3].foreign


def test_money_as_marathi_certificates_write_it():
    assert _amounts("३,२१,४५०=७५") == [321450.75]
    assert _amounts("३१,६२,५७.४०/-") == [], "a digit lost by OCR must not become ₹2.05 lakh"


# --- the verdicts ------------------------------------------------------------------------

SPEC_CTX = {"ref_no": "PMC/ZONE-9/2026-27/EXAMPLE/4", "estimated_cost": 6_000_000.0, "completion_years": 1.0, "bid_date": "2026-05-02"}


def _spec(key, operator, threshold=None, **params):
    return CriterionSpec(key=key, label=key, operator=operator, params=params, threshold_value=threshold,
                         threshold_unit="INR", tender=SPEC_CTX)


def test_emd_rounding_the_tender_reference_and_a_shortfall():
    spec = _spec("pmc_emd", ">=", 60000.0)
    ok = evaluate_criterion(spec, [_claim("pmc_emd", 59999.0, for_this_tender=True, payment_status="Success")], "", date(2026, 5, 4))
    assert ok.verdict == "pass"
    other = evaluate_criterion(spec, [_claim("pmc_emd", 60000.0, for_this_tender=False, tender_ref="PMC/OTHER/1")], "", date(2026, 5, 4))
    assert other.verdict == "unknown"
    short = evaluate_criterion(spec, [_claim("pmc_emd", 6000.0, for_this_tender=True, payment_status="Success")], "", date(2026, 5, 4))
    assert short.verdict == "not_matched"


def test_registration_is_judged_on_the_bid_date_and_any_valid_one_counts():
    spec = _spec("pmc_registration", "exists")
    small = _claim("pmc_registration", 5_000_000.0, valid_to="2027-01-01", capacity=5_000_000.0)
    big = _claim("pmc_registration", 15_000_000.0, valid_to="2028-01-01", capacity=15_000_000.0)
    expired = _claim("pmc_registration", 15_000_000.0, valid_to="2026-04-30", capacity=15_000_000.0)
    assert evaluate_criterion(spec, [small, big], "", date(2026, 9, 1)).verdict == "pass"
    assert evaluate_criterion(spec, [small], "", date(2026, 9, 1)).verdict == "unknown"  # covers ₹50 L < ₹60 L
    assert evaluate_criterion(spec, [expired], "", date(2026, 9, 1)).verdict == "unknown"
    lapsed_after_bid = _claim("pmc_registration", None, valid_to="2026-07-29")
    assert evaluate_criterion(spec, [lapsed_after_bid], "", date(2026, 9, 30)).verdict == "pass"  # valid when the bid was made


def test_bid_capacity_is_recomputed_on_the_tenders_terms():
    working = ("BID CAPACITY WORKING\n(Rs. in Lakhs)\nValue of A (works executed) : 210.00\nValue of B (commitments) : 20\n"
               "Bid Capacity : ( A * N * 1.5 ) - B\nValue of N : 6 months\n: 137.50\nUDIN: 0000")
    got = read_bid_capacity(working)
    assert got["A"] == 21_000_000 and got["B"] == 2_000_000 and got["N"] == 0.5
    spec = _spec("pmc_bid_capacity", ">=", 6_000_000.0)
    claim = _claim("pmc_bid_capacity", got["stated"], recomputed=21_000_000 * 1.0 * 1.5 - 2_000_000, A=21_000_000, B=2_000_000,
                   defects=["uses a period N of 0.5 year(s); this tender's is 1"])
    v = evaluate_criterion(spec, [claim], "", date(2026, 5, 4))
    assert v.verdict == "pass" and "Recomputed" in v.reason


def test_a_working_with_two_values_for_a_is_left_to_the_committee():
    muddled = "A=30.00 Lacs for F.Y. 2025-26= (30.00*1.5=45.00) updated\nN=0.50 years\nB=10 Lacs\n= 12.50 lakhs\nUDIN: 0000"
    assert read_bid_capacity(muddled)["ambiguous_A"]


def test_non_gating_conditions_do_not_hold_a_bidder_pending():
    assert overall_status([("pass", True), ("unknown", False)]) == "eligible"
    assert overall_status([("pass", True), ("unknown", True)]) == "pending"
    assert overall_status([("fail", True), ("unknown", True)]) == "disqualified"


# --- key quantities -----------------------------------------------------------------------

@pytest.mark.parametrize("required,row,expected", [
    ("60 mm Shot Blasted Block", "60 mm shot blasted", True),
    ("60 mm Shot Blasted Block", "Providing and laying 80 mm paver blocks", False),
    ("60 mm Shot Blasted Block", "६० मि.मी. जाडीचा ब्लॉक", False),
    ("M-15 Concrete", "एम १५ कॉंक्रीट", True),
    ("M-15 Concrete", "M-30 concrete", False),
    ("इंटरलॉकींग ब्लॉक काढून बसविणे", "इंटरलॉकिंग ब्लॉक काढून परत बसविणे.", True),
    ("इंटरलॉकींग ब्लॉक काढून बसविणे", "Providing and laying 100 mm paver blocks", False),
])
def test_items_are_matched_by_their_distinguishing_features(required, row, expected):
    assert matches(required, row) is expected


def test_executed_quantities_not_the_tenders():
    abstract = _page(1, "B O Q CERTIFICATE\nकामाचे नाव : रस्ता दुरुस्ती\nटेंडर क्र. : ५ - २०२४", kind="ocr_en", tables=[[
        ["BOQ Item No.", "Item Name", "Unit", "Tender QTY.", "Upto Date Qty"],
        ["10", "Relaying of paver blocks", "Sqm", "750.00", "412.50"]]])
    rows = executed_rows(_doc(abstract, types=["boq_certificate"]))
    assert [r.qty for r in rows] == [412.5]


def test_quantities_printed_across_a_row():
    cert = _page(1, "B O Q CERTIFICATE\nदाखला देण्यात येतो कि ... काम समाधान पूर्वक पूर्ण", tables=[[
        ["अ क्र", "कामाचे वर्णन", "टेंडर र.", "टेंडर मान्यता र", "शेरा"],
        ["1", "मलवाहिन्यांची कामे करणे.", "८८-२०२२", "५८,१७,९०३/-", "काम पूर्ण"],
        ["माती खोदाई", "आर सी.सी पाईप पुरवणे", "M-30", "GSB"],
        ["१६००.०० घ.मी", "NP-3", "१३९.४० घ.मी", "४२८ घ.मी"]]])
    rows = _horizontal_rows(_doc(cert), ["completion_certificate"])
    m30 = next(r for r in rows if r.item == "M-30")
    assert m30.qty == 139.4 and m30.unit == "cum" and m30.tender_year == 2022
    assert not any(r.item in ("अ क्र", "टेंडर मान्यता र") for r in rows), "a table's heading over its data is not quantities"


def test_key_quantities_count_one_work_or_a_year_never_a_duplicate():
    spec = _spec("pmc_key_quantities", "quantities", items=[{"item": "Interlocking blocks refixed", "qty": 600, "unit": "sqm"}],
                 window_years=5)
    rows = [_claim("pmc_key_quantities", 400.0, 1, item="Interlocking blocks refixed", year=2023),
            _claim("pmc_key_quantities", 250.0, 2, item="Interlocking blocks refixed", year=2023)]
    assert evaluate_criterion(spec, rows, "", date(2026, 5, 4)).verdict == "pass"
    older = [_claim("pmc_key_quantities", 900.0, 1, item="Interlocking blocks refixed", year=2019)]
    assert evaluate_criterion(spec, older, "", date(2026, 5, 4)).verdict == "unknown"


# --- nature of work, turnover grids --------------------------------------------------------

def test_nature_is_what_was_built_not_where():
    title = "PRABHAG KR. 5 MADHYE RSTYANCHI DURUSTI V PANIPURVATHA / MALNISSARAN VAHINI KHODAICHE REINSTETMENT KARNE"
    assert judge("road works", title, "शहरातील विविध रस्त्यांवर खोदाईचे रिईनस्टेटमेंट करणे.")[0] == "similar"
    assert judge("road works", title, "गायमुख येथे रस्त्यालगत नाल्यास संरक्षक भिंत बांधणे.")[0] == "not_similar"
    assert judge("road works", title, "प्रभाग क्र. ७ मध्ये ड्रेनेज लाईन टाकणे.")[0] == "uncertain"  # a family the title names
    assert judge("road works", title, "Repair and renovation of the district court building")[0] == "not_similar"


def test_turnover_grids_in_every_shape():
    assert cell_year("F. Y. 2024-25 A. Y. 2025-26") == 2024 and cell_year("• Financial Year 2021-22") == 2021
    lakhs = read_year_series([[["Year", "Turnover (Fig in Lakhs)"], ["2022-23", "503.54"]]])
    assert [(y.year, y.value) for y in lakhs] == [(2022, 50_354_000.0)]
    split = read_year_series([[["Financial Year", "Assessment Year", "Govt", "Private", "Total amount of Contract Receipts"],
                               ["2024-2025", "2025-2026", "3,72,18,906", "0", "3,72,18,906"]]])
    assert [(y.year, y.value) for y in split] == [(2024, 37_218_906.0)]
