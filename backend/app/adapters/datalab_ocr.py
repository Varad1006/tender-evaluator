"""Datalab /convert adapter (Chandra OCR) — the remote tier of the hybrid extraction pipeline.

Only pages the local extractors cannot read are sent here (see services/ocr_router.py).
Measured on the PMC corpus (Sep 2026):

  mode       engine        printed / legacy-font   phone scans, handwriting        cost/page   latency/request
  fast       Datalab fast  exact                   numbers exact, words misread     ~0.4¢       ~13–25 s
  balanced   Chandra       exact                   exact (incl. <del>/<i> marks)    ~0.4–2¢     ~10–50 s
  accurate   Chandra       exact                   identical to balanced            ~1¢         ~26 s

Latency is dominated by per-request overhead, not page count (10 pages ≈ 25 s), so pages are
batched per document and requests run concurrently under the plan's limits. Results are cached
per (file hash, page, mode) on disk: the same general-conditions PDF ships with many tenders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)

CONVERT_URL = "https://www.datalab.to/api/v1/convert"
PAGE_SPLIT = re.compile(r"\n*\{(\d+)\}-{10,}\n*")


class OcrError(Exception):
    pass


@dataclass
class OcrJob:
    path: Path
    file_hash: str
    pages: list[int]  # 1-based
    mode: str  # fast | balanced | accurate


@dataclass
class OcrPage:
    page: int  # 1-based
    markdown: str
    mode: str
    cached: bool = False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_range(pages: list[int]) -> str:
    """1-based pages -> Datalab's 0-based page_range, compressed: [4,5,6,9] -> '3-5,8'."""
    zero = sorted(p - 1 for p in pages)
    parts, start = [], zero[0]
    for a, b in zip(zero, zero[1:] + [None]):
        if b != a + 1:
            parts.append(f"{start}-{a}" if a != start else str(a))
            start = b
    return ",".join(parts)


def split_pages(markdown: str) -> dict[int, str]:
    """Paginated markdown ('{3}------') -> {1-based page: markdown}."""
    out: dict[int, str] = {}
    pieces = PAGE_SPLIT.split(markdown)
    # split() yields [preamble, idx, body, idx, body, ...]
    for i in range(1, len(pieces) - 1, 2):
        out[int(pieces[i]) + 1] = pieces[i + 1].strip()
    return out


class DatalabOcr:
    def __init__(self, api_key: str, cache_dir: Path, max_concurrency: int = 4, requests_per_minute: int = 10,
                 poll_seconds: float = 2.0, timeout_seconds: float = 300.0, client: httpx.Client | None = None,
                 sleep=time.sleep) -> None:
        self._key = api_key
        self._cache = cache_dir
        self._client = client or httpx.Client(timeout=120)
        self._pool = max_concurrency
        self._poll = poll_seconds
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._min_interval = 60.0 / max(1, requests_per_minute)
        self._rate_lock = threading.Lock()
        self._last_submit = 0.0

    # --- cache ---------------------------------------------------------------------------
    def _cache_file(self, file_hash: str, page: int, mode: str) -> Path:
        return self._cache / file_hash[:2] / file_hash / f"{mode}-p{page}.md"

    def cached(self, file_hash: str, page: int, modes: tuple[str, ...]) -> OcrPage | None:
        for mode in modes:
            f = self._cache_file(file_hash, page, mode)
            if f.exists() and f.stat().st_size > 0:  # an empty entry (older runs cached failures) is a miss
                return OcrPage(page, f.read_text(), mode, cached=True)
        return None

    def _store(self, file_hash: str, page: OcrPage) -> None:
        f = self._cache_file(file_hash, page.page, page.mode)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(page.markdown)

    # --- remote ----------------------------------------------------------------------------
    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self._last_submit + self._min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last_submit = time.monotonic()

    def _submit(self, job: OcrJob) -> str:
        data = {"mode": job.mode, "output_format": "markdown", "page_range": _to_range(job.pages),
                "paginate": "true", "disable_image_extraction": "true", "disable_image_captions": "true"}
        for attempt in range(4):
            self._throttle()
            with open(job.path, "rb") as fh:
                resp = self._client.post(CONVERT_URL, headers={"X-API-Key": self._key},
                                         files={"file": (job.path.name, fh, "application/pdf")}, data=data)
            if resp.status_code == 429:  # rate limited: back off (docs recommend ~60 s)
                self._sleep(min(60, 10 * 2 ** attempt))
                continue
            if resp.status_code >= 400:
                raise OcrError(f"Datalab rejected {job.path.name}: HTTP {resp.status_code} {resp.text[:200]}")
            body = resp.json()
            if not body.get("success", True):  # page-concurrency limit is reported this way, not as HTTP
                self._sleep(min(60, 10 * 2 ** attempt))
                continue
            return body["request_check_url"]
        raise OcrError(f"Datalab kept rejecting {job.path.name} (rate/concurrency limit).")

    def _await(self, url: str) -> dict:
        started, delay = time.monotonic(), self._poll
        while time.monotonic() - started < self._timeout:
            self._sleep(delay)
            body = self._client.get(url, headers={"X-API-Key": self._key}).json()
            if body.get("status") != "processing":
                if not body.get("success", False):
                    raise OcrError(body.get("error") or "OCR failed")
                return body
            delay = min(delay * 1.5, 10.0)
        raise OcrError("OCR timed out")

    def run_job(self, job: OcrJob) -> list[OcrPage]:
        body = self._await(self._submit(job))
        pages = split_pages(body.get("markdown") or "")
        if not pages and len(job.pages) == 1:
            pages = {job.pages[0]: (body.get("markdown") or "").strip()}
        out = []
        for p in job.pages:
            page = OcrPage(p, pages.get(p, ""), job.mode)
            if page.markdown.strip():  # an empty result may be transient: never freeze it in the cache
                self._store(job.file_hash, page)
            out.append(page)
        return out

    def run(self, jobs: list[OcrJob], on_done=None) -> tuple[list[tuple[OcrJob, list[OcrPage]]], list[str]]:
        """Runs jobs concurrently. on_done(job, pages) is called as each finishes (progress)."""
        results, errors = [], []
        lock = threading.Lock()

        def work(job: OcrJob):
            try:
                pages = self.run_job(job)
            except (OcrError, httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
                log.warning("OCR job failed for %s pages %s: %s", job.path.name, job.pages, exc)
                with lock:
                    errors.append(f"{job.path.name} p.{_to_range(job.pages)}: {exc}")
                return
            with lock:
                results.append((job, pages))
            if on_done:
                on_done(job, pages)

        with ThreadPoolExecutor(max_workers=self._pool) as ex:
            list(ex.map(work, jobs))
        return results, errors


def get_ocr() -> DatalabOcr | None:
    if not settings.DATALAB_API_KEY or not settings.OCR_ENABLED:
        return None
    return DatalabOcr(settings.DATALAB_API_KEY, settings.OCR_CACHE_DIR,
                      max_concurrency=settings.OCR_MAX_CONCURRENCY,
                      requests_per_minute=settings.OCR_REQUESTS_PER_MINUTE)
