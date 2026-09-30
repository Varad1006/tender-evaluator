# Tender document processing — hybrid architecture

Goal: fetch the right requirements from real MahaTender / PMC tender bundles, fast, with a page
citation for every requirement, without sending everything to a paid OCR service.

## Pipeline

```
upload (zip / rar / pdf / xls, several at once)
   │
   ▼
0. UNPACK            bundle.expand ─ recursive zip/rar (bsdtar), zip-slip guard, skips __MACOSX/dotfiles,
   │                 keeps an origin trail: "work_2291063.zip › Tenderdocuments.rar › DATASHEET.pdf"
   ▼
1. LOCAL TIER (seconds, free, parallel per document)
   ├─ quick scan      pdfium, every page (~2 ms/page): text + readability class + "is this a scan?"
   ├─ classify doc    notice / conditions / datasheet / rfp / boq / specifications / approval
   ├─ structure       pdfplumber tables + headings + sentences, ONLY readable pages of
   │                  eligibility-bearing documents (specs, SOPs, QAPs, BOQs are skipped)
   └─ BOQ .xls        xlrd: estimated amount, contract no., Percentage / Item-rate
   │
   ▼
2. EXTRACT            facts → PMC standard criteria → indexation + experience ladder →
   │                  sentence rules → checklists → award
   │                  ► persisted immediately; the UI shows usable criteria within seconds
   │                  ► the ladder and the indexation table are read across sentences and pages,
   │                    because neither fits in the one sentence a rule sees
   ▼
3. OCR TIER (background, only what the local tier could not read)
   ├─ router          pages classed image / garbled / lossy-Marathi, in eligibility documents,
   │                  priority datasheet > notice > conditions > rfp > other, capped per tender
   ├─ cache           (file sha256, page, mode) on disk — a shared general-conditions PDF is OCR'd once
   ├─ Datalab /convert  batches of ≤10 pages (page_range), 4 requests in flight, rate-limited,
   │                  429/limit back-off, polling with back-off, per-job failure isolation
   └─ merge           Chandra markdown → the same page/sentence/table structures as local pages
   │
   ▼
4. EXTRACT AGAIN      derived criteria become located and quoted; facts gain sources
```

Nothing waits on OCR. If OCR is not configured, fails, or times out, the local result stays and
the unread pages are listed; criteria that should exist but were not found are shown as
**derived — confirm**, never silently dropped.

## Page readability classes

Real bundles contain pages where "has a text layer" is not the same as "readable":

| class | what it is (seen in the corpus) | local tier | OCR route |
|---|---|---|---|
| `english` | genuine English text layer | full parse, quotable | — |
| `marathi` | Unicode Devanagari; pdfminer/pdfium drop conjuncts and split words | cite by Devanagari-digit signatures (४०%, ७५), never quote | `fast` (vector) / `balanced` (scan) |
| `garbled` | text exists but is nonsense: bad OCR layer on a scan (TERMS.pdf) or Marathi in a legacy ASCII font (`Q|Sa Jmofdmam` = टेंडर गोषवारा) | ignored | `fast` (vector) / `balanced` (scan) |
| `image` | no text layer at all | ignored | `balanced` |
| `blank` | scanned backside / separator sheet: < 0.3 % ink (letterhead show-through, punch holes) | ignored | **never** — Chandra invented text on one ("पुस्तक का विवरण" × 10) |
| `ocr_en` / `ocr_mr` | after OCR | full parse, quotable (Marathi quoted as Marathi) | — |

## OCR mode choice (measured on the corpus, Sep 2026)

| mode | printed / legacy font | phone scans, handwriting | cost / page | latency per request |
|---|---|---|---|---|
| `fast` | exact | numbers exact, words misread ("mainline" for "manhole", मंगील for मागील) | ~0.4¢ | 13–25 s |
| `balanced` (Chandra) | exact | exact, marks `<del>` struck and `<i>` handwritten text | ~0.4¢ batched (2¢ single page) | 10–50 s |
| `accurate` (Chandra) | exact | identical to balanced | ~1¢ | ~26 s |

Routing: **vector page with a broken or lossy text layer → `fast`; scanned page → `balanced`**.
`accurate` bought nothing on this corpus. Latency is per request, not per page (10 pages ≈ 25 s),
hence batching. Both are configurable (`TE_OCR_MODE_SCANNED`, `TE_OCR_MODE_VECTOR`).

Chandra does OCR only — it does **not translate**. Marathi stays Marathi: rules match Marathi
clauses directly and the English criterion label comes from the PMC standard. A translation layer
(for arbitrary Marathi clauses) would need an LLM and is not part of this build.

## Reconciliation rules (why the extraction can be trusted)

* **Facts come from every structured section** that states them (Tender Notice box, Work Input
  Form, B-1 row, conditions tender table, Tender Abstract total, Marathi Goshwara, BOQ .xls) and are
  marked consistent / single source / conflict.
* **Typed text and spreadsheets outrank OCR** (which includes handwriting); OCR outliers are reported.
* **PMC security deposit**: a 4 % figure and a 5 % figure are consistent (1 % EMD is converted).
* **The tender's own stated threshold governs; the formula is a cross-check.** The engine explains
  mismatches: the "Rs.423647 Lakhs" template unit error, blanks left unfilled, and tenders that compute
  on the GST-inclusive cost instead of the cost put to tender.
* **A corrigendum supersedes.** A short document titled Corrigendum / Addendum / शुद्धिपत्रक is its own
  role; its revised values ("Existing | Revised" tables, "… revised to Rs. …") win, and the fact shows
  "Changed by corrigendum" with the original value noted — never a conflict.
