"""Gemini adapter (optional). Used only for LLM-assisted extraction and qualitative judgement.

Arithmetic comparisons never reach this module (FSD 7.3, NFR3). When GEMINI_API_KEY is unset,
`get_llm()` returns None and every caller degrades to deterministic/rule-based behaviour.

Error handling follows the FSD Adapter Error Matrices:
  * Timeout          -> 2 retries with exponential backoff (2s, 4s), then LLMError
  * MalformedOutput  -> 1 retry with a stricter instruction, then LLMError
Callers turn LLMError into an `unknown` verdict or a skipped criterion, never a crash.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import httpx

from app.config import settings
from app.config.models import get_model

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
STRICT_SUFFIX = "\n\nReturn ONLY a single valid JSON value. No prose, no markdown fences."


class LLMError(Exception):
    pass


class GeminiAdapter:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        timeout: float = settings.LLM_EXTRACTION_TIMEOUT_SECS,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep

    def _call(self, model: str, prompt: str) -> str:
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        backoffs = [2, 4]
        for attempt in range(len(backoffs) + 1):
            try:
                resp = self._client.post(
                    f"{GEMINI_BASE}/{model}:generateContent",
                    json=body,
                    headers={"x-goog-api-key": self._api_key},
                )
                resp.raise_for_status()
                data = resp.json()
                return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == len(backoffs):
                    raise LLMError(f"Timeout calling {model}: {exc}") from exc
                self._sleep(backoffs[attempt])
            except (httpx.HTTPStatusError, KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"Bad response from {model}: {exc}") from exc
        raise LLMError("unreachable")

    def generate_json(self, role: str, prompt: str) -> Any:
        model = get_model(role)
        text = self._call(model, prompt)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("Malformed JSON from %s, retrying with stricter prompt", model)
        text = self._call(model, prompt + STRICT_SUFFIX)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Malformed output from {model} after retry") from exc


def get_llm() -> GeminiAdapter | None:
    if not settings.GEMINI_API_KEY:
        return None
    return GeminiAdapter(settings.GEMINI_API_KEY)
