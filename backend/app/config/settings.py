"""System configuration (FSD section 4, PoC subset). Values come from the environment, not code.

Secrets (DATALAB_API_KEY, GEMINI_API_KEY) are read from the process environment or from
backend/.env, which is git-ignored.
"""

from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """As python-dotenv does: a later line overrides an earlier one (so a key is updated by appending
    it), and a variable already set in the process environment wins over the file."""
    if not path.exists():
        return
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        os.environ.setdefault(key, value)


if not os.environ.get("TE_SKIP_DOTENV"):
    _load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


DATA_DIR = Path(os.environ.get("TE_DATA_DIR", BACKEND_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
SAMPLE_DIR = BACKEND_DIR / "seed" / "pdfs"
DATABASE_URL = os.environ.get("TE_DATABASE_URL", f"sqlite:///{DATA_DIR / 'poc.db'}")

MAX_FILE_SIZE_MB = int(os.environ.get("TE_MAX_FILE_SIZE_MB", "200"))
MAX_BIDDERS_SOFT_LIMIT = int(os.environ.get("TE_MAX_BIDDERS_SOFT_LIMIT", "20"))
LLM_EXTRACTION_TIMEOUT_SECS = int(os.environ.get("TE_LLM_EXTRACTION_TIMEOUT_SECS", "45"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# --- OCR tier (Datalab / Chandra) -------------------------------------------------------
DATALAB_API_KEY = os.environ.get("DATALAB_API_KEY", "")
OCR_ENABLED = _bool("TE_OCR_ENABLED", True)
OCR_CACHE_DIR = Path(os.environ.get("TE_OCR_CACHE_DIR", DATA_DIR / "ocr_cache"))
OCR_MAX_CONCURRENCY = int(os.environ.get("TE_OCR_MAX_CONCURRENCY", "4"))  # base plan allows 5 in flight
OCR_REQUESTS_PER_MINUTE = int(os.environ.get("TE_OCR_REQUESTS_PER_MINUTE", "10"))
OCR_BATCH_PAGES = int(os.environ.get("TE_OCR_BATCH_PAGES", "10"))
OCR_MAX_PAGES_PER_TENDER = int(os.environ.get("TE_OCR_MAX_PAGES_PER_TENDER", "150"))
# Bids are mostly one merged scan of every document (91–241 pages in the Warje set, 93% image pages),
# and evidence can sit on any page, so the cap is per bidder and higher. ~0.4¢ a page, cached.
OCR_MAX_PAGES_PER_BIDDER = int(os.environ.get("TE_OCR_MAX_PAGES_PER_BIDDER", "300"))
OCR_MODE_SCANNED = os.environ.get("TE_OCR_MODE_SCANNED", "balanced")  # Chandra: phone scans, handwriting
OCR_MODE_VECTOR = os.environ.get("TE_OCR_MODE_VECTOR", "fast")  # legacy fonts / broken text layers
