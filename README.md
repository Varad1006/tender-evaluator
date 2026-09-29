# Tender Evaluator: PoC

A working proof of concept for the PMC-side Tender Evaluator. It proves one thing: an evaluator can click a
pass/fail verdict and see **the exact tender requirement and the exact bidder evidence that produced it, side by
side, with page citations**.

Scope follows the *Tender Evaluator: PoC Build Brief*: three screens, one seeded tender, 2–3 bidders. The PRD and
FSD (`FSD_TenderEvaluatorPMC_20260917.md`) remain the source of truth for everything the Brief doesn't narrow.

```
Upload tender PDF → Criteria grid → Upload 2–3 bidder PDFs → Verdict grid → click a verdict → Dual-citation popover
                                                                        └→ Comparison matrix + L1 ranked shortlist
```

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
# 1. Backend (FastAPI + SQLite), http://127.0.0.1:8010
cd backend
uv sync
uv run python seed/generate_pdfs.py          # (re)builds the seeded demo PDFs in seed/pdfs/
uv run uvicorn app.main:app --port 8010

# 2. Frontend (Vite + React + MUI), http://localhost:5180
cd frontend
npm install
TE_API_URL=http://127.0.0.1:8010 npm run dev -- --port 5180
```

The Vite proxy forwards `/api` to `TE_API_URL` (default `http://127.0.0.1:8000`). Ports are arbitrary; 8010/5180 avoid
clashing with other local apps.

**Reset for a fresh demo:** delete the tender with the icon on the tender list, or stop the backend and delete
`backend/data/` (keep `backend/data/ocr_cache/` to avoid paying for OCR again).

### Real tenders (MahaTender bundles) and OCR

Upload a tender the way MahaTender delivers it — the zip, or the notice + conditions PDFs + BOQ `.xls` together
(several files at once; `.rar` needs `bsdtar`, which macOS ships as `tar`). The pipeline is hybrid:
local extraction first (results in seconds), then Chandra OCR in the background for the pages the local pass
cannot read (scans, garbled OCR layers, legacy-font and Marathi pages). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
and what the corpus taught us in [docs/CORPUS_FINDINGS.md](docs/CORPUS_FINDINGS.md).

OCR uses the Datalab API. Put the key in `backend/.env` (git-ignored):

```
DATALAB_API_KEY=...
```

Without a key everything still works; unread pages are listed and affected criteria show as *derived — confirm*.
Tuning: `TE_OCR_MODE_SCANNED` (default `balanced` = Chandra), `TE_OCR_MODE_VECTOR` (default `fast`),
`TE_OCR_MAX_CONCURRENCY` (4), `TE_OCR_BATCH_PAGES` (10), `TE_OCR_MAX_PAGES_PER_TENDER` (150).

Run the whole corpus from the command line (OCR results are cached per file hash and page):

```bash
uv run python scripts/corpus_report.py "/path/to/Dataset Tenders" --ocr --json report.json
uv run python scripts/score_corpus.py report.json   # accuracy vs tests/data/corpus_truth.json (243 hand-verified checks)
```

### Optional: LLM-assisted extraction (Gemini)

Everything works **without** an LLM. The seeded demo is fully deterministic. To enable the LLM tiers:

```bash
GEMINI_API_KEY=... uv run uvicorn app.main:app --port 8010
```

Model IDs come from the role-based resolver in `backend/app/config/models.py` (`get_model(role)`), which follows the
existing Tender AI `config/models.py` pattern. **Replace the default IDs with the ones the existing system pins**, or
override per role with `TE_MODEL_EXTRACTION_DEFAULT`, `TE_MODEL_JUDGE`, and so on.

With a key set, the LLM is used for exactly three things. The comparison itself is never one of them:
1. **Extra tender criteria** that the deterministic rules don't recognise. Each is accepted only if its quote appears
   verbatim on the page it cites (quote-then-extract); anything else is discarded and counted in a warning.
2. **Finding bidder evidence** for those LLM-found criteria (same verbatim-quote guard).
3. **Judging "similar nature of work"** for the experience criterion, and only *after* value and date have passed in
   code. A confidence below 0.7 gives `unknown`, not a guess.

Timeouts retry twice (2 s, 4 s), and malformed JSON retries once with a stricter prompt. Any remaining failure degrades to
rule-based or `unknown` and never crashes (FSD §7 error matrices).

## The demo (Brief's script, mapped to the app)

| Step | In the app |
|---|---|
| 1. Empty tender list | `/`: "No tenders evaluated yet" |
| 2. Upload tender, grid populates | **Upload Tender PDF** → `backend/seed/pdfs/PMC_SolarRooftop_Tender_2026.pdf` (or "use the seeded sample tender"). 8 criteria, each with its verbatim clause, clause number and page |
| 3. Upload 2–3 bidders | One PDF per slot from `backend/seed/pdfs/` (or "Load the 3 seeded sample bids"). Bidder name and quoted price are read from each PDF |
| 4. Verdict grid mix | 21 pass · 1 fail · 2 unknown |
| 5. Click a verdict | GreenVolt × Turnover → tender cl. 5.1 **p.14** beside the bid's **p.5**, with `₹3,80,00,000 < ₹5,00,00,000` |
| 6. The inconsistency | Urja × Warranty → "10 years" (**p.9**) vs "7 years" (**p.27**) in the same bid: `unknown`, both quotes shown |
| 7. Comparison matrix | Each cell shows the verdict *and* the figure the bidder stated, plus a quoted-price row |
| 8. Ranked shortlist | L1 Sunrise ₹42,50,000 · Urja pending (2 undetermined) · GreenVolt disqualified on turnover, with both citations |

