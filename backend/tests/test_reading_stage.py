"""The reading stage is allowed to be wrong; it is not allowed to be unverifiable.

Every test here feeds a model reply that would read plausibly to a committee and checks that it is
refused unless the submission actually says it. The gates matter more than the model: a figure
nobody can find on the cited page, shown beside a page number, is the one output of this system
that would destroy trust in all of it.
"""

from __future__ import annotations

import pytest

from app.adapters.llm import LLMError
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services import concepts, llm_verdict
from app.services.evaluation import evaluate_criterion

# A chartered accountant's statement in lakhs (invented figures).
ANNEXURE = "Turnover — FY 2022-23: 512"
ROSTER = ("We confirm that 45+ qualified engineers are on our payroll for software development "
          "as on 01/03/2026")


def claim(text: str, page: int = 15, **detail) -> Claim:
    return Claim(criteria_key="avg_turnover", value="x", page=page, quote=text,
                 section="Annexure-A", detail=detail)


class Replies:
    """A model that answers with whatever it is handed, once per role."""

    def __init__(self, *replies):
        self.replies, self.calls, self.last_model = list(replies), 0, "test-model"

    def generate_json(self, role, prompt, max_tokens=None):
        self.calls += 1
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


TURNOVER_SPEC = CriterionSpec("avg_turnover", "Turnover", ">=", {}, 40_000_000, "INR")
STAFF_SPEC = CriterionSpec("staff_strength", "Staff strength", ">=", {}, 30, "persons")
REQUIREMENT = ("The bidder shall have an average annual turnover of at least INR 4.0 Cr over the "
               "last three financial years (2022-23 to 2024-25).")


def read_one(reply, spans=None, spec=TURNOVER_SPEC, concept=concepts.TURNOVER):
    return llm_verdict.read(spec, concept, spans or [claim(ANNEXURE)], REQUIREMENT, Replies(reply))


# ----------------------------------------------------------------------------------------------
# The gates
# ----------------------------------------------------------------------------------------------

def test_a_figure_read_in_lakhs_becomes_rupees_by_our_arithmetic_not_the_models():
    """The model reports "512" and names the unit; the multiplication is ours. A model that cannot
    multiply cannot turn ₹5.12 crore into ₹512 crore."""
    out = read_one({"found": True, "reasoning": "stated in lakhs",
                    "items": [{"id": 0, "quote": "FY 2022-23: 512", "raw_figure": "512",
                               "unit": "lakh", "year": 2022, "confidence": 0.95}]})
    assert out.ok and len(out.readings) == 1
    r = out.readings[0]
    assert r.value == 5_12_00_000.0 and r.raw_figure == "512" and r.page == 15


def test_a_quote_that_is_not_on_the_page_is_refused():
    """The one gate that makes a fabricated figure impossible to cite."""
    out = read_one({"found": True, "items": [
        {"id": 0, "quote": "Turnover — FY 2022-23: 9,99,00,000", "raw_figure": "9,99,00,000",
         "unit": "rupees", "year": 2022, "confidence": 0.99}]})
    assert not out.ok
    assert any("not on page 15" in r for r in out.refusals)


def test_a_figure_absent_from_the_models_own_quote_is_refused():
    out = read_one({"found": True, "items": [
        {"id": 0, "quote": "Turnover — FY 2022-23", "raw_figure": "812", "unit": "lakh",
         "year": 2022, "confidence": 0.9}]})
    assert not out.ok
    assert any("does not appear in the quoted text" in r for r in out.refusals)


def test_evidence_that_was_never_supplied_is_refused():
    """A page number is never asked for, so one can never be invented: the page comes from the span."""
    out = read_one({"found": True, "items": [
        {"id": 7, "quote": "anything", "raw_figure": "512", "unit": "lakh", "confidence": 0.9}]})
    assert not out.ok
    assert any("was not supplied" in r for r in out.refusals)


def test_an_unconfident_reading_is_refused():
    out = read_one({"found": True, "items": [
        {"id": 0, "quote": "FY 2022-23: 512", "raw_figure": "512", "unit": "lakh",
         "year": 2022, "confidence": 0.3}]})
    assert not out.ok and any("confidence" in r for r in out.refusals)


