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
