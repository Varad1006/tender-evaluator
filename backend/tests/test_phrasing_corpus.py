"""How authorities word the same requirement — and what must never be read as one.

A tender that exposes a bug is a symptom. Patching the reader until that document parses produces
a reader that works on the documents we happen to own, so each shape learned from a bundle is
written here as a row instead: the wording, and the value it must yield.

The `MUST_REFUSE` half is the important one. A miss costs the committee one manual lookup. A
misread prints a wrong number under a page citation that looks authoritative, and on a gating
criterion it disqualifies a company that actually qualifies. Precision before recall, always.

Sources are named in comments as evidence for a rule, never as a condition of it — every pattern
here is about document structure (a count beside a noun beside a threshold; a geometric column of
factors; a deployment verb beside a site) and must hold for a tender nobody has seen yet.
"""

from __future__ import annotations

import pytest

from app.adapters.pdf_parser import Sentence, _unwrap_rows
from app.services.criteria_extraction import rule_key_personnel, rule_staff_strength
from app.services.experience_ladder import build_ladder, find_rungs, find_window_years, satisfied_by
from app.services.indexation import escalate, parse_index_table
from app.services.values import corrupt_amounts, parse_amounts
from app.services.work_proof import NOT_WORK_PROOF, _from_table, _role_of
from app.services.year_tables import (
    DERIVED_LABEL,
    TURNOVER_LABEL,
    WORK_DONE_LABEL,
    cell_amount,
    cell_year,
    read_year_series,
)
from app.services.pmc_eligibility import _names_a_trade, _nature_in_clause


def S(text: str, page: int = 1, in_table: bool = True) -> Sentence:
    return Sentence(page=page, text=text, section="", in_table=in_table)


# --------------------------------------------------------------------------------------------
# 1. The experience ladder: one rung at a time
# --------------------------------------------------------------------------------------------

# (wording, expected count, expected absolute value or None, expected percentage or None)
RUNG_SHAPES = [
    # PMC works, percentage of the cost put to tender (drainage bundle 2286446 p.109)
    ("a) Three completed works (sewer /storm water line/retaining wall) costing not less than "
     "amount equal to 40% of the total estimated cost put to tender.", 3, None, 0.40),
    ("b) Two completed works costing not less than amount equal to 50% of the estimated cost.", 2, None, 0.50),
    ("c) One completed works costing not less than amount equal to 80% of the estimated cost.", 1, None, 0.80),
    # IT RFP, plain amounts, value stated before the qualifier (PMC DBT RFP p.14-15)
    ("One project of minimum value INR 64 Lakhs having scope of Design, Development, Operation "
     "and Maintenance of Software application.", 1, 6_400_000, None),
    ("Two projects of minimum value INR 40 Lakhs each having scope of Design, Development.", 2, 4_000_000, None),
    ("Three projects of minimum value INR 32 Lakhs each having scope of Design, Development.", 3, 3_200_000, None),
    # IT RFP, "At-least N … with each … value of more than ₹X" (PCMC encroachment RFP p.24)
    ("At-least three different projects with each Project value of more than ₹ 17 Lakhs", 3, 1_700_000, None),
    ("At-least two different projects with each Project value of more than ₹ 21 Lakhs", 2, 2_100_000, None),
    ("At-least one project with Project value of more than ₹34 Lakhs", 1, 3_400_000, None),
    # Spellings and orderings not yet seen in a bundle but inside the same shape
    ("atleast 2 works of value not less than Rs. 25,00,000 each", 2, 2_500_000, None),
    ("Minimum of three assignments each costing more than Rs 12 lakh", 3, 1_200_000, None),
    ("2 (two) completed contracts worth at least INR 1.5 Crore each", 2, 15_000_000, None),
    ("One work of similar nature costing not less than 80 per cent of the estimated cost", 1, None, 0.80),
]


@pytest.mark.parametrize("text,count,value,pct", RUNG_SHAPES)
def test_rung_shapes_are_read(text, count, value, pct):
    rungs = find_rungs([S(text)])
    assert rungs, f"no rung found in: {text!r}"
    r = next((x for x in rungs if x.count == count), None)
    assert r is not None, f"count {count} not found in {[x.count for x in rungs]}"
    if value is not None:
        assert r.value == pytest.approx(value), f"{r.value} != {value}"
    if pct is not None:
        assert r.pct == pytest.approx(pct)


# A count beside a noun is not a requirement. Each of these would put a wrong threshold, or a
# threshold where the tender states none, under a page citation.
MUST_REFUSE_RUNGS = [
    # No threshold at all — a submission instruction, not a criterion.
    "List of Works of similar type and magnitude carried out by Contractor (Statement No-II).",
    "The bidder shall submit three copies of the completed works schedule.",
    # A number that is not money: a duration, a clause pointer, a page.
    "The Bidder should have relevant experience of executing one project over last 3 years.",
    "Details of the quoted projects as per format mentioned in Section 8.6 of this RFP.",
    "Two projects shall be listed in Appendix 3 along with 5 supporting documents.",
    # A volume requirement, not a ladder rung.
    "The bidder must have completed at least 15 projects of any value.",
    # Staffing, which counts people rather than works.
    "The Bidder must have an on-roll staff strength of a minimum of 30 IT Professionals.",
]


@pytest.mark.parametrize("text", MUST_REFUSE_RUNGS)
def test_rungs_that_must_be_refused(text):
    assert find_rungs([S(text)]) == [], f"wrongly read a rung from: {text!r}"


# --------------------------------------------------------------------------------------------
# 2. The ladder as a whole: assembly, the monotone check, the one-base invariant
# --------------------------------------------------------------------------------------------

def test_ladder_assembles_across_sentences_and_pages():
    """The PMC IT RFP prints the options in separate table cells either side of a page break."""
    rungs = find_rungs([
        S("One project of minimum value INR 64 Lakhs having", page=14),
        S("Two projects of minimum value INR 40 Lakhs each", page=15),
        S("Three projects of minimum value INR 32 Lakhs each", page=15),
    ])
    ladder = build_ladder(rungs)
    assert ladder is not None
    assert [o["count"] for o in ladder.options()] == [3, 2, 1]
    assert [o["min_value"] for o in ladder.options()] == [3_200_000, 4_000_000, 6_400_000]
    assert ladder.pages == [14, 15]


def test_only_the_strictest_rung_would_overstate_the_requirement():
    """Reading one rung alone — what a single-sentence rule does — rejects a qualifying bidder."""
    rungs = find_rungs([
        S("One project of minimum value INR 64 Lakhs having", page=14),
        S("Two projects of minimum value INR 40 Lakhs each", page=15),
        S("Three projects of minimum value INR 32 Lakhs each", page=15),
    ])
    options = build_ladder(rungs).options()
    three_mid_sized = [3_300_000, 3_300_000, 3_300_000]
    assert satisfied_by(options, three_mid_sized), "the 3-project option must qualify this bidder"
    assert satisfied_by([o for o in options if o["count"] == 1], three_mid_sized) is None


def test_every_option_implies_one_contract_value():
    """40 / 50 / 80% of the same number; the ratios recover a value the RFP never prints."""
    rungs = find_rungs([
        S("At-least three different projects with each Project value of more than Rs 17 Lakhs"),
        S("At-least two different projects with each Project value of more than Rs 21 Lakhs"),
        S("At-least one project with Project value of more than Rs 34 Lakhs"),
    ])
    ladder = build_ladder(rungs)
    assert ladder.consistent
    assert ladder.base_inferred
    assert ladder.implied_base == pytest.approx(4_250_000, rel=0.02)


def test_a_misread_option_breaks_the_invariant_and_is_not_used():
    """A Lakh read as a Crore still looks like a ladder; dividing back to a base is what catches it."""
    rungs = find_rungs([
        S("Three projects of minimum value INR 32 Crore each"),
        S("Two projects of minimum value INR 40 Lakhs each"),
        S("One project of minimum value INR 64 Lakhs"),
    ])
    ladder = build_ladder(rungs)
    assert ladder is None or not ladder.consistent, "an impossible ladder must not be reported as usable"


def test_a_non_monotone_group_is_not_a_ladder():
    """More projects must cost less each. Two unrelated requirements that happen to sit together
    ("2 works ≥ ₹10 L" for one trade, "3 works ≥ ₹20 L" for another) are not alternatives."""
    rungs = find_rungs([
        S("Two completed works of value not less than Rs 10,00,000 each"),
        S("Three completed works of value not less than Rs 20,00,000 each"),
    ])
    assert build_ladder(rungs) is None


def test_window_of_years_prefers_the_longer_reading():
    """A window read too short rejects work the tender accepts, so ties go to the longer one."""
    assert find_window_years([S("Completion of similar type of works during last Five years.")]) == 5
    assert find_window_years([S("executing below mentioned project over last 3 years as on bid "
                                "submission date")]) == 3
    assert find_window_years([S("in the last 5 years from the date of submission of bid.")]) == 5
    assert find_window_years([S("The contract runs for three years.")]) is None