def test_an_invented_unit_is_refused():
    out = read_one({"found": True, "items": [
        {"id": 0, "quote": "FY 2022-23: 512", "raw_figure": "512", "unit": "zillion",
         "year": 2022, "confidence": 0.9}]})
    assert not out.ok and any("is not one of" in r for r in out.refusals)


def test_a_count_is_never_scaled_by_a_unit():
    """A roster of people read with unit "lakh" would report forty-five lakh employees."""
    out = llm_verdict.read(
        STAFF_SPEC, concepts.STAFF_STRENGTH, [claim(ROSTER, page=40)],
        "an on-roll strength of at least 30 IT professionals",
        Replies({"found": True, "items": [
            {"id": 0, "quote": "45+ qualified engineers are on our payroll", "raw_figure": "45",
             "unit": "lakh", "confidence": 0.95}]}))
    assert out.ok and out.readings[0].value == 45.0


def test_the_reading_stage_being_down_is_not_a_shortfall():
    out = llm_verdict.read(TURNOVER_SPEC, concepts.TURNOVER, [claim(ANNEXURE)], REQUIREMENT,
                           Replies(LLMError("503 from provider")))
    assert not out.ok and any("unavailable" in r for r in out.refusals)


def test_a_model_finding_nothing_says_so():
    out = read_one({"found": False, "items": [], "reasoning": "no turnover stated"})
    assert not out.ok and not out.refusals


# ----------------------------------------------------------------------------------------------
# What the verdict does with it
# ----------------------------------------------------------------------------------------------

def test_a_deterministic_figure_never_reaches_the_model():
    """Ten to fifteen bidders on one tender: the model is for what deterministic reading cannot
    reach, never for a question already answered."""
    spans = [Claim("avg_turnover", "₹4,60,00,000", 15, ANNEXURE, "Annexure-A", 46_000_000.0)]
    model = Replies(AssertionError("the model must not be consulted"))
    assert evaluate_criterion(TURNOVER_SPEC, spans, REQUIREMENT, llm=model).verdict == "pass"
    assert model.calls == 0


def test_a_figure_read_and_short_is_not_matched_not_unknown():
    spans = [Claim("avg_turnover", "₹1,90,00,000", 15, ANNEXURE, "Annexure-A", 19_000_000.0)]
    r = evaluate_criterion(TURNOVER_SPEC, spans, REQUIREMENT, llm=None)
    assert r.verdict == "not_matched" and "below the required" in r.reason


def test_a_shortfall_the_second_model_disagrees_with_becomes_unknown():
    """A bidder wrongly disqualified is the worst output this system can produce, so a shortfall a
    model read has to survive a stronger model before it is shown as one."""
    short = {"found": True, "items": [{"id": 0, "quote": "FY 2022-23: 512", "raw_figure": "512",
                                       "unit": "rupees", "year": 2022, "confidence": 0.9}]}
    qualifying = {"found": True, "items": [{"id": 0, "quote": "FY 2022-23: 512", "raw_figure": "512",
                                            "unit": "lakh", "year": 2022, "confidence": 0.95}]}
    r = evaluate_criterion(TURNOVER_SPEC, [claim(ANNEXURE)], REQUIREMENT,
                           llm=Replies(short, qualifying))
    assert r.verdict == "unknown" and r.unknown_kind == "conflict"
    assert "disagree" in r.reason


def test_a_roster_that_was_counted_outranks_a_reading_that_fell_short():
    """The rows actually counted are a floor: a shortfall below it is an artefact of the reading."""
    spans = [Claim("staff_strength", "33 named personnel", 40,
                   "Numbered roster of personnel, serials 1-58 (33 rows read) on pages 40-42",
                   "Roster", 33.0, detail={"floor": 33, "source": "roster"})]
    r = evaluate_criterion(STAFF_SPEC, spans, "at least 30 IT professionals on roll", llm=None)
    assert r.verdict == "pass"


@pytest.mark.parametrize("requirement,want", [
    ("an average annual turnover of at least INR 4 Cr over the last three years", "average"),
    ("a positive net worth in each of the last three years", "each"),
    ("the highest annual turnover in any of the last five years", "best"),
])
def test_how_a_series_is_measured_comes_from_the_requirements_own_words(requirement, want):
    assert llm_verdict.aggregation(CriterionSpec("k", "l", ">=", {}), requirement) == want