Every `p.N` tag opens the source PDF at that page (`…/file#page=N`).

### Seeded corpus

The Brief suggests a tender from the existing repo's ground-truth corpus (`backend/tests/groundtruth/corpus.json`).
That repo isn't available here, so `seed/generate_pdfs.py` builds a 22-page PMC rooftop-solar tender, based on the
original prototype's scenario, plus three fabricated bids written to force the Brief's outcome mix:

| Bidder | Designed outcome |
|---|---|
| Sunrise Energy Systems | Passes everything. Also lists a second, too-small project that the engine correctly skips |
| GreenVolt Infra | Cheapest bid, but average turnover ₹3.8 Cr < ₹5 Cr gives **fail** → disqualified |
| Urja Solar Tech | Warranty 10 yrs (p.9) vs 7 yrs (p.27) gives **unknown / conflict**. No ESIC registration anywhere gives **unknown / not found** |

To swap in a real corpus tender, upload it. The criteria rules in `criteria_extraction.py` are wording-based, not
tied to this document, but expect to tune them against real layouts (the Brief's "extraction reliability" risk).

## How it works

```
backend/app/
  adapters/pdf_parser.py        pdfplumber → sentences with page, section heading, clause no. (headers/footers dropped)
  adapters/llm.py               optional Gemini adapter (retry/backoff, JSON repair), off without a key
  config/models.py              get_model(role): single source of model IDs
  services/criteria_extraction  tender → criteria registry rows (deterministic rules + optional verified LLM pass)
  services/bidder_extraction    bid → every candidate claim per criterion, each with page + verbatim quote
  services/evaluation.py        verdict engine: arithmetic in code; unknown ≠ fail; conflicts keep both citations
  services/ranking.py           L1 shortlist (QCBS/unknown → explicit warning, never a silent guess)
  services/pipeline.py          storage (per-bidder path isolation guard), background jobs, read models
  api/routes.py                 /api/v1 REST, {success, data, message} envelope (FSD §8)
frontend/src/
  theme.ts                      the only styling source: MUI theme + tokens, `sx` everywhere, no inline style={{}}
  components/DualCitationPopover.tsx   the demo moment
  components/VerdictMatrix.tsx  @mui/x-data-grid bidder × criterion matrix (verdict and comparison modes)
  pages/                        TenderList → Criteria → Bidders → Verdicts → Comparison
```

**Data model:** the Brief's six tables (`eval_dg_tenders`, `_criteria_registry`, `_bidders`, `_bidder_documents`,
`_bidder_extracted_values`, `_bidder_criterion_verdicts`). Each verdict row stores the requirement citation (tender page and
quote) and the evidence citation (document, page and quote). `evidence_json` keeps *every* statement, so a contradiction is
never collapsed to one side. Comparison and ranking are recomputed live from the verdict table.

**Verdict rules**

| Situation | Verdict |
|---|---|
| Threshold met or missed (turnover, warranty, days) | `pass` / `fail`, computed in code, shown as e.g. `12 years ≥ 10 years` |
| Required document present | `pass`, with the sentence that proves it |
| No evidence anywhere in the bid | `unknown` · *not found*, never `fail` |
| Bid states two different figures | `unknown` · *conflict*, both citations shown |
| Similar-nature judgement not confident | `unknown` · *needs evaluator judgement* |
| Any gating `fail` | bidder **disqualified**. Every other criterion is still evaluated and shown |

## Tests

```bash
cd backend && uv run pytest        # 57 tests; corpus golden + accuracy tests run when ~/Desktop/Dataset Tenders exists (or TE_CORPUS_DIR), replaying the OCR cache offline
cd frontend && npm run build       # type-check + production build
```

`tests/test_golden.py` runs the demo script end to end through the API against the seeded PDFs: criteria pages and
thresholds, the full 24-cell verdict matrix, 100% dual-citation coverage on pass/fail verdicts (the FSD's success
metric), contradiction handling, and the ranking. `test_evaluation.py` checks the rules above, including that
deterministic criteria never call the LLM and that the LLM judge runs only after the arithmetic gates.
`test_llm_adapter.py` checks retries, backoff, and rejection of LLM quotes that aren't on the cited page.

## Deliberately not built (Brief's "Explicitly Cut" list)

RBAC and multi-evaluator sign-off (one demo user), consistency-flag detection, lifecycle enforcement (the status is a
label: *Draft* → *Under Evaluation* on first bid), audit trail, exports, multi-document bidders (one combined PDF
each), consortium/JV, cross-tender intelligence, EN/MR localisation. Also not built: OCR for scanned PDFs, which are
rejected with a clear message, and QCBS scoring, where a QCBS tender shows a warning and a price-ordered list.

## Deviations from the FSD to note

- `POST /api/v1/tenders` takes the PDF directly and creates the tender from it. The FSD's separate create, upload
  and extract calls are merged because title and reference number come *from* the document.
- Seed PDFs write amounts as "Rs." (as real tenders do). The UI displays ₹, while quotes stay verbatim.