# --------------------------------------------------------------------------------------------
# 3. Price-level indexation
# --------------------------------------------------------------------------------------------

INDEX_TABLE = """
Turnover of previous years shall be given weightage of 10% per year based on
Rupee value to bring them to current year price level.
Financial year Multiplying Factor
2024-2025 1.10
2023-2024 1.21
2022-2023 1.33
2021-2022 1.46
2020-2021 1.61
"""


def test_index_table_is_read_with_its_rate():
    t = parse_index_table(INDEX_TABLE, page=109)
    assert t is not None
    assert t.factors == {2024: 1.10, 2023: 1.21, 2022: 1.33, 2021: 1.46, 2020: 1.61}
    assert t.stated_rate == pytest.approx(0.10)
    assert t.implied_rate == pytest.approx(0.10, abs=0.005)
    assert t.consistent
    assert t.current_year == 2025


def test_two_digit_financial_years_are_the_same_table():
    t = parse_index_table(
        "Multiplying factor to be applied\nFY 2024-25 : 1.10\nFY 2023-24 : 1.21\nFY 2022-23 : 1.33", page=1)
    assert t is not None and t.factors == {2024: 1.10, 2023: 1.21, 2022: 1.33}


def test_indexation_changes_a_turnover_verdict():
    """The whole point: the tender's own rule turns this bidder's fail into a pass."""
    t = parse_index_table(INDEX_TABLE, page=109)
    threshold = 3_654_521  # E x 0.75 / 1 year, PMC drainage bundle 2286446
    raw = 3_000_000  # best year, FY 2022-23
    assert raw < threshold, "raw figure is below the bar"
    assert escalate(raw, 2022, t).value == pytest.approx(3_990_000)
    assert escalate(raw, 2022, t).value >= threshold, "indexed figure clears it"


def test_a_year_at_the_current_price_level_is_not_escalated():
    t = parse_index_table(INDEX_TABLE, page=109)
    assert escalate(5_000_000, 2025, t).value == 5_000_000
    assert not escalate(5_000_000, 2025, t).applied


def test_an_unknown_year_is_left_alone_rather_than_guessed():
    t = parse_index_table(INDEX_TABLE, page=109)
    e = escalate(5_000_000, None, t)
    assert e.value == 5_000_000 and not e.applied


MUST_REFUSE_INDEX = [
    # A year-and-money table. Reading rupees as multipliers would multiply a turnover by 4,872,695.
    ("Financial year Estimated Amount\n2024-2025 4872695\n2023-2024 3200000\n2022-2023 2100000", "no factors"),
    # Percentages of completion, not price-level factors, and no intro phrase.
    ("Year Progress\n2024-2025 0.40\n2023-2024 0.50\n2022-2023 0.80", "no intro"),
    # Fewer rows than make a progression.
    ("Multiplying Factor\n2024-2025 1.10", "one row"),
    # An intro but no table: a bare escalation clause states no factors to apply.
    ("Price variation clause will be applicable for all works under PMC from day one.", "prose only"),
]


@pytest.mark.parametrize("text,why", MUST_REFUSE_INDEX)
def test_index_tables_that_must_be_refused(text, why):
    assert parse_index_table(text, page=1) is None, why


def test_factors_that_do_not_follow_one_rate_are_reported_not_applied():
    t = parse_index_table(
        "Multiplying Factor to bring them to current year price level\n"
        "2024-2025 1.10\n2023-2024 1.21\n2022-2023 2.90\n2021-2022 3.10", page=1)
    assert t is not None and not t.consistent
    assert escalate(1_000_000, 2022, t).applied is False, "an inconsistent table must not move money"


# --------------------------------------------------------------------------------------------
# 4. A qualification of the bidder vs an obligation of the contractor
# --------------------------------------------------------------------------------------------

def test_site_deployment_clause_is_not_a_personnel_criterion():
    """PMC drainage bundle 2286446 states both, 16 pages apart, with different numbers of years."""
    execution = S("Contractor should keep two nos of experienced Civil Engineers B.E (civil) at each "
                  "site. These Engineers should have minimum of 3 years experience of execution of "
                  "drainage lines.", page=94, in_table=False)
    assert rule_key_personnel(execution) is None


def test_the_qualification_row_is_the_personnel_criterion():
    qualification = S("Staff Minimum requirement Engineer B.E. Civil / DCE/Technical Staff of having "
                      "experience of min. 5 Years", page=110)
    draft = rule_key_personnel(qualification)
    assert draft is not None
    assert draft.params["min_years"] == 5


# --------------------------------------------------------------------------------------------
# 5. A requirement split over several table rows
# --------------------------------------------------------------------------------------------

def test_a_tall_cell_split_across_rows_is_rejoined():
    """PMC's DBT RFP p.15 rules every line, so one requirement becomes four rows with the evidence
    column interleaved between them. Row by row it states no number at all."""
    rows = [
        ["5", "Manpower Declaration: The Bidder must have an", ""],
        ["", "on-roll staff strength of a minimum of 30 IT", ""],
        ["", "", "Certificate from the Company Secretary (CS) or Head"],
        ["", "Professionals having Core Expertise in Software", ""],
        ["", "", "HR on the company's letterhead (as per Section 8.12)"],
        ["", "Development.", ""],
        ["6", "Self - Declaration on Non-Blacklisting", "Undertaking on Company's Letterhead"],
    ]
    out = _unwrap_rows(rows)
    assert len(out) == 2, "the six rows are two records"
    assert "staff strength of a minimum of 30 IT Professionals" in out[0][1]
    assert out[0][2].startswith("Certificate from the Company Secretary")
    assert out[1][1].startswith("Self - Declaration")


def test_a_table_without_a_label_column_is_left_alone():
    """A plain key/value form has no serial number, so every row is already its own record."""
    rows = [["Tender Fee", "INR 6079.00"], ["Earnest Money Deposit", "INR 80,000.00"]]
    assert _unwrap_rows(rows) == rows


STAFF_SHAPES = [
    ("The Bidder must have an on-roll staff strength of a minimum of 30 IT Professionals having "
     "Core Expertise in Software Development.", 30),
    ("The bidder shall have a staff strength of minimum 25 technical professionals.", 25),
    ("Bidder must have staff strength of at least 40 employees on its payroll.", 40),
    ("The agency should have a staff strength of not less than 12 IT staff.", 12),
]


@pytest.mark.parametrize("text,count", STAFF_SHAPES)
def test_staff_strength_shapes(text, count):
    draft = rule_staff_strength(S(text, in_table=False))
    assert draft is not None, f"no staff-strength criterion from: {text!r}"
    assert draft.threshold_value == count


# --------------------------------------------------------------------------------------------
# 6. The nature of similar work
# --------------------------------------------------------------------------------------------

NATURE_SHAPES = [
    ("a) Three completed works (sewer /storm water line/retaining wall) costing not less than 40%",
     "sewer /storm water line/retaining wall"),
    ("Two completed works (road / footpath / kerb) of similar nature", "road / footpath / kerb"),
]


@pytest.mark.parametrize("text,expected", NATURE_SHAPES)
def test_nature_is_taken_from_the_parenthetical(text, expected):
    assert _nature_in_clause(text) == expected


MUST_REFUSE_NATURE = [
    "Bid Capacity = {(A x N x 1.5) - B} = 100% of total estimated cost",   # arithmetic
    "Three completed works (a) costing not less than 40% of the cost",     # a clause label
    "Two works (3) of similar nature",                                      # a count
]


@pytest.mark.parametrize("text", MUST_REFUSE_NATURE)
def test_nature_that_must_be_refused(text):
    assert _nature_in_clause(text) is None, f"wrongly read a trade from: {text!r}"


# On the signed qualification sheet almost everything handwritten is a figure, and a clerk's
# correction to one comes back marked struck. Only a fragment naming a trade can be the nature of
# work; without this the sheet reported "printed '36.55' is struck out and replaced with
# 'sewer /storm water line/retaining wall'", which describes nothing that happened.
TRADE_FRAGMENTS = [
    ("sewer /storm water line/retaining wall", True),
    ("storm water", True),
    ("road / footpath / kerb", True),
    ("NP-III RCC Pipe - 600mm", True),
    ("36.55", False),
    ("19.49", False),
    ("Rs. 24.37 lacs", False),
    ("03", False),
    ("48.73 lacs", False),
    ("12", False),
    ("(5)", False),
]


@pytest.mark.parametrize("fragment,is_trade", TRADE_FRAGMENTS)
def test_only_a_trade_description_can_be_a_nature_of_work(fragment, is_trade):
    assert _names_a_trade(fragment) is is_trade, f"{fragment!r}"


# --------------------------------------------------------------------------------------------
# 7. Bidder submissions: money a scan's text layer has damaged
# --------------------------------------------------------------------------------------------
# Bidder documents are scanned, stamped paper. Their text layers substitute letters for digits and
# break Indian grouping with stray spaces, and both failures move money *downwards* — which is the
# direction that disqualifies. Every row here is verbatim from one of the nine Warje submissions.

