"""Rules read from PMC's tender-procedure circular, a scanned Marathi document read by OCR: what it
states as a rule, what it only mentions, and what it leaves for the tender notice. Synthetic wording
throughout; no real tender's figures."""

from app.adapters.pdf_parser import ParsedDocument, ParsedPage, Sentence
from app.adapters.readability import PageQuality
from app.services import indexation, pmc_eligibility
from app.services.bundle_extraction import _tender_fy
from app.services.pmc_eligibility import DocView
from app.services.pmc_facts import FactCollector

PMC = "पुणे महानगरपालिका"


def _view(*pages: tuple[str, list[list[str]]]) -> DocView:
    """OCR'd Marathi pages: each line a sentence, each table row a quoted row."""
    parsed, sentences = [], []
    for n, (text, rows) in enumerate(pages, 1):
        parsed.append(ParsedPage(number=n, text=text, tables=[[list(r) for r in rows]] if rows else [],
                                 quality=PageQuality("ocr_mr"), raw_text=text))
        sentences += [Sentence(page=n, text=line.strip(), section="") for line in text.splitlines() if line.strip()]
        sentences += [Sentence(page=n, text=" ".join(r), section="", in_table=True, row=tuple(r)) for r in rows]
    return DocView("c", "circular.pdf", "conditions", ParsedDocument(pages=parsed, sentences=sentences))


def _criteria(doc: DocView, cost: float = 750_000) -> dict:
    facts = FactCollector().reconcile()
    facts["estimated_cost"].value, facts["estimated_cost"].status = cost, "single_source"
    facts["form_of_contract"].value, facts["form_of_contract"].status = "Percentage", "single_source"  # as the BOQ states
    return {c.key: c for c in pmc_eligibility.build([doc], facts, True).criteria}


def test_a_joint_venture_mentioned_is_not_a_joint_venture_banned():
    # A list of points to clarify names a JV's agreement because JVs may bid.
    mention = _view((f"{PMC}\n३) Joint Venture मार्फत निविदा आल्यास भागीदारी करार तपासून घ्यावा.", []))
    assert "no_joint_venture" not in _criteria(mention)
    banned = _view((f"{PMC}\n९. या कामासाठी Joint Venture अनुज्ञेय राहणार नाही.", []))
    assert _criteria(banned)["no_joint_venture"].source.page == 1


def test_bid_capacity_is_located_by_its_formula_whatever_the_ocr_made_of_its_name():
    row = ["४", "निविदा भरण्याची कमना (बिड कोमिटी)", "(अ x एन x १.५) - बी, येथे अ ही कमाल वार्षिक उलाढाल"]
    c = _criteria(_view((f"{PMC}\nपात्रता निकष", [row])))["pmc_bid_capacity"]
    assert c.status == "located" and c.source.page == 1


def test_emd_is_worked_out_from_the_rule_only_when_the_rule_is_stated():
    rule = (f"{PMC}\n४.१ इसारा रक्कम (EMD)\nअ) १.५० कोटीपर्यंतच्या कामांसाठी अंदाजपत्रकीय रकमेच्या १%\n"
            "ब) त्यापेक्षा मोठ्या कामांसाठी ०.५% किंवा १.५० लक्ष, जे जास्त असेल ते")
    emd = _criteria(_view((rule, [])), cost=750_000)["pmc_emd"]
    assert emd.threshold_value == 7500 and any("rule, not a figure" in n for n in emd.notes)
    bare = _criteria(_view((f"{PMC}\n४.१ इसारा रक्कम (EMD) ऑनलाईन भरावी.", [])))["pmc_emd"]
    assert bare.threshold_value is None


def test_a_turnover_threshold_without_a_completion_period_states_no_number():
    rows = [["१", "निविदाधारकाची गेल्या पाच वर्षांतील उलाढाल", "कामाच्या वार्षिक मूल्याच्या किमान ७५ टक्के असावी."]]
    turnover = _criteria(_view((f"{PMC}\n२.९ पात्रता निकष", rows)))["pmc_max_turnover"]
    assert turnover.threshold_value is None and any("completion period is not stated" in n for n in turnover.notes)


def test_an_escalation_rate_stated_in_prose_builds_its_own_factors():
    doc = _view((f"{PMC}\nटीप: मागील वर्षांचे आकडे चालू दरात आणताना प्रतिवर्षी १०% वाढ धरावी.", []))
    table = indexation.table_from_stated_rule([doc], current_fy=2026)
    assert table.factors[2025] == 1.1 and table.factors[2023] == 1.331 and table.page == 1
    assert table.factor_for(2026) == 1.0  # the current year is not escalated
    assert indexation.table_from_stated_rule([doc], current_fy=None) is None  # no current level, nothing applied


def test_the_tenders_financial_year():
    assert _tender_fy("PMC/ZONE-3/2026-27/WARD/4", []) == 2026
    footer = _view(("Printed on 14/08/2026 — page 1 of 4", []))
    assert _tender_fy("PMC/ZONE-1/2026/WARD/9", [footer]) == 2026
    february = _view(("Printed on 15/02/2026 — page 1 of 3", []))
    assert _tender_fy("PMC/ZONE-1/2026/WARD/9", [february]) == 2025
    assert _tender_fy(None, []) is None
