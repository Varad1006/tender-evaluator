"""OpenRouter adapter for the reading stage, with a cache, a cost meter and a spend ceiling.

Interchangeable with `GeminiAdapter`: same `generate_json(role, prompt)`, same `LLMError`, so every
caller degrades to deterministic behaviour when no key is configured.

Three things this adapter does that the Gemini one does not, all of them because a tender is
evaluated against ten to fifteen bidders at once:

  * **Caches on disk.** A prompt is keyed by (model, prompt) and its reply kept, so re-running an
    evaluation after a code change costs nothing and a committee can reopen a result for free. The
    reading stage is the only billed part of the pipeline besides OCR.
  * **Meters spend.** OpenRouter returns the actual charge for a call when asked, so the cost is
    recorded rather than estimated from a price table that would go stale.
  * **Stops at a ceiling.** `TE_LLM_MAX_SPEND_USD` caps one run. On reaching it the adapter raises
    `LLMError` like any other failure, and the criteria that had not been read yet come back
    `unknown` instead of the run silently costing more than the committee agreed to.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from app.adapters.llm import LLMError, STRICT_SUFFIX
from app.config import settings
from app.config.models import get_model

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class Spend:
    """What the reading stage cost, per model, for one process."""

    calls: int = 0
    cached: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)

    def record(self, model: str, usage: dict) -> None:
        self.calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        cost = float(usage.get("cost") or 0.0)
        self.usd += cost
        self.by_model[model] = self.by_model.get(model, 0.0) + cost

    def as_dict(self) -> dict:
        return {"calls": self.calls, "cached": self.cached, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "usd": round(self.usd, 6),
                "by_model": {m: round(v, 6) for m, v in self.by_model.items()}}


class OpenRouterAdapter:
    provider = "openrouter"

    def __init__(
        self,
        api_key: str,
        timeout: float = settings.LLM_EXTRACTION_TIMEOUT_SECS,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache_dir: Path | None = None,
        max_spend_usd: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep
        self._cache = cache_dir if cache_dir is not None else settings.DATA_DIR / "llm_cache"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._max_spend = settings.LLM_MAX_SPEND_USD if max_spend_usd is None else max_spend_usd
        self._lock = threading.Lock()
        self.spend = Spend()
        self.last_model = ""

    # -- cache ---------------------------------------------------------------------------
    def _key(self, model: str, prompt: str) -> Path:
        digest = hashlib.sha256(f"{model}\0{prompt}".encode()).hexdigest()
        return self._cache / f"{digest}.json"

    def _cached(self, model: str, prompt: str) -> str | None:
        path = self._key(model, prompt)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())["text"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _store(self, model: str, prompt: str, text: str) -> None:
        try:
            self._key(model, prompt).write_text(json.dumps({"model": model, "text": text}))
        except OSError as exc:  # a full disk must not fail an evaluation
            log.warning("Could not cache LLM reply: %s", exc)

    # -- call ----------------------------------------------------------------------------
    def _call(self, model: str, prompt: str, max_tokens: int | None = None) -> str:
        hit = self._cached(model, prompt)
        if hit is not None:
            with self._lock:
                self.spend.cached += 1
            return hit
        with self._lock:
            if self._max_spend and self.spend.usd >= self._max_spend:
                raise LLMError(f"Spend ceiling reached (${self.spend.usd:.4f} of ${self._max_spend:.2f})")
        # `max_tokens` is always sent. Without it OpenRouter reserves the model's full output
        # window against the account balance and refuses the request outright — "You requested up
        # to 65535 tokens, but can only afford 276" — so an unbounded call fails on a low balance
        # even though the reply would have cost a fraction of a cent. It also bounds what a run can
        # spend when a model decides to be talkative.
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }
        headers = {"Authorization": f"Bearer {self._api_key}",
                   "HTTP-Referer": "https://github.com/starkdigitalmediaservices/Tender_Evaluator",
                   "X-Title": "Tender Evaluator"}
        backoffs = [2, 4]
        for attempt in range(len(backoffs) + 1):
            try:
                resp = self._client.post(API_URL, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data and not data.get("choices"):
                    raise LLMError(f"{model}: {data['error']}")
                text = data["choices"][0]["message"]["content"] or ""
                with self._lock:
                    self.spend.record(model, data.get("usage") or {})
                self._store(model, prompt, text)
                return text
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == len(backoffs):
                    raise LLMError(f"Timeout calling {model}: {exc}") from exc
                self._sleep(backoffs[attempt])
            except httpx.HTTPStatusError as exc:
                # 429 and 5xx are worth another try; a 4xx is our own bad request.
                if exc.response.status_code in (429, 500, 502, 503, 529) and attempt < len(backoffs):
                    self._sleep(backoffs[attempt])
                    continue
                raise LLMError(f"Bad response from {model}: {exc}") from exc
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"Unexpected reply from {model}: {exc}") from exc
        raise LLMError("unreachable")

    def generate_json(self, role: str, prompt: str, max_tokens: int | None = None) -> Any:
        model = get_model(role)
        self.last_model = model
        text = self._call(model, prompt, max_tokens)
        for candidate in (text, _unfence(text)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        log.warning("Malformed JSON from %s, retrying with a stricter instruction", model)
        text = self._call(model, prompt + STRICT_SUFFIX, max_tokens)
        try:
            return json.loads(_unfence(text))
        except json.JSONDecodeError as exc:
            raise LLMError(f"Malformed output from {model} after retry") from exc


def _unfence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def get_openrouter() -> OpenRouterAdapter | None:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return OpenRouterAdapter(key) if key else None