REPAIRABLE = [
    # Stray spaces inside the grouping carry no information, so they are removed.
    ("Fina ncial Year 2019-20 Rs. 1,87 ,87 ,157 /-", 18787157),
    ("Financial Y ear 2O22-23 Rs. 50,14,106/-", 5014106),
    ("o Financial Year 2020-21 Rs. 30,03,325l/-", 3003325),   # trailing letter is noise, not a digit
]


@pytest.mark.parametrize("text,expected", REPAIRABLE)
def test_spacing_damage_is_repaired_not_refused(text, expected):
    got = parse_amounts(text)
    assert got and got[0].value == expected, f"{[a.raw for a in got]}"


UNREADABLE = [
    "' Financial Year 2023-24 Rs. 7O,62,407/-",     # 70,62,407 — would read as Rs 7
    "Financial Year 2027-22 Rs.77,83,O72/-",        # 77,83,072 — would read as Rs 7,783
    "2 2023-24 L,26,40 ,423",                       # 1,26,40,423 — matches no currency pattern at all
    "3 2024-25 2, L7 ,12,07 4",                     # 2,17,12,074
]


@pytest.mark.parametrize("text", UNREADABLE)
def test_letter_for_digit_damage_is_refused_never_guessed(text):
    """"O" is a 0 here and a 6 elsewhere. A guessed digit is a number the document never stated."""
    assert corrupt_amounts(text), f"not detected as damaged: {text!r}"
    for a in parse_amounts(text):
        assert a.value > 10_000, f"a truncated {a.raw!r} was returned as {a.value}"


NOT_DAMAGED = [
    "PAN : AQRPD4656N",                                  # letters and digits, but no grouping
    "UDIN : 25113775BMHXTG8789",
    "F.R.No. 123357W",
    "Flat No. 402, D Wing, Metro Greens Society",        # one comma, an address
    "September 17, 2025",
    "2020-2021 3,43,19,907/-",
    "Gugale & Associates, Chartered Accountants",
]


@pytest.mark.parametrize("text", NOT_DAMAGED)
def test_identifiers_and_addresses_are_not_mistaken_for_damaged_money(text):
    assert corrupt_amounts(text) == [], f"false alarm on {text!r}"


# --------------------------------------------------------------------------------------------
# 8. A financial year, and the grids that state one
# --------------------------------------------------------------------------------------------

FY_SHAPES = [("2020-2021", 2020), ("2020-21", 2020), ("FY 2024-25", 2024), ("F.Y.2019-20", 2019)]


@pytest.mark.parametrize("text,start", FY_SHAPES)
def test_financial_years(text, start):
    assert cell_year(text) == start


NOT_FY = ["2027-22", "2O2O-27", "2020-2022", "36/18", "411043"]


@pytest.mark.parametrize("text", NOT_FY)
def test_pairs_that_are_not_financial_years(text):
    """A scan turned 2021-22 into "2027-22". Filing a turnover under a year the certificate never
    states is a misread, so only consecutive years count."""
    assert cell_year(text) is None, f"{text!r} read as a financial year"


CELL_AMOUNTS = [("3,43,19,907/-", 34319907), ("41,96,280", 4196280), ("Rs. 6,78,75,525", 67875525),
                ("1.40", None), ("1 Year", None), ("03", None), ("", None), ("PARTICULARS", None)]


@pytest.mark.parametrize("text,expected", CELL_AMOUNTS)
def test_bare_cells_are_money_only_when_they_are_money(text, expected):
    """Currency lives in the column header, so a cell is a bare number — but so is the escalation
    factor sitting two rows below it."""
    assert cell_amount(text) == expected


def test_turnover_grid_is_read_row_wise():
    """A turnover certificate: the year and its amount are in different cells of one row, and the
    amount column's heading is what says the figures are turnover."""
    rows = [["Financial Year", "Turnover (Rs)"],
            ["2020-2021", "3,43,19,907/-"],
            ["2021-2022", "2,81,82,821/-"],
            ["2024-2025", "4,26,07,355/-"]]
    got = read_year_series([rows], want=TURNOVER_LABEL, avoid=DERIVED_LABEL)
    assert [(y.year, y.value) for y in got] == [(2020, 34319907), (2021, 28182821), (2024, 42607355)]


def test_bid_capacity_grid_is_read_column_wise_without_the_derived_rows():
    """The same figures turned ninety degrees, with the accountant's own escalation stacked under
    them. Reading "Updated Value of Work" as turnover would apply the factor twice."""
    rows = [["PARTICULARS", "2020-2021", "2021-2022"],
            ["Amount of Work During the Year", "3,43,19,907", "2,81,82,821"],
            ["Factor For Updating", "1.40", "1.30"],
            ["Updated Value of Work", "4,80,47,870", "3,66,37,667"]]
    raw = read_year_series([rows], want=WORK_DONE_LABEL, avoid=DERIVED_LABEL)
    assert [(y.year, y.value) for y in raw] == [(2020, 34319907), (2021, 28182821)]
    assert all("Updated" not in (y.label or "") for y in raw)


# --------------------------------------------------------------------------------------------
# 9. Work proof, in whatever shape the bidder sent it
# --------------------------------------------------------------------------------------------
# No two bidders prove completed work the same way, so the reader looks for a record — a
# description, a sum, a date it finished — rather than a schema. What it must never do is credit a
# bidder with work they have not done.

FORM_II = [
    ["Sr. No.", "", "", "Name of work", "", "", "Cost of work", "", "",
     "Date of starting", "", "", "", "Stipulated", "", "", "Actual date", "", "Remarks", "", ""],
    ["", "", "", "", "", "", "", "", "", "", "", "", "", "date of", "", "", "of", "", "", "", ""],
    ["", "", "", "", "", "", "", "", "", "", "", "", "", "completion", "", "", "completion", "", "", "", ""],
    ["1.", "", "", "Development of 24m DP road at Mahammadwadi", "", "", "10,33,94,909.00", "", "",
     "01.02.2024", "", "", "February 25", "", "", "March -25", "", "", "100 % completed", "", ""],
    ["2.", "", "", "Design, development of Scientific landfill", "", "", "17,84,87,759", "", "",
     "11.09.2019", "", "", "11.09.2020", "", "", "25.11.2020", "", "", "100 % Completed", "", ""],
]


def test_a_statement_of_completed_works_is_read():
    got = _from_table(FORM_II, page=91)
    assert [int(r.value) for r in got] == [103394909, 178487759]
    assert [r.year for r in got] == [2025, 2020]
    assert [r.value_label for r in got] == ["Cost of work", "Cost of work"]


def test_the_heading_may_sit_a_column_off_from_its_own_data():
    """In this bidder's grid both completion headings land one column right of their figures, so
    an exact-index lookup reads every completion date as blank and loses the five-year window."""
    got = _from_table(FORM_II, page=91)
    assert all(r.year is not None for r in got), "column drift lost the completion dates"
    assert got[0].date == "March -25" and got[0].year_certain is False
    assert got[1].date == "25.11.2020" and got[1].year_certain is True


WORK_IN_HAND = [
    ["Sr.", "Name of work", "Cost of Contract", "Cost of Remaining Work",
     "Anticipated Date of Completion", "Estimated Cost"],
    ["1", "Kondhwa Khurd sports ground", "8,66,58,616", "2,80,93,693", "03.01.2025", "8,66,58,616"],
]


def test_work_in_hand_is_not_completed_work():
    """Form I and Form II are the same grid under different headings, and the difference is the
    whole point: this bidder has not finished any of it."""
    assert _from_table(WORK_IN_HAND, page=88) == []


BILL_OF_QUANTITIES = [
    ["It. No.", "Item", "Executed Qty", "Rate", "Amount"],
    ["4", "Construction of granular sub-base by providing crushed stone", "1053.374 cum", "2152", "22,66,861"],
    ["9", "Providing and laying Cast in situ Ready Mix concrete", "585.914 cum", "3390", "19,86,248"],
]


def test_bill_of_quantities_lines_are_not_separate_completed_works():
    """One bidder's work order yielded nine "completed works", one per priced line of a single
    contract. A BOQ has a description and a money column; what it has no column for is a date the
    work finished."""
    assert _from_table(BILL_OF_QUANTITIES, page=131) == []


NOT_PROOF_PAGES = [
    "INDIAN INCOME TAX RETURN ACKNOWLEDGEMENT Assessment Year 2024-25 Total Income 14,75,310",
    "Receipt No.:5792 Date: 20/02/2026 Leave and Licenses Agreement Registration Fee: 500.00 "
    "Stamp duty of Rs.10912.00/- is paid",
    "Tax Invoice  Total Value of Supply in Rupees  Total SGST Payable  Engine No. N4H22ZAJ0529  "
    "L&T491 Mini Tandem Compactor  12,00,000",
]


