import json

import httpx
import pytest

from app.adapters.llm import GeminiAdapter, LLMError
from app.adapters.pdf_parser import parse_pdf
from app.services.criteria_extraction import extract_tender, llm_supplement


def gemini_reply(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


def adapter(handler, sleeps):
    return GeminiAdapter("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)


def test_malformed_output_retried_once_with_stricter_prompt():
    prompts = []

    def handler(request):
        prompts.append(json.loads(request.content)["contents"][0]["parts"][0]["text"])
        assert request.headers["x-goog-api-key"] == "test-key"
        return gemini_reply("not json" if len(prompts) == 1 else '{"ok": true}')

    assert adapter(handler, []).generate_json("judge", "Q") == {"ok": True}
    assert prompts[1].endswith("No prose, no markdown fences.")


def test_timeouts_back_off_then_raise():
    sleeps = []

    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError):
        adapter(handler, sleeps).generate_json("judge", "Q")
    assert sleeps == [2, 4]


class ScriptedLLM:
    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, role, prompt):
        return self.payload


def test_llm_criteria_are_only_accepted_with_verifiable_quotes(seed_dir):
    doc = parse_pdf(seed_dir / "PMC_SolarRooftop_Tender_2026.pdf")
    existing = extract_tender(doc).criteria
    llm = ScriptedLLM([
        {"label": "Module rating", "bucket": "Technical", "datatype": "number", "operator": ">=",
         "threshold_value": 540, "threshold_unit": "Wp", "search_terms": ["Wp"], "page": 18,
         "quote": "PV modules shall be mono-PERC or TOPCon, of minimum 540 Wp rating, conforming to IEC 61215 and IEC 61730."},
        {"label": "ISO 9001", "bucket": "Eligibility", "datatype": "boolean", "operator": "exists",
         "page": 9, "quote": "Bidders shall hold ISO 9001 certification."},  # fabricated
    ])
    drafts, warnings = llm_supplement(llm, doc, existing)
    assert [d.label for d in drafts] == ["Module rating"]
    assert drafts[0].extraction_method == "llm" and drafts[0].source.page == 18
    assert "discarded" in warnings[0]
