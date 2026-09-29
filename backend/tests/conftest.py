import os
import tempfile
from pathlib import Path

# Isolate test data before the app (and its settings) are imported.
_TMP = tempfile.mkdtemp(prefix="te-poc-tests-")
os.environ["TE_DATA_DIR"] = _TMP
os.environ["TE_SKIP_DOTENV"] = "1"  # tests never read local secrets or call remote OCR
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("DATALAB_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "seed" / "pdfs"


def _ensure_seed() -> None:
    if not (SEED / "PMC_SolarRooftop_Tender_2026.pdf").exists():
        from seed.generate_pdfs import main

        main()


@pytest.fixture(scope="session")
def seed_dir() -> Path:
    _ensure_seed()
    return SEED


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