@pytest.mark.parametrize("text", NOT_PROOF_PAGES)
def test_documents_that_carry_money_and_dates_but_prove_no_work(text):
    assert NOT_WORK_PROOF.search(text), f"not vetoed: {text[:60]!r}"


VALUE_ROLES = [
    ("Work Done Amount", "value_done"),
    ("Gross amount of final bill", "value_done"),
    ("Accepted tender Amount", "value_contract"),
    ("Cost of work", "value_contract"),
    ("Administration Approval", "value_other"),     # a sanction, not what was built
    ("Estimated Cost", "value_other"),
    ("Actual date of completion", "date_actual"),
    ("Stipulated date of completion", "date_stipulated"),
    ("Date of work starting", "date_started"),
    ("Name of work", "description"),
]


@pytest.mark.parametrize("label,role", VALUE_ROLES)
def test_column_headings_are_understood_by_what_they_say(label, role):
    """A completion certificate carries three different amounts. Which one is the value of the work
    is decided by its own label, not by which came first on the page."""
    assert _role_of(label) == role


COLLIDED = "18,0 6 , 7 3 , 3 7 , 1 5 3 1 8 , 0 3 , 3 4 , 1 8 1 3 . 0 3 . 2 0 2"


def test_collided_columns_state_no_amount_anybody_can_defend():
    """Three columns printed into one cell. Any figure recovered here would be invented."""
    from app.services.work_proof import _amounts
    assert all(v <= 100_000_000_000 for v in _amounts(COLLIDED))
    assert 180 not in _amounts(COLLIDED), "a truncated fragment was returned as an amount"


# ==============================================================================================
# 10. The same fact, worded by the tender and then by the bidder
#
# Every row below is a verbatim shape from the twelve-bid corpus or the five tender bundles. The
# point of the section is that the two sides share no vocabulary: the tender asks for "an on-roll
# staff strength of a minimum of 30 IT Professionals" and the bid answers "50+ technically
# qualified professionals on companies' payroll". Searching for the requirement's words finds
# nothing, and the criterion comes back unknown on a submission that answers it twice over.
# ==============================================================================================

REQUIREMENT_CONCEPTS = [
    # (requirement as the tender prints it, clause heading, datatype, concept it is about)
    ("The Bidder should have an average annual turnover of minimum of INR 4.0 Cr over the last Three "
     "years (FY 2022-23, 2023-24 and 2024-25).", "Financials", "currency", "turnover"),
    ("The Bidder should respectively have positive net worth in each of the last Three financial "
     "years (FY 2022-23, 2023-24 and 2024-25).", "Financials", "currency", "net_worth"),
    ("The Bidder must have an on-roll staff strength of a minimum of 30 IT Professionals having "
     "Core Expertise in Software Development.", "Manpower Declaration", "count", "staff_strength"),
    ("Technical Manpower Strength", "", "count", "staff_strength"),
    ("Solvency certificate from a scheduled bank", "", "currency", "solvency"),
    ("The Bidder should not have been blacklisted / debarred by any Govt. entity or any PSU in "
     "India as on the date of bid submission.", "", "boolean", "non_blacklisting"),
    ("The Bidder should be an entity registered in India under the Companies Act 1956 / 2013 or LLP "
     "registered under LLP Act 2008", "Legal Entity", "boolean", "legal_entity"),
]


@pytest.mark.parametrize("requirement,label,datatype,want", REQUIREMENT_CONCEPTS)
def test_a_requirement_resolves_to_what_it_is_about(requirement, label, datatype, want):
    from app.services.concepts import resolve

    got = resolve(requirement, label=label, datatype=datatype)
    assert got is not None and got.key == want


# How a bid states each fact, and what the cue is worth. "anchor" names the fact on its own;
# "support" is consistent with it but names other things too; None is a page that says the word and
# states no value of it, which must never be read as evidence.
BIDDER_PHRASINGS = [
    # --- turnover, eleven shapes across twelve bids ---
    ("turnover", "Financial Year Turnover (Rs) 2020-2021 3,43,19,907/-", "anchor"),
    ("turnover", "Financial Year Sales Turnover (in Work done crore) 2020-200", "anchor"),
    ("turnover", "Sr No Financial Year Annual Turnover 1 Year 1 (2024-25)", "anchor"),
    ("turnover", "1 Turnover 407 417 453", "anchor"),
    ("turnover", "ANNUAT TURNOVER CERTIFICATE This is to certify that to the best of our knowledge", "anchor"),
    ("turnover", "their annual turnovers for last three consecutive years are as under", "anchor"),
    ("turnover", "the value of Business Turnover of, MR.SANDIP SAVATA ZAGADEPROP OF M/s. SANDIP ENTERPRISES", "anchor"),
    ("turnover", "Date of filing Return Period Taxable Turnover Total Turnover 1 27ADFFS8462G1ZP", "anchor"),
    ("turnover", "TDS TDS claimed Gross receipt", "anchor"),
    ("turnover", "I Revenue From Operations 15", "support"),
    ("turnover", "Note 31 March 2025 31 March 2024 Revenue from Operations 18", "support"),
    # The GST declaration on every tax invoice in every bid: the word, beside invoice amounts,
    # reporting nobody's turnover. Read as evidence it invents a figure on a real page.
    ("turnover", "of sale covered by this tax invoice has been effected by me /us and it shall be "
                 "accounted for in the turnover of sales while filing of return and the due tax", None),
    ("turnover", "s and it shall be accounted for in A/c Holder's Name : OM ENGINEERS the turnover "
                 "of supplies while filling of return and the due tax, Bank Name : Cosmos Bank", None),

    # --- net worth: stated once, derivable otherwise ---
    ("net_worth", "2 Net-Worth 255 293 332", "anchor"),
    ("net_worth", "I. EQUITY AND LIABILITIES (1) Shareholders Fund (a) Share Capital 3", "support"),
    ("net_worth", "Reserves and Surplus 2,41,33,019", "support"),
    # The auditor describing the method is not the bidder's figure.
    ("net_worth", "I. Verification of Net-worth: Net-worth is determined by subtracting total "
                  "liabilities from total assets", None),

    # --- people: the requirement counts them, the bid names them ---
    ("staff_strength", "Undertaking for number of technically qualified professionals on companies' payroll", "anchor"),
    ("staff_strength", "I hereby declare that our organization has 50+ technically qualified "
                       "professionals having core expertise in Software development", "anchor"),
    ("staff_strength", "STATEMENT NO -IV Details of Technical Personnel Available with Contractor", "anchor"),
    ("staff_strength", "Technical Staff Certificate This is to certify that the following technical "
                       "staff is currently working with company / organization", "anchor"),
    ("staff_strength", "Sr. No. Employee Name Educational Qualification Total Experience in Years", "support"),
    # A works contract's deployment clause names the same cadres and promises the future.
    ("staff_strength", "The contractor shall deploy one graduate engineer at site during the "
                       "execution of the work", None),
    ("staff_strength", "A qualified site engineer will be appointed before commencement", None),

    # --- a portal heading and an RFP clause are not the bidder's declaration ---
    ("non_blacklisting", "BID MANAGEMENT User Management Debarred/Revoked Details Bank Response", None),
    ("non_blacklisting", "We hereby declare that our firm has not been blacklisted or debarred by "
                         "any Government entity", "anchor"),
    ("quality_certification", "the requirement of: ISO 9001:2015 Quality Management System", "anchor"),
]


@pytest.mark.parametrize("key,text,want", BIDDER_PHRASINGS)
def test_how_a_bid_states_each_fact(key, text, want):
    from app.services.concepts import BY_KEY

    assert BY_KEY[key].hit(text) == want


# A bid opens with a checklist of its own contents and the tender closes with the same list. Those
# rows name every concept exactly, next to numbers that are page ranges. One of them read as
# evidence produced a net worth of ₹2,022 for a company certifying ₹3.32 crore.
CHECKLIST_ROWS = [
    ("net_worth", "Copy of the audited total turnover, net-worth and Profit & Loss for financial "
                  "years 2022-23, 2023-24 and 2024-25 as per format mentioned in Section 8.5"),
    ("net_worth", "14. Net Worth Certificate 49-49"),
    ("turnover", "9 Turn Over and Net Worth Certificate Y Page No.12-15"),
    ("turnover", "13. Turnover Certificate 48-48"),
    ("staff_strength", "16 Manpower Declaration Y Page No.75-77"),
    ("turnover", "Sr. No. Checklist Items Compliance(Yes/No) Page No. and Section No. in bid"),
]


@pytest.mark.parametrize("key,row", CHECKLIST_ROWS)
def test_a_checklist_is_never_evidence(key, row):
    """A checklist says where a document is, never what it says."""
    from app.services.concepts import BY_KEY

    assert BY_KEY[key].hit(row) is None


# ==============================================================================================
# 11. Grids nobody ruled
#
# A chartered accountant aligns figures with spaces. pdfplumber reports no table, so every reader
# above it sees nothing on exactly the pages that carry the turnover, net worth and profit series.
# ==============================================================================================