* **A clause's label does not settle which base it used.** "1% of the tender cost (Total Estimated
  Cost)" is in fact 1% of the cost put to tender excluding GST, in the one bundle where both figures
  and the resulting EMD are all printed. Bases are decided by trying both and reporting which one
  the tender's own figure matches, never by the wording.
* **Past money is compared after indexation.** Where the tender prints multiplying factors by
  financial year, turnover, completed-work values and the bid-capacity `A` term are escalated to
  current price level first. The factors are read from the document and applied only if they form
  one geometric progression (`indexation.py`).
* **The options in an experience ladder must divide back to one contract value.** 40 / 50 / 80% of
  the same number, whether the tender prints the percentages or only the amounts. A rung that
  implies a different value was misread and the ladder is reported rather than used.

## Edge cases handled

| where | case | handling |
|---|---|---|
| upload | password-protected, truncated, zero-page PDF | clear per-document error; the rest of the bundle still runs |
| upload | zip bombs, encrypted members, RAR, images, Word/Excel exports, duplicates, `..` paths | see `bundle.py` (refused / converted / read once / warned) |
| pages | blank scans | detected locally (ink ratio), never sent to OCR |
| OCR | repetitive (hallucinated) output | rejected before it reaches extraction |
| OCR | empty cache entries from older runs | treated as a miss, retried |
| OCR | not configured, failed, or over the page cap | the pages left unread are listed by number in the warnings |
| text | numbered list items run together in one sentence ("…below- 1 GST … 2 PF code … 3 …") | each item is its own paragraph and inherits its intro's obligation |
| text | words run together by the text layer ("EARNESTMONEYDEPOSIT", "calendarmonths") | whitespace-tolerant patterns for the key facts |
| tables | a row split across a page break | continuation joined to the previous item |
| tables | a tall cell ruled into one row per visual line, with the next column's lines interleaved between them | a row whose serial-number column is empty is a continuation and is folded into the row above (`_unwrap_rows`) |
| criteria | alternatives ("3 works ≥ 40%, **or** 2 ≥ 50%, **or** 1 ≥ 80%") spread over cells and pages | collected into one criterion; reading a single rung would state the requirement three times too strictly |
| criteria | a personnel clause that stations staff at the site during execution | not an eligibility gate — the qualification table's own row is |
| checklists | data sheets, fill-in financial formats, boxed clauses, tables of contents, chapter headings, clause mentions of "qualification criteria" | not treated as requirement lists |
| checklists | summary tables and submission lists restating a criterion ("Staff", "Documental proof of GSTN") | folded into that criterion as evidence |
| criteria | key-quantity grid printed blank | item names listed; 30 % of each BOQ quantity applies; window (3 or 5 years) read from the clause |

## Accuracy harness

`tests/data/corpus_truth.json` holds values verified by reading the pages (297 checks over 17
tenders: facts, thresholds, similar-work options, key quantities, personnel, required and forbidden
labels). `scripts/score_corpus.py` scores a `corpus_report.py --json` run against it, and
`tests/test_corpus_golden.py::test_accuracy_against_ground_truth_with_cached_ocr` replays the OCR
cache offline and fails on any miss. A tender in the ground truth but not in this machine's corpus
folder is skipped rather than counted as a miss, so the truth can grow ahead of any one download.

Current: **243 / 243** on the original 13 tenders + RFP, and **54 / 54** on the three added in
Sep 2026 (work_2286446, PCMC encroachment RFP, PMC DBT RFP).

The three added tenders score **51 / 54 from the local text layer alone**. The hybrid tier closes
the gap: work_2286446's key BOQ quantities are handwritten on a scanned data-sheet page, and
Chandra reads them exactly ("NP-III RCC Pipe - 600mm | 99 | Rmt"). OCR also recovers that bundle's
Marathi work name, its tender category, contract type B1 and pre-bid flag, and moves the turnover,
similar-works and bid-capacity citations onto the signed qualification sheet they actually come
from. 45 pages, about 70 s, cached thereafter.

`tests/test_phrasing_corpus.py` is the other half of the instrument: how authorities word each
requirement, and — the important half — the wordings that must **not** be read as one. A tender that
exposes a bug gets a row there, not a patch to the reader.

## Throughput on the corpus (13 tenders + 1 RFP, ~1,100 pages)

| | first run | re-run (cache) |
|---|---|---|
| local tier per bundle | 0.1–7.5 s | same |
| OCR per bundle | 14–105 s (3–62 pages) | ~0.1 s |
| criteria located & quoted | local only: 74 / 120 (+17 cited in Marathi, 29 derived) | hybrid: **188 / 188** (139 criteria + 49 documents) |

## Code map

| stage | module |
|---|---|
| unpack, document roles | `app/services/bundle.py` |
| quick scan, structural parse | `app/adapters/pdf_parser.py` (`quick_scan`, `parse_pdf`) |
| readability | `app/adapters/readability.py` |
| BOQ spreadsheet | `app/adapters/boq_xls.py` |
| OCR adapter | `app/adapters/datalab_ocr.py` |
| OCR markdown → pages | `app/adapters/ocr_text.py` |
| router, orchestration | `app/services/tender_processing.py` |
| facts + reconciliation | `app/services/pmc_facts.py` |
| PMC standard criteria | `app/services/pmc_eligibility.py` |
| experience ladder ("3 × 40%, or 2 × 50%, or 1 × 80%") | `app/services/experience_ladder.py` |
| price-level indexation (multiplying factors by FY) | `app/services/indexation.py` |
| checklists | `app/services/checklist_extraction.py` |
| bundle extraction | `app/services/bundle_extraction.py` |
| API job (persist local, then OCR) | `app/services/pipeline.py` (`process_tender`) |
| corpus runner | `scripts/corpus_report.py` |
| accuracy scorer | `scripts/score_corpus.py` + `tests/data/corpus_truth.json` |
