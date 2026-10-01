from datetime import date

import pytest

from app.adapters.llm import LLMError
from app.services.bidder_extraction import Claim, CriterionSpec
from app.services.evaluation import evaluate_criterion, overall_status


class ExplodingLLM:
    def generate_json(self, role, prompt):
        raise AssertionError("LLM must not be called for deterministic criteria")


class FakeJudge:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def generate_json(self, role, prompt):
        self.calls += 1
        assert role == "judge"
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def claim(key, value, numeric=None, page=1, **detail):
    return Claim(criteria_key=key, value=value, page=page, quote=f"quote p{page}", section="S", value_numeric=numeric, detail=detail)


TURNOVER = CriterionSpec("avg_turnover", "Turnover", ">=", {}, 50_000_000, "INR")
WARRANTY = CriterionSpec("warranty_years", "Warranty", ">=", {}, 10, "years")
LABOUR = CriterionSpec("epf_esic_registration", "EPF / ESIC", "exists_all", {"parts": ["EPF", "ESIC"]})
EXPERIENCE = CriterionSpec("similar_experience", "Experience", "similar_project",
                           {"window_years": 5, "nature_terms": ["rooftop", "solar"]}, 20_000_000, "INR")


@pytest.mark.parametrize("spec", [TURNOVER, WARRANTY, LABOUR, EXPERIENCE])
def test_missing_evidence_is_unknown_never_fail(spec):
    r = evaluate_criterion(spec, [], "req", llm=ExplodingLLM())
    assert r.verdict == "unknown" and r.unknown_kind == "not_found"


def test_deterministic_tiers_never_call_llm():
    assert evaluate_criterion(TURNOVER, [claim("avg_turnover", "x", 61_000_000)], "req", llm=ExplodingLLM()).verdict == "pass"
    assert evaluate_criterion(TURNOVER, [claim("avg_turnover", "x", 38_000_000)], "req", llm=ExplodingLLM()).verdict == "not_matched"
    r = evaluate_criterion(WARRANTY, [claim("w", "12", 12)], "req", llm=ExplodingLLM())
    assert r.verdict == "pass" and r.tier == "deterministic" and r.computation == "12 years ≥ 10 years"


def test_contradiction_is_unknown_with_both_citations():
    r = evaluate_criterion(WARRANTY, [claim("w", "10", 10, page=9), claim("w", "7", 7, page=27)], "req")
    assert r.verdict == "unknown" and r.unknown_kind == "conflict"
    assert [c.page for c in r.evidence] == [9, 27]


def test_repeated_same_figure_is_not_a_conflict():
    r = evaluate_criterion(WARRANTY, [claim("w", "10", 10, page=9), claim("w", "10", 10, page=30)], "req")
    assert r.verdict == "pass"


def test_partial_registrations_are_unknown():
    r = evaluate_criterion(LABOUR, [claim("l", "EPF", page=20, part="EPF")], "req")
    assert r.verdict == "unknown" and r.extra["missing_parts"] == ["ESIC"]
    both = [claim("l", "EPF", part="EPF"), claim("l", "ESIC", part="ESIC")]
    assert evaluate_criterion(LABOUR, both, "req").verdict == "pass"


def project(value, year, hits):
    return claim("similar_experience", "p", value, year=year, nature_hits=hits, nature_terms=["rooftop", "solar"])


def test_similar_project_rule_based():
    ref = date(2026, 1, 12)
    assert evaluate_criterion(EXPERIENCE, [project(23_000_000, 2023, ["rooftop", "solar"])], "req", ref).verdict == "pass"
    too_small = evaluate_criterion(EXPERIENCE, [project(6_800_000, 2023, ["rooftop", "solar"])], "req", ref)
    assert too_small.verdict == "not_matched" and "below" in too_small.reason
    too_old = evaluate_criterion(EXPERIENCE, [project(23_000_000, 2019, ["rooftop", "solar"])], "req", ref)
    assert too_old.verdict == "not_matched" and "window" in too_old.reason
    partial = evaluate_criterion(EXPERIENCE, [project(23_000_000, 2023, ["solar"])], "req", ref)
    assert partial.verdict == "unknown" and partial.unknown_kind == "low_confidence" and partial.tier == "rule_based"


def test_similar_project_llm_judge_only_after_arithmetic_gates():
    ref = date(2026, 1, 12)
    judge = FakeJudge({"similar": True, "confidence": 0.9, "rationale": "Same nature."})
    r = evaluate_criterion(EXPERIENCE, [project(23_000_000, 2023, [])], "req", ref, llm=judge)
    assert r.verdict == "pass" and r.tier == "ai_assisted" and judge.calls == 1
    judge = FakeJudge({"similar": True, "confidence": 0.9})
    evaluate_criterion(EXPERIENCE, [project(1_000_000, 2023, [])], "req", ref, llm=judge)
    assert judge.calls == 0  # value gate failed; no judgement spent
    low = evaluate_criterion(EXPERIENCE, [project(23_000_000, 2023, [])], "req", ref,
                             llm=FakeJudge({"similar": True, "confidence": 0.4}))
    assert low.verdict == "unknown" and low.unknown_kind == "low_confidence"
    down = evaluate_criterion(EXPERIENCE, [project(23_000_000, 2023, ["rooftop", "solar"])], "req", ref,
                              llm=FakeJudge(LLMError("timeout")))
    assert down.verdict == "pass" and down.tier == "rule_based"  # degrades, never crashes


def test_overall_status():
    assert overall_status([("pass", True), ("unknown", True)]) == "pending"
    assert overall_status([("fail", True), ("unknown", True)]) == "disqualified"
    assert overall_status([("not_matched", True), ("pass", True)]) == "disqualified"
    assert overall_status([("fail", False), ("pass", True)]) == "eligible"