ANNEXURE_A = [
    "(Annexure·A)",
    "A statement showing the Turnover Net-worth and Net Profit of Stark Digital Media Services",
    "Private Limited during preceding three years given below: -",
    "No. FY 2022-23 FY 2023-24 FY 2024-25",
    "Details",
    "Amt (Rs. in lakhs) Amt (Rs. in lakhs) Amt (Rs. in lakhs)",
    "1 Turnover 407 417 453",
    "2 Net-Worth 255 293 332",
    "3 Net Profit 41 38 39",
    "For BS and Co LLP",
]

UNRULED_SERIES = [
    ("turnover", 4_07_00_000.0, 4_53_00_000.0),
    ("net_worth", 2_55_00_000.0, 3_32_00_000.0),
    ("net_profit", 41_00_000.0, 39_00_000.0),
]


@pytest.mark.parametrize("row,first,last", UNRULED_SERIES)
def test_an_unruled_grid_is_read_with_the_unit_its_heading_states(row, first, last):
    """The unit sits in a second header row, under the years and above the figures. Read without
    it a turnover of ₹4.07 crore is reported as ₹407 and the bidder fails on a unit."""
    from app.services.year_tables import (
        NETWORTH_LABEL, PROFIT_LABEL, TURNOVER_LABEL, DERIVED_LABEL,
        grid_from_lines, read_year_series,
    )

    labels = {"turnover": TURNOVER_LABEL, "net_worth": NETWORTH_LABEL, "net_profit": PROFIT_LABEL}
    series = read_year_series(grid_from_lines(ANNEXURE_A), want=labels[row], avoid=DERIVED_LABEL)
    assert [s.year for s in series] == [2022, 2023, 2024]
    assert series[0].value == first and series[-1].value == last


def test_the_average_of_an_unruled_series_decides_the_criterion():
    """₹4.0 Cr required, three years of ₹4.07/4.17/4.53 Cr stated in lakhs on an unruled grid."""
    from app.services.year_tables import TURNOVER_LABEL, DERIVED_LABEL, grid_from_lines, read_year_series

    series = read_year_series(grid_from_lines(ANNEXURE_A), want=TURNOVER_LABEL, avoid=DERIVED_LABEL)
    assert sum(s.value for s in series) / len(series) >= 4_00_00_000


def test_a_serial_column_is_not_a_row_label():
    """"1 | Turnover | 407" labelled every row "1", and a filter for turnover matched nothing."""
    from app.services.year_tables import _label_of

    assert _label_of(["1", "Turnover", "407", "417", "453"]) == "Turnover"
    assert _label_of(["2", "Net-Worth", "255", "293", "332"]) == "Net-Worth"


def test_a_full_rupee_grid_is_not_rescaled():
    """A stray "lakh" anywhere on the page must not multiply figures already in rupees."""
    from app.services.year_tables import TURNOVER_LABEL, read_year_series

    grid = [[["Financial Year", "Turnover (Rs) in lakhs"],
             ["2020-2021", "3,43,19,907/-"], ["2021-2022", "2,81,82,821/-"]]]
    assert [s.value for s in read_year_series(grid, want=TURNOVER_LABEL)] == [3_43_19_907.0, 2_81_82_821.0]


# ==============================================================================================
# 12. A table of data is not a damaged page
#
# A roster carries no function words, so the common-word test calls it garbled; its text layer is
# then discarded and the page billed to OCR. Two of the three pages this recovers in the corpus are
# the staff rosters the IT-professionals count is read from.
# ==============================================================================================

PAGE_KINDS = [
    ("roster", "english",
     "15 Mohit Pandey BE 3.7\n16 Rushikesh Bairagi BE 3.5\n17 Sayali Bhosale Diploma in IT 3.5\n"
     "18 suryakanta Sahu BCA 3.5\n19 Hariom Gawande bachelor of Mass communication 3.4\n"
     "20 Lokesh Patil BE 3.3\n21 Pratik Patil MBA 4\n22 Dhiraj Pawar BE 3.2\n23 Manan Jain BCA 3.1"),
    ("prose", "english",
     "The contractor shall submit the certificate of completion for the work along with the final "
     "bill, and all measurements shall be recorded in the measurement book as per this tender."),
    ("shifted encoding", "garbled",
     ";?G;8@GCO b>JB?;I>E;(=BE(;BH>(B=;9<E(B:(J>?(zjw(p?>C>dBE;(=CBG:>:r(9@(BEA(D?<EF(9E@<?"),
    ("letter-spaced scan", "garbled",
     "W O R K C O M PL E T I O N CERTI F I CA TE Ac c ept ed t en der Am oun t 2t7.t2 f or t he w or k"),
]


@pytest.mark.parametrize("name,want,text", PAGE_KINDS)
def test_a_page_of_data_is_still_readable(name, want, text):
    from app.adapters.readability import classify_page

    assert classify_page(text).kind == want, name


# ==============================================================================================
# 13. Counting people a requirement asks for and a roster proves
#
# "An on-roll staff strength of a minimum of 30 IT Professionals" is answered by a declaration of
# "50+ technically qualified professionals on companies' payroll" and a roster numbered to 67 over
# three pages. Both are returned: the declaration is what the bidder asserts, the roster is the
# only one of the two that can be counted.
# ==============================================================================================

def _page(number, lines, tables=()):
    from app.adapters.pdf_parser import ParsedPage
    from app.adapters.readability import PageQuality

    return ParsedPage(number=number, text="\n".join(lines), lines=list(lines),
                      tables=[list(t) for t in tables], quality=PageQuality("english"))


def _roster_doc():
    """The DBT bid's shape: a contents page that numbers its own rows, then the roster on 75-77."""
    from app.adapters.pdf_parser import ParsedDocument, Sentence

    index = [f"{i} Annexure item {i} BE Y Page No.{i}-{i}" for i in range(1, 17)]
    intro = ["Undertaking for number of technically qualified professionals on companies' payroll",
             "I hereby declare that our organization has 50+ technically qualified professionals "
             "having core expertise in Software development as on 02/02/2026"]
    names = ["Aashwini Bankar MCA", "Vishal Yewle BE Electrical", "Kartik Shete BCA",
             "Neelamala Bengle BSC", "Vijay Ishware BCA", "Ranjit Mohol B.Com"]
    body = {75: range(1, 15), 76: range(15, 48), 77: range(48, 68)}
    pages = [_page(1, index)] + [_page(n, ([] if n != 75 else intro) +
                                       [f"{i} {names[i % len(names)]} {3 + i % 7}" for i in rng])
                                 for n, rng in body.items()]
    pages = [pages[0]] + [p for p in pages[1:]]
    sentences = [Sentence(page=75, text=intro[0], section="Undertaking"),
                 Sentence(page=75, text=intro[1], section="Undertaking")]
    # Pages 2-74 are absent from this fixture; the finder addresses pages by number, not position.
    filler = [_page(n, []) for n in range(2, 75)]
    return ParsedDocument(pages=[pages[0]] + filler + pages[1:], sentences=sentences)


STAFF_SPEC_REQ = ("The Bidder must have an on-roll staff strength of a minimum of 30 IT "
                  "Professionals having Core Expertise in Software Development.")


def test_both_routes_to_a_head_count_are_returned():
    from app.services.bidder_extraction import CriterionSpec, find_staff_strength

    spec = CriterionSpec(key="min_staff_strength", label="Minimum IT Staff on Payroll", operator=">=",
                         params={}, threshold_value=30, threshold_unit="people",
                         requirement=STAFF_SPEC_REQ)
    by_source = {c.detail.get("source"): c for c in find_staff_strength(_roster_doc(), spec)}
    assert by_source["declared"].value_numeric == 50
    assert by_source["roster"].value_numeric == 67


def test_the_contents_page_does_not_steal_the_rosters_low_numbers():
    """A single dict keyed by serial is first-come-first-served, and the index page — scanned
    first, numbering its own rows 1, 2, 3 — took them. The roster then looked as though it began
    at 5 and was discarded, losing a 67-name roster entirely."""
    from app.services.bidder_extraction import CriterionSpec, find_staff_strength

    spec = CriterionSpec(key="min_staff_strength", label="Staff", operator=">=", params={},
                         threshold_value=30, threshold_unit="people", requirement=STAFF_SPEC_REQ)
    roster = next(c for c in find_staff_strength(_roster_doc(), spec)
                  if c.detail.get("source") == "roster")
    assert roster.detail["first_page"] == 75 and roster.detail["last_page"] == 77
    assert roster.page == 75, "cited the contents page instead of the roster"


def test_a_requirement_echoed_back_is_not_the_bidders_own_count():
    """Bids reproduce the RFP clause. Reading its number as the claim hands every bidder exactly
    the threshold — a pass on the tender's own words."""
    from app.adapters.pdf_parser import ParsedDocument, Sentence
    from app.services.bidder_extraction import CriterionSpec, find_staff_strength

    echo = ("We confirm that we have read the requirement for an on-roll staff strength of a "
            "minimum of 30 IT Professionals having core expertise in Software Development.")
    doc = ParsedDocument(pages=[_page(1, [echo])], sentences=[Sentence(page=1, text=echo, section="")])
    spec = CriterionSpec(key="min_staff_strength", label="Staff", operator=">=", params={},
                         threshold_value=30, threshold_unit="people", requirement=STAFF_SPEC_REQ)
    assert find_staff_strength(doc, spec) == []


def test_a_bill_of_quantities_row_is_not_a_person():
    """A BOQ line has a roster's exact shape: serial, description, number."""
    from app.adapters.pdf_parser import ParsedDocument, Sentence
    from app.services.bidder_extraction import CriterionSpec, find_staff_strength

    boq = ["1 Excavation in ordinary soil 2500", "2 Providing and laying PCC 1:4:8 1800",
           "3 Supplying and fixing MS grating 940"]
    doc = ParsedDocument(
        pages=[_page(1, ["Details of Technical Personnel"] + boq)],
        sentences=[Sentence(page=1, text="Details of Technical Personnel", section="")])
    spec = CriterionSpec(key="min_staff_strength", label="Staff", operator=">=", params={},
                         threshold_value=3, threshold_unit="people", requirement=STAFF_SPEC_REQ)
    assert not [c for c in find_staff_strength(doc, spec) if c.detail.get("source") == "roster"]


# ==============================================================================================
# 14. Where a criterion's threshold comes from
#
# Nothing in this system knows what any tender requires. A criterion is recognised by the *shape*
# of its clause and its number is read out of the clause, which is why the same recogniser yields
# ₹4 crore on PMC's DBT RFP, ₹25 crore on MahaIT's and ₹13 lakh on PCMC's.
# ==============================================================================================

THRESHOLDS_FROM_THE_PAGE = [
    # (clause as the tender prints it, criterion key, threshold the system must compare against)
    ("Financials: The Bidder should have an average annual turnover of minimum of INR 4.0 Cr over "
     "the last Three years (FY 2022-23, 2023-24 and 2024-25).", "avg_turnover", 4_00_00_000.0),
    ("The Bidder should have an average annual turnover of minimum INR 25 Cr over the last three "
     "financial years.", "avg_turnover", 25_00_00_000.0),
    ("The Bidder shall have a minimum average annual turnover of Rs. 13,00,000 during the last "
     "three financial years.", "avg_turnover", 13_00_000.0),
    ("Manpower Declaration: The Bidder must have an on-roll staff strength of a minimum of 30 IT "
     "Professionals having Core Expertise in Software Development.", "min_staff_strength", 30.0),
    ("The Bidder must have an on-roll staff strength of at least 75 technical professionals.",
     "min_staff_strength", 75.0),
]


@pytest.mark.parametrize("clause,key,threshold", THRESHOLDS_FROM_THE_PAGE)
def test_the_threshold_is_read_out_of_the_clause(clause, key, threshold):
    from app.adapters.pdf_parser import Sentence
    from app.services.criteria_extraction import RULES

    s = Sentence(page=1, text=clause, section="Eligibility")
    drafts = [d for d in (rule(s) for rule in RULES) if d is not None]
    match = next((d for d in drafts if d.key == key), None)
    assert match is not None, f"no recogniser produced {key}"
    assert match.threshold_value == threshold


def test_a_document_checklist_row_is_not_given_a_figure_reader():
    """"Turn Over and Net Worth Certificate" in a list of enclosures resolves to the turnover
    concept perfectly well, and is still asking whether a document is in the envelope. Answering it
    with a figure found on some other page is the right verdict for the wrong reason."""
    from app.services.bidder_extraction import (
        CriterionSpec, _finder_for, find_concept_presence, find_turnover_concept,
    )

    enclosure = CriterionSpec(key="doc_turn_over_and_net_worth_certificate",
                              label="Turn Over and Net Worth Certificate", operator="exists",
                              params={}, requirement="9. Turn Over and Net Worth Certificate")
    # The presence reader, which asks whether the certificate is there — never the figure reader.
    assert _finder_for(enclosure) is find_concept_presence

    measured = CriterionSpec(key="avg_turnover_2", label="Minimum Average Annual Turnover",
                             operator=">=", params={}, threshold_value=4_00_00_000.0,
                             threshold_unit="INR",
                             requirement="The Bidder should have an average annual turnover of "
                                         "minimum of INR 4.0 Cr over the last Three years")
    assert _finder_for(measured) is find_turnover_concept


# ==============================================================================================
# 15. The rest of the requirement families
#
# Thirty concepts now, harvested from what the five RFPs actually ask for rather than invented.
# Every row below is a verbatim shape from a real bid or a real tender format.
# ==============================================================================================

MORE_PHRASINGS = [
    # --- identity, proved by a number only one issuer can mint ---
    ("pan", "eccan Station, Deccan Gymkhana, Pune - 471.004 (PAN BZSPB8994G ) were as under", "identifier"),
    ("pan", "Proprietor Mr. Amol Navnath Dudhe having PAN : AQRPD4656N, Flat No. 9", "identifier"),
    ("gst", "Annexure A GSTIN 27BZSPB8994G1ZL Legal Name", "identifier"),
    # A supplier's GSTIN on a purchase invoice is the supplier's, and a bid is full of them.
    ("gst", "Date : GSTIN : 27AQRPD4656N1Z5 ConSignee (Ship To) :-", None),
    ("msme_udyam", "UDYAM REGISTRATION NUMBER UDYAM-MH-26-0071650 NAME OF ENTERPRISE", "identifier"),
    ("legal_entity", "U72900PN2013PTC146869 08th April 2013", "identifier"),

    # --- authority to sign: the instrument, not the footer ---
    ("signing_authority", "POWER OF ATTORNEY  I, Amit Mohol, Director, do hereby appoint", "anchor"),
    ("signing_authority", "“RESOLVED FURTHER THAT any one of the following officers be authorised", "anchor"),
    # "Authorised Signatory" closes every tax invoice, insurance schedule and vendor letter in a bid
    # — nineteen of them in this corpus, on pages about cheque payment and jurisdiction.
    ("signing_authority", "Authorised Signatory TERMS & CONDITIONS : 1.Cheque/DD in favour of Siddharth A", None),
    ("signing_authority", "AUTHORISED SIGNATORY Note: This is computer generated copy, does not need any s", None),

    # --- ESIC: the one concept where the likeliest thing on the page is a denial ---
    ("employee_insurance", "As on date of your application, your establishment is not registered with ESIC.", None),
    ("employee_insurance", "under section 1(3) of the esi. act, 1948 is applicable to all factories/establ", None),
    ("employee_insurance", "A State wise list of ESI Dispensaries is available on our website www.esic.nic.in", None),
    ("employee_insurance", "ENSURE - TO INSURE ALL ELIGIBLE WORKERS WITH ESI FOR TOTAL SOCIAL SECURITY", None),
    ("employee_insurance", "ESIC registration certificate of the establishment is enclosed", "anchor"),
    ("provident_fund", "your establishment is registered with Employees' Provident Fund Organisation "
                        "with the following code number : Code Nu", "anchor"),

    # --- fees, dates, accounts ---
    ("tender_fee", "Tender Fee in | 6,579 My Bids History", "anchor"),
    ("tender_fee", "EMD Amount in Rs : 80,000 Tender Fee", None),
    ("firm_age", "DATE OF INCORPORATION / REGISTRATION OF ENTERPRISE 27/12/2019", "anchor"),
    ("firm_age", "Date of commencement of business / 01/02/2013", "anchor"),
    ("firm_age", "valid Fitness certificate, as applicable, on the date of commencement of the Policy", None),
    ("audited_accounts", "BALANCE SHEET AS AT 31.03.2024", "anchor"),

    # --- experience, as a document rather than a measured value ---
    ("work_order", "WORK ORDER To, S.S Construction, Prop- HUSE", "anchor"),
    ("work_order", "Total Work Portion Amount from work order 8,472,98 8,366, 2,778, 2,884,", None),
    ("completion_certificate", "WORK COMPLETION CERTIFICATE CERTIFICATE IN RESPECT OF WORK EXECUTED BY", "anchor"),
    ("project_experience", "Bidder’s Experience - Details of similar work undertaken", "anchor"),
    ("project_experience", "List of completed/on-going assignments of similar nature", "anchor"),

    # --- machinery: a rate-analysis line prices a roller by the hour and owns nothing ---
    ("machinery", "83.80 P.no ROLLER 934.00 HR", None),
    ("machinery", "o4.33 Cum Extra ITEM 1.50% ROLLER 39.00 H", None),
    ("machinery", "List of Plant and Machinery owned by the contractor", "anchor"),

    # --- declarations, each of which is itself a negative and must not suppress itself ---
    ("not_insolvent", "We declare that we are not insolvent, in receivership or bankrupt as on the date", "anchor"),
    ("not_insolvent", "The bidder has not been declared bankrupt by any court", "anchor"),
    ("not_insolvent", "Self-Declaration: The Bidder should not be insolvent, in receivership or bankrupt", None),
    ("anti_collusion", "We hereby certify and confirm that in the preparation of our bid there was no collusion", "anchor"),
    ("no_deviation", "We certify that our offer contains no deviation either technical or commercial", "anchor"),
    ("non_disclosure", "NON-DISCLOSURE AGREEMENT entered into between Pune Municipal Corporation and", "anchor"),
    ("no_joint_venture", "We are submitting this bid in our own individual capacity and not as a joint venture", "anchor"),
    ("bid_forms", "Dear Sir, I, having read and examined in detail all the bidding documents", "anchor"),
    ("bidder_particulars", "4. Registered office address 411045", "anchor"),
]


@pytest.mark.parametrize("key,text,want", MORE_PHRASINGS)
def test_how_a_bid_states_the_rest_of_the_facts(key, text, want):
    from app.services.concepts import BY_KEY

    assert BY_KEY[key].hit(text) == want


REQUIREMENTS_TO_CONCEPTS = [
    ("Copy of PAN", "pan"),
    ("Copy of Goods and Services Tax Identification Number (GSTIN)", "gst"),
    ("A Board Resolution or Power of Attorney, in the name of the person executing the bid", "signing_authority"),
    ("Self-Declaration: The Bidder should not be insolvent, in receivership or bankrupt", "not_insolvent"),
    ("Anti-Collusion Certificate", "anti_collusion"),
    ("No– Deviation Certificate", "no_deviation"),
    ("Non-Disclosure Agreement", "non_disclosure"),
    ("Audited Balanced sheet and Profit & Loss Statements", "audited_accounts"),
    ("Proof of Existence of Firm from last 5 years", "firm_age"),
    ("Bid Document Fee Details Amount Reference No", "tender_fee"),
    ("Record for poor performance such as abandoning the works", "past_performance"),
    ("Sub-Contracting/ Consortium/ JV of companies/ firms etc. is not allowed", "no_joint_venture"),
    ("CV of Key Professionals (Appendix-8)", "key_roles"),
    ("List of completed/on-going assignments of similar nature (Past Experience Details)", "project_experience"),
    ("Pre-Qualification Bid Covering letter", "bid_forms"),
    ("General Details of the Bidder (Appendix-3)", "bidder_particulars"),
    ("Valid certifications:- CMMI Level 3 or Higher", "quality_certification"),
]


@pytest.mark.parametrize("requirement,want", REQUIREMENTS_TO_CONCEPTS)
def test_the_rest_of_the_requirements_resolve_too(requirement, want):
    from app.services.concepts import resolve

    got = resolve(requirement, label=requirement, datatype="boolean")
    assert got is not None and got.key == want


# ==============================================================================================
# 16. Precision of a presence check
#
# Coverage bought with a bad citation is worse than no coverage: a pass whose page does not say
# what the committee is told it says destroys trust in every other row of the grid.
# ==============================================================================================

def _doc(pages, sentences=()):
    from app.adapters.pdf_parser import ParsedDocument, Sentence

    return ParsedDocument(pages=list(pages),
                          sentences=[Sentence(page=p, text=t, section="") for p, t in sentences])


def _presence(label, pages, sentences=(), requirement=""):
    from app.services.bidder_extraction import CriterionSpec, find_concept_presence

    spec = CriterionSpec(key="doc_x", label=label, operator="exists", params={},
                         requirement=requirement or label)
    return find_concept_presence(_doc(pages, sentences), spec)


CONTENTS = [
    "Checklist for the documents to be included in the Pre-Qualification (PQ) Folder",
    "Sr. No. Checklist Items Compliance(Yes/No) Page No. and Section No. in bid",
]


def test_a_bid_claiming_to_contain_a_certificate_is_not_the_certificate():
    """Where the contents list is ruled, each row is a bare title with the page range in another
    cell, so the row carries none of the marks a checklist line usually has. The page is still
    unmistakable from its heading, and a pass cited to it is circular."""
    rows = CONTENTS + ["Anti-Collusion Certificate", "No Deviation Certificate",
                       "Board Resolution / Power of Attorney"]
    page = _page(1, rows)
    claims = _presence("Anti-Collusion Certificate", [page],
                       [(1, "Anti-Collusion Certificate")])
    assert claims == [], "evidence was taken from the bid's own table of contents"


def test_the_real_declaration_is_cited_not_the_contents_row():
    real = "We hereby certify and confirm that in the preparation of our bid there was no collusion."
    claims = _presence("Anti-Collusion Certificate",
                       [_page(1, CONTENTS + ["Anti-Collusion Certificate"]), _page(70, [real])],
                       [(1, "Anti-Collusion Certificate"), (70, real)])
    assert [c.page for c in claims] == [70]


def test_a_roster_row_is_not_evidence_of_a_particular_role():
    """"Diploma in Graphic Designer" on a staff roster is personnel evidence and says nothing about
    a Database Administrator. Both criteria resolve to the same concept, so only the words of the
    clause can separate them."""
    roster = ["1 Rohit Jadhav Diploma in Graphic Designer 2.6", "2 Mithun Sen BSC 3"]
    claims = _presence("Database Administrator Part A: Design and Development of the new Software",
                       [_page(76, roster)], [(76, roster[0])])
    assert all(c.detail.get("weak") for c in claims), "cited a designer as a database administrator"


def test_evidence_of_the_right_kind_but_the_wrong_thing_is_unknown_not_a_pass():
    from app.services.bidder_extraction import CriterionSpec
    from app.services.evaluation import evaluate_criterion

    roster = ["1 Rohit Jadhav Diploma in Graphic Designer 2.6"]
    spec = CriterionSpec(key="doc_dba", label="Database Administrator", operator="exists", params={},
                         requirement="Database Administrator")
    claims = _presence("Database Administrator", [_page(76, roster)], [(76, roster[0])])
    r = evaluate_criterion(spec, claims, "Database Administrator")
    assert r.verdict == "unknown" and "names what this criterion asks for" in r.reason


def test_an_identifier_is_self_proving():
    """A GSTIN needs no corroborating word near it. Demoting the one on the bidder's own GST
    certificate, because that page never spells out "GST", handed the criterion to a page that
    mentioned GST returns in passing."""
    claims = _presence("GST Registration",
                       [_page(6, ["27AASCS9581M1Z5", "Legal Name STARK DIGITAL MEDIA SERVICES"]),
                        _page(12, ["reconciled the Turnover declared in GST returns with the books"])],
                       [(6, "27AASCS9581M1Z5")])
    assert claims and claims[0].page == 6 and not claims[0].detail.get("weak")


# ==============================================================================================
# 17. Marathi, and tenders and bids that are not in the same language
#
# A PMC works tender is written in Marathi and answered by bids whose balance sheets, GST
# certificates and work orders are in English; an English RFP draws bids with Marathi completion
# certificates stapled into them. Both directions have to work, and the concept is what bridges
# them because it is the only thing that carries both vocabularies.
#
# Every Marathi string below is verbatim from the PMC works corpus.
# ==============================================================================================

MARATHI_MONEY = [
    ("र.रु.१.५० कोटी मर्यादितपर्यंतची कामे", 1_50_00_000.0),
    ("रक्कम रुपये २५.०० लाख", 25_00_000.0),
    ("रु. १,२३,४५,६७८/-", 1_23_45_678.0),
    ("एकूण र.रु. ४,२१,८७३ मात्र", 4_21_873.0),
]


@pytest.mark.parametrize("text,want", MARATHI_MONEY)
def test_marathi_money_is_read(text, want):
    """Nothing above this parsed at all before: "रु." is not "Rs." to a pattern that only knows
    "Rs.", and Devanagari digits are not digits to a pattern that only knows ASCII."""
    from app.services.values import parse_amounts

    amounts = parse_amounts(text)
    assert amounts and amounts[0].value == want


def test_a_marathi_amount_is_quoted_as_the_page_prints_it():
    """The scan runs on a copy with the digits folded to ASCII; the quote comes out of the original,
    so the committee is shown what the page says."""
    from app.services.values import parse_amounts

    assert parse_amounts("रु. १,२३,४५,६७८/-")[0].raw == "रु. १,२३,४५,६७८/-"


MARATHI_DURATIONS = [
    ("मागील ५ वर्षातील जास्तीत जास्त", 5, "years"),
    ("गेल्या ५ (पाच) वर्षातील", 5, "years"),       # the figure repeated in words, in brackets
    ("कालावधी ६ महिने", 6, "months"),
    ("१२ महिन्यांत पूर्ण", 12, "months"),
    ("१० वर्षांनंतर प्रतिवर्षी", 10, "years"),
]


@pytest.mark.parametrize("text,value,unit", MARATHI_DURATIONS)
def test_marathi_durations_are_read(text, value, unit):
    """Marathi inflects the unit rather than adding a word: वर्ष, वर्षे, वर्षात, वर्षातील,
    वर्षांनंतर are all "years"."""
    from app.services.values import parse_durations

    found = parse_durations(text, unit=unit)
    assert found and found[0].value == value


MARATHI_OBLIGATIONS = [
    "निविदाधारकाने वैध नोंदणी प्रमाणपत्र सादर करणे आवश्यक आहे.",
    "सदर रक्कम बयाणा रक्कम म्हणून भरावी लागेल.",
    "कंत्राटदारास सर्व कागदपत्रे जोडणे बंधनकारक राहील.",
    "ही अट सर्व निविदाधारकांना अनिवार्य आहे.",
]


@pytest.mark.parametrize("text", MARATHI_OBLIGATIONS)
def test_a_marathi_sentence_can_state_an_obligation(text):
    """Marathi states the obligation as a verb ending, not a modal — there is no word for "shall".
    Until this was read, every rule refused every Marathi sentence and a wholly Marathi tender
    produced no criteria at all."""
    from app.adapters.pdf_parser import Sentence
    from app.services.criteria_extraction import _required

    assert _required(Sentence(page=1, text=text, section=""))


def test_a_marathi_turnover_clause_becomes_a_criterion():
    from app.adapters.pdf_parser import Sentence
    from app.services.criteria_extraction import rule_turnover

    clause = ("निविदाधारकाची मागील ५ (पाच) वर्षातील सरासरी वार्षिक आर्थिक उलाढाल "
              "किमान र.रु.२५.०० लाख असणे आवश्यक आहे.")
    draft = rule_turnover(Sentence(page=1, text=clause, section="पात्रता"))
    assert draft is not None
    assert draft.threshold_value == 25_00_000.0
    assert draft.params["window_years"] == 5 and draft.params["average"] is True


MARATHI_CONCEPT_LINES = [
    ("turnover", "मागील ५ वर्षात, कंत्राटदाराची कामाची उलाढाल", "anchor"),
    ("project_experience", "मागील ५ वर्षात, कंत्राटदाराने त्याच स्वरूपाच्या तुल्यबळ किंमतीच्या, "
                           "पूर्ण केलेल्या कामाची किमान किंमत.", "anchor"),
    ("project_experience", "समकक्ष कामांचा दाखला कार्यकारी अगर तत्सम पद धारण करणाऱ्या अधिका-याच्या "
                           "स्वाक्षरीचा असावा", "anchor"),
    ("legal_entity", "भारतातील कोणत्याही शासकीय/निमशासकीय संस्थेकडील वैध पूर्वनोंदणी प्रमाणपत्र "
                     "आवश्यक आहे", "anchor"),
    ("signing_authority", "(२) पॉवर ऑफ अटर्नी बाबतची स्पष्टीकरणे", "anchor"),
    ("audited_accounts", "चार्टर्ड अकाउंटंट प्रमाणित केलेली निविदाधारकाची मागील पाच वर्षातील "
                         "आर्थिक उलाढाल", "anchor"),
    ("completion_certificate", "कंत्राटदाराने समाधानकारकरीत्या काम पूर्ण केले असल्याबाबत", "anchor"),
    ("staff_strength", "अ.क्र. | नाव | शैक्षणिक अर्हता | कामाचा अनुभव | निविदाधारकाकडे कार्यरत कालावधी",
     "anchor"),
    # Every PMC certificate is signed by one of these. Counting the Superintending Engineer who
    # issues a certificate as proposed personnel staffs the bid from the municipality.
    ("key_roles", "अधीक्षक अभियंता (यांत्रिकी) यांच्या योग्यता प्रमाणपत्राची आवश्यकता नाही", None),
    ("key_roles", "कार्यकारी अभियंता / खानेप्रमुख यांनी अभिलेखीत करावे", None),
]


@pytest.mark.parametrize("key,text,want", MARATHI_CONCEPT_LINES)
def test_how_a_marathi_document_states_each_fact(key, text, want):
    from app.services.concepts import BY_KEY

    assert BY_KEY[key].hit(text) == want


TWINS = [
    ("मागील ५ वर्षातील जास्तीत जास्त वार्षिक उलाढाल", "maximum annual turnover over the last 5 years", "currency"),
    ("त्याच स्वरूपाच्या पूर्ण केलेल्या कामाचा अनुभव", "experience of completed works of similar nature", "boolean"),
    ("पॉवर ऑफ अटर्नी", "power of attorney", "boolean"),
    ("निविदा शुल्क", "tender fee", "currency"),
    ("ताळेबंद व नफा तोटा पत्रक", "balance sheet and profit & loss statement", "boolean"),
    ("यंत्रसामग्रीची यादी", "list of machinery", "boolean"),
    ("काळ्या यादीत नसल्याचे प्रतिज्ञापत्र", "undertaking of not being blacklisted", "boolean"),
]


@pytest.mark.parametrize("marathi,english,datatype", TWINS)
def test_a_requirement_resolves_the_same_in_either_language(marathi, english, datatype):
    from app.services.concepts import resolve

    a, b = resolve(marathi, label=marathi, datatype=datatype), resolve(english, label=english, datatype=datatype)
    assert a is not None and b is not None and a.key == b.key


def test_the_clause_heading_outweighs_its_body():
    """A row headed "Power of Attorney" inside a clause that also mentions a registration
    certificate is about the power of attorney."""
    from app.services.concepts import resolve

    body = "निविदाधारकाने वैध नोंदणी प्रमाणपत्र व पॉवर ऑफ अटर्नी सादर करणे आवश्यक आहे."
    assert resolve(body, label="पॉवर ऑफ अटर्नी", datatype="boolean").key == "signing_authority"
    assert resolve(body, label="वैध नोंदणी प्रमाणपत्र", datatype="boolean").key == "legal_entity"


def _bilingual_bid():
    english = [_page(4, ["CERTIFICATE OF INCORPORATION", "U72900PN2013PTC146869"]),
               _page(9, ["POWER OF ATTORNEY", "I, Amit Mohol, Director, do hereby appoint"])]
    marathi = [_page(14, ["कामाचा पूर्णत्वाचा दाखला", "समाधानकारकरीत्या काम पूर्ण केले आहे"]),
               _page(17, ["ताळेबंद व नफा तोटा पत्रक", "सनदी लेखापाल यांनी प्रमाणित केलेले"]),
               _page(19, ["यंत्रसामग्रीची यादी", "१ जेसीबी २ टिप्पर ३ रोलर"])]
    return _doc(english + marathi,
                [(p.number, line) for p in english + marathi for line in p.lines])


CROSS_LANGUAGE = [
    ("पॉवर ऑफ अटर्नी", "निविदाधारकाने पॉवर ऑफ अटर्नी सादर करणे आवश्यक आहे.", 9),
    ("वैध नोंदणी प्रमाणपत्र", "निविदाधारकाने वैध नोंदणी प्रमाणपत्र सादर करणे आवश्यक आहे.", 4),
    ("Work completion certificate", "The bidder shall submit a work completion certificate.", 14),
    ("Audited balance sheet", "The bidder shall submit an audited balance sheet.", 17),
    ("List of plant and machinery", "The bidder shall submit a list of plant and machinery owned.", 19),
    ("यंत्रसामग्रीची यादी", "निविदाधारकाने यंत्रसामग्रीची यादी सादर करणे आवश्यक आहे.", 19),
]


@pytest.mark.parametrize("label,requirement,page", CROSS_LANGUAGE)
def test_a_requirement_finds_its_evidence_in_the_other_language(label, requirement, page):
    """The distinctive-term test cannot run across alphabets — applied anyway it rejects every page
    and the criterion comes back unknown on a submission that answers it. The concept still holds,
    because it carries both vocabularies."""
    from app.services.bidder_extraction import CriterionSpec, find_concept_presence
    from app.services.evaluation import evaluate_criterion

    spec = CriterionSpec(key="doc_x", label=label, operator="exists", params={}, requirement=requirement)
    claims = find_concept_presence(_bilingual_bid(), spec)
    assert claims, f"no evidence found for {label}"
    assert claims[0].page == page
    assert evaluate_criterion(spec, claims, requirement).verdict == "pass"


def test_a_marathi_clause_still_has_distinctive_words_of_its_own():
    """Extracting only Latin words left a Marathi clause with no distinctive terms at all, so the
    precision gate was skipped entirely and every Marathi requirement matched every page of the
    right kind."""
    from app.services.bidder_extraction import CriterionSpec, _distinctive

    spec = CriterionSpec(key="k", label="पॉवर ऑफ अटर्नी", operator="exists", params={},
                         requirement="निविदाधारकाने पॉवर ऑफ अटर्नी सादर करणे आवश्यक आहे.")
    terms = _distinctive(spec)
    assert "पॉवर" in terms and "अटर्नी" in terms
    assert "निविदाधारकाने" not in terms and "आवश्यक" not in terms   # filler every clause contains
