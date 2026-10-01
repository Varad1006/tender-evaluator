# Tender document processing — hybrid architecture

Goal: fetch the right requirements from real MahaTender / PMC tender bundles, fast, with a page
citation for every requirement, without sending everything to a paid OCR service.

## Pipeline

```
upload (zip / rar / pdf / xls, several at once)
   │
   ▼
0. UNPACK            bundle.expand ─ recursive zip/rar (bsdtar), zip-slip guard, skips __MACOSX/dotfiles,
   │                 keeps an origin trail: "work_1234567.zip › Tenderdocuments.rar › DATASHEET.pdf"
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
  mismatches: the template unit error (a rupee figure printed with "Lakhs"), blanks left unfilled, and tenders that compute
  on the GST-inclusive cost instead of the cost put to tender.
* **A corrigendum supersedes.** A short document titled Corrigendum / Addendum / शुद्धिपत्रक is its own
  role; its revised values ("Existing | Revised" tables, "… revised to Rs. …") win, and the fact shows
  "Changed by corrigendum" with the original value noted — never a conflict.
* **A clause's label does not settle which base it used.** A clause naming the "total estimated
  cost" can turn out to mean the cost put to tender excluding GST, as a bundle that prints both
  figures and the resulting EMD shows. Bases are decided by trying both and reporting which one
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

`tests/local/corpus_truth.json` holds values verified by reading the pages (facts, thresholds,
similar-work options, key quantities, personnel, required and forbidden labels). It is read from
real tenders, so it is git-ignored and stays on the machine that has them. `scripts/score_corpus.py`
scores a `corpus_report.py --json` run against it, and
`tests/local/test_corpus_golden.py::test_accuracy_against_ground_truth_with_cached_ocr` replays the
OCR cache offline and fails on any miss. A tender in the ground truth but not in this machine's corpus
folder is skipped rather than counted as a miss, so the truth can grow ahead of any one download.

Current: **378 / 378** checks over 20 tenders (13 PMC works bundles and an IT RFP first, three more
bundles and RFPs in September 2026, three PMC zone bundles in October 2026). Besides values, the scorer
checks that a criterion is located rather than derived, that the escalation rate is carried, and
that a threshold states no number when an input it needs is missing from the bundle.

From the local text layer alone the September additions scored 51 of 54; the hybrid tier closes the
gap. In one drainage bundle the key BOQ quantities are handwritten on a scanned data sheet, and
Chandra reads them exactly; OCR also recovers that bundle's Marathi work name, category, contract
type and pre-bid flag, and moves the financial citations onto the signed sheet they come from.

`tests/test_phrasing_corpus.py` is the other half of the instrument: how authorities word each
requirement, and — the important half — the wordings that must **not** be read as one. A tender that
exposes a bug gets a row there, not a patch to the reader.

## Throughput on the corpus (13 tenders + 1 RFP, ~1,100 pages)

| | first run | re-run (cache) |
|---|---|---|
| local tier per bundle | 0.1–7.5 s | same |
| OCR per bundle | 14–105 s (3–62 pages) | ~0.1 s |
| criteria located & quoted | local only: 74 / 120 (+17 cited in Marathi, 29 derived) | hybrid: **188 / 188** (139 criteria + 49 documents) |

# Bid envelopes — the same hybrid pipeline, read for evidence

A MahaTender bid arrives as `Fee_PreQual_Technical_<bid id>.zip`: the portal's EMD receipt plus the
bidder's documents, usually merged into one scanned PDF of 50–300 pages. On the first ten envelopes
(1,470 pages), **93 % of pages were image scans**, so for bids OCR is the main road, not the fallback.

```
upload envelope(s)   one zip = one bidder (several files per bidder allowed; .pdf/.zip/.rar)
   │                 the bid id is read from the file name
   ▼
1. LOCAL TIER        bundle.expand + local_pass(role="bidder"): same quick scan and readability classes
   ▼
2. PAGE TYPING       22 document types, English and Marathi, decided by the page's heading
   │                 (first ~700 chars) — a passing mention elsewhere does not type a page.
   │                 Sworn statements (हमीपत्र / प्रतिज्ञापत्र / affidavit) are typed first: they
   │                 restate every criterion. A continuation page inherits its predecessor's type.
   ▼
3. OCR TIER          every unreadable page, plus scanned English pages whose amounts are corrupt
   │                 and every turnover / bid-capacity certificate (their figures decide verdicts);
   │                 capped per bid (TE_OCR_MAX_PAGES_PER_BIDDER, 300); pages left unread are listed
   ▼
4. FETCH             each tender requirement → the bid's documents that answer it (below)
   ▼
5. EVIDENCE          one reader per criterion, over the documents fetched for it first
   │                 → claims, each with page + verbatim quote + the document it was read from
   ▼
6. VERDICTS          evaluation.py — arithmetic in code, one handler per PMC criterion
```

## Fetching a bidder's documents by what the tender requires

The tender decides what is fetched from a bid; nothing about any particular bid is known in advance.
`app/services/document_fetch.py` joins the two sides:

```
TENDER criteria ──► 1. REQUIREMENT PLAN      each requirement → the kinds of document that prove it
                       (by criterion; else by what it is about — concepts.resolve; else by the
                        tender's own words for the document)
BID files ────────► 2. INVENTORY             every document in the bid: kind, file, pages, title,
                       (pages typed by heading; a run of one kind is one document unless a page
                        carries its own heading, which starts the next)
                    3. FETCH                 each requirement → its documents, ranked: what states
                       the fact (primary, or a page headed with the document asked for), then what
                       corroborates it; status found / supporting only / not in bid / not a gate
                    4. READ                  evidence is read from the fetched documents first, then
                       the supporting ones, and only then the whole bid — and every figure records
                       which document it came from
```

| requirement | documents fetched (primary · supporting) |
|---|---|
| EMD | EMD payment receipt |
| registration | contractor registration certificate |
| turnover | CA turnover certificate (also a letterhead over a year grid), audited accounts · ITR |
| bid capacity | bid-capacity certificate · turnover certificate, works in hand |
| similar works | work completion certificates, completed-works statement · BOQ certificates, work orders |
| key quantities | BOQ certificates, completion certificates · completed-works statement |
| no joint venture | JV / consortium agreement, constitution, registration, GST · affidavits |
| an RFP's own requirement | by concept (PAN, GST, Udyam, declarations, staff, audited accounts …), else by the page whose heading carries the tender's words for the document |

Three rules keep it honest:

* **Nothing is guessed.** A requirement with no matching document says so ("no turnover certificate
  in this bid"); a figure found elsewhere is marked as found outside the documents the requirement
  asks for.
* **An absence is checked everywhere.** "No joint venture" is answered by what is *not* in the bid,
  so it is always read across the whole bid; its documents are fetched and shown, never used to
  narrow the search. A JV agreement is a document kind of its own, so it can never inherit the type
  of the certificate in front of it.
* **Whose name a document carries is not judged here.** The firm named on a page may be its issuer
  (a chartered accountant), a stamp vendor or the bidder; a warning that is usually wrong would
  mislead. Work certificates issued to another contractor are flagged by `work_proof`, from the
  certificate's own wording.

On ten real bid envelopes every requirement's documents were found (turnover for nine on the first
run; the tenth was a CA letterhead over a bare year grid, now recognised), and the evidence for
EMD, registration, bid capacity, turnover, similar works and key quantities is read from the
fetched documents. The bidder card lists each requirement with its documents, linked to the page,
and every verdict's evidence says which fetched document it was read from.

## Matching a requirement to a bid that words it differently

A tender states a requirement in its own language and a bid answers in the bidder's. The two rarely
share a word, and nothing in a submission is labelled with the tender's wording:

| | |
|---|---|
| tender | "an **on-roll staff strength** of at least 25 **IT professionals**" |
| bid | "we confirm that **40+** qualified engineers are **on our payroll**", over a numbered roster |

(an invented pair with the shape real documents show)

Searching for the requirement's words finds nothing, and the criterion comes back unknown on a
submission that answers it twice over. `app/services/concepts.py` closes that gap. A requirement is
resolved to a **concept**, and the concept carries the vocabulary a bid is known to use, in three
kinds:

* **anchors** name the fact alone — "Net-Worth", "Annual Turnover", "on companies' payroll".
* **supports** are consistent with it but name other things too — "Revenue from Operations" is the
  turnover line of a P&L and also appears in notes and schedules. A support needs corroboration.
* **vetoes** name the fact in text that states no value of it. Every tax invoice carries the
  standard GST declaration about accounting for supplies in turnover; every bid opens with a
  contents list naming each certificate beside a page range. Read as evidence, such a row turns a
  financial year into a net worth of two thousand rupees — cited to a real page, and enough to
  disqualify a company certifying crores. A veto outranks any anchor on the same span.

Finders are dispatched **by concept, not by criterion key**: the keys are generated from the clause
they were read in, so the same requirement arrives as `avg_turnover` on one tender,
`positive_net_worth` on another and `min_staff_strength` on a third. A registry keyed by name finds
a finder for none of them on a tender nobody has seen yet.

### Two languages, on either side

A PMC works tender is written in Marathi and answered by bids whose balance sheets, GST certificates
and work orders are in English; an English RFP draws bids with Marathi completion certificates
stapled into them. Both directions occur, and both have to work.

**The value layer reads Marathi.** `रु.`, `र.रु.` and `रुपये` are the same marker as `Rs.`, and
`कोटी`/`लाख` the same multipliers as crore/lakh. Devanagari digits map one-for-one onto ASCII, so
every scan runs on a folded copy while each quote is sliced out of the original — the committee is
shown `रु. १,२३,४५,६७८/-`, not `1,23,45,678`. Durations are matched by stem because Marathi inflects
the unit rather than adding a word (वर्ष, वर्षे, वर्षात, वर्षातील, वर्षांनंतर are all "years"), and the
bracketed repetition Indian drafting favours — "गेल्या ५ (पाच) वर्षातील", "5 (five) years" — is
allowed between the figure and its unit.

**A Marathi sentence can state an obligation.** Marathi has no modal for "shall": the obligation is a
verb ending — "सादर करणे आवश्यक आहे", "भरावी लागेल", "बंधनकारक". `REQUIRE` matched only English
modals, so every rule refused every Marathi sentence and a wholly Marathi tender produced no criteria
at all. 198 sentences in the works corpus read as obligations once the stems were added.

**The concept is the bridge.** 18 of the 30 concepts now carry Marathi vocabulary alongside English,
so a requirement resolves to the same concept whichever language states it:

| the tender says | the bid says | concept |
|---|---|---|
| पॉवर ऑफ अटर्नी | POWER OF ATTORNEY | `signing_authority` |
| Work completion certificate | कामाचा पूर्णत्वाचा दाखला | `completion_certificate` |
| Audited balance sheet | ताळेबंद व नफा तोटा पत्रक | `audited_accounts` |
| मागील ५ वर्षातील वार्षिक उलाढाल | Annual Turnover 2023-24 | `turnover` |

**The precision gate stands down across alphabets.** The distinctive-term test compares the clause's
own words against the evidence page; across scripts that comparison is meaningless, and applied
anyway it rejects every page and returns `unknown` on a submission that answers the requirement. So
when the requirement and the page are in different alphabets the concept match stands on its own, the
claim records why, and the verdict tells the committee: *"The tender states this requirement in a
different language from the page it was found on; they were matched on what the clause is about, not
on its wording."* Within one alphabet the gate still applies in full — and it now extracts Devanagari
terms too, since reading only Latin words left a Marathi clause with no distinctive terms and skipped
the gate entirely.

**One veto this made necessary.** Every PMC certificate is signed by an अधीक्षक / कार्यकारी / उप /
शाखा अभियंता, and a works tender names them page after page. Read as personnel, the Superintending
Engineer who *issues* a certificate becomes one of the bidder's proposed engineers — the bid staffed
from the municipality.

### The concept list, and what it is not

Thirty concepts, harvested from what five real RFPs ask for rather than imagined:

| group | concepts |
|---|---|
| money | `turnover` `net_worth` `net_profit` `solvency` `tender_fee` `audited_accounts` |
| people | `staff_strength` `key_roles` |
| identity | `pan` `gst` `msme_udyam` `legal_entity` `firm_age` `signing_authority` `bidder_particulars` |
| declarations | `non_blacklisting` `not_insolvent` `anti_collusion` `non_disclosure` `no_deviation` `past_performance` `no_joint_venture` `bid_forms` |
| labour | `provident_fund` `employee_insurance` |
| experience | `project_experience` `work_order` `completion_certificate` |
| capability | `quality_certification` `machinery` |

Across 72 criteria extracted from three RFPs, 66 (92%) now reach a concept-aware or dedicated
reader, against 20 (28%) before. The six that do not are not requirements a concept should cover:
one open-ended catch-all for other supporting documents, one that belongs to the marks stage (the
technical score), and four that are **scope headings the tender-side extractor wrongly turned into
document requirements** (a design phase, a project duration). That
last group is an over-extraction defect on the tender side, not a gap on the bid side.

Three mechanisms do most of the work:

* **An identifier is self-proving.** A PAN, GSTIN, CIN, LLPIN, Udyam number or EPF code is
  recognisable by shape alone, so the fact is located without depending on any word near it — which
  matters because the word is usually in a column header two cells away, or on a scan OCR misread.
* **A negation outranks every anchor.** An EPF intimation letter can state that the establishment is
  **not registered with ESIC**, followed by a general brochure about the ESI Act.
  Every word a search for ESIC registration looks for is present, and the document says the bidder
  does not have it. A veto says "this text is not about the fact"; a negation says "this text is
  about the fact and says no". Concepts that *are* a negative declaration ("we have not been
  blacklisted") opt out.
* **A concept locates a kind of fact, not an instance.** "Project Lead" and "Database
  Administrator" are the same kind of fact, so only the clause's own distinctive words can separate
  them. Without that, a roster row for a graphic designer was cited as evidence of a database
  administrator. Evidence of the right kind on a page naming nothing the clause asked for is
  still shown — but it yields `unknown`, never a pass.

And the page a bid lists its own contents on is never evidence: a bid asserting that it encloses a
certificate is not the certificate. Where that list is ruled, each row is a bare title with the page
range in another cell, so the row has none of the marks a checklist line usually has — the page is
identified from its heading instead.

### Grids nobody ruled

A chartered accountant aligns figures with spaces, so `page.tables` is empty on exactly the pages
that carry the turnover, net worth and profit series. `year_tables.grid_from_lines` rebuilds the
structure from the token pattern — a line naming *k* financial years declares *k* columns, and a
line ending in *k* figures fills them. The same routine works on an OCR'd page, which arrives as
lines of text with no coordinates at all. The unit is taken from whichever header or caption row
states it ("amount in lakhs"), because read without it a turnover of ₹5.12 crore is reported as
₹512 and the bidder fails a ₹4 crore requirement on a unit.

### Counting people

Three independent routes, all returned as separate claims: a declared count ("40+"), the roster's
own highest serial, and a tabulated staff statement. The roster is the only one that can be
counted, so `rows_read` travels with it as a **floor** — a shortfall below a count taken directly
from the submission is an artefact of the reading, not the bidder.

## The reading stage, and why its output is checked

`llm_verdict.py` is the only billed part of the pipeline besides OCR, and it runs **only where
deterministic extraction cannot reach a figure** — with ten to fifteen bidders on one tender, that
is the difference between a run costing cents and one that does not. Everything a model returns is
treated as a claim about the submission and verified against it:

1. **It may only cite evidence it was given.** A page number is never asked for, so one can never
   be invented: the page comes from the span the model selected.
2. **Its quote must be in that span, verbatim.** This is the gate that makes a fabricated figure
   impossible to cite — there is nowhere for it to come from.
3. **It never does arithmetic.** It reports the figure as printed ("512") and names the unit the
   document states ("lakhs"); the multiplication is ours.
4. **The comparison stays in Python**, which is what lets a result say "deterministic check — no AI
   in the comparison" truthfully.
5. **A negative is confirmed twice.** Before `not_matched` is issued on a model's reading, it goes
   to a stronger model; disagreement yields `unknown`, never a shortfall.

A reply failing any gate is not retried into compliance — the criterion comes back `unknown` with
the reason recorded, which is the honest answer when the submission could not be read.

### Verdict vocabulary

| verdict | meaning |
|---|---|
| `pass` | the figure was read and meets the requirement |
| `not_matched` | the figure was read and falls short — *"they do not meet this"* |
| `unknown` | not found, unreadable, contradictory, or an unconfident reading — *"we could not tell"* |
| `fail` | a rule broken outright |

`not_matched` and `fail` both disqualify; they differ in what the committee is being told.

## Verdict rules for PMC works criteria

| criterion | rule |
|---|---|
| EMD | paid for **this** tender (reference checked), payment status success, amount ≥ the tender's with ₹1 of rounding |
| Registration | any registration valid **on the bid date** whose class covers the estimated cost; the bid date comes from the EMD receipt, else the latest bid date among the tender's bids |
| Bid capacity | recomputed as `A × N × 1.5 − B` from the certificate's own A and B on the tender's N. A figure written two ways, or a certificate restricted to another authority, gives `unknown` |
| Turnover | the best year inside the tender's window; lakh / crore headings, FY and AY pairs, rupee + paise columns, "receipts" rows |
| Similar works | the experience ladder (3 / 2 / 1 works) after indexation; value precedence **done > accepted > tender amount, never the estimate**; a certificate still running (चालू, सुरु, बाकी, running bill, < 100 %) is not a completed work; year bounds from an explicit date, else the financial year, else the tender year as a floor and the issue date as a ceiling. A work whose nature or window is uncertain makes the verdict `unknown — would qualify once confirmed`, never `fail` |
| Key quantities | executed quantities (not the tender quantity column), from vertical or horizontal BOQ certificates; one work within the window, or a sum across works of one financial year; never `fail` on missing evidence |
| No JV | single-firm constitution and no JV agreement |
| Bid validity, rate analysis, additional security | **not gates**: recorded for the committee, shown neutral, never hold a bidder pending |

Nature of work is judged by a bilingual family lexicon on the work's final verb clause (location
words stripped: a work *on* a road is not a road work). A certificate in another contractor's
name is flagged (majority name, compared within one script).

## Running many bids at once

* Jobs run in a pool of 2 workers (`TE_JOB_WORKERS`); uploads return immediately.
* PDFium is not thread-safe — concurrent use crashed the process natively (`Trace/BPT trap`) —
  so every PDFium call holds one process-wide lock.
* SQLite in WAL mode with a 30 s busy timeout; all computation happens outside transactions,
  which stay short (read → compute → write).
* A job cut short by a server stop is resumed at the next start from the saved uploads (OCR pages
  already paid for come back from the cache). A job interrupted three times in a row is failed
  with a message instead of crash-looping.

## Bid accuracy harness (local only)

Bidder documents never enter the repository. Ground truth for real bids lives in
`backend/tests/local/*_truth.json` (git-ignored): the tender, the envelopes and the verdict and
figure range each criterion must reach, all verified by reading the pages.
`scripts/bid_report.py` runs a tender and its envelopes through the same code as the app (OCR
from the cache unless `--online`), `scripts/score_bids.py` scores it, and
`tests/test_bid_corpus.py` replays it offline and skips on machines without the files.

Current: **106 / 106** checks on one tender's ten envelopes. Those are the bids the rules were
built on, so this guards against regressions; it does not yet measure accuracy on unseen bids.

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
| accuracy scorer | `scripts/score_corpus.py` + `tests/local/corpus_truth.json` (git-ignored) |
| bid envelope: bid id, OCR plan, identity, bid date | `app/services/bidder_processing.py` |
| bid page typing (22 types) | `app/services/bidder_documents.py` |
| requirement-driven document fetching | `app/services/document_fetch.py` |
| bid evidence: EMD, registration, bid capacity, JV, key quantities | `app/services/bidder_evidence.py` |
| completed-work certificates (tables, key-value forms, Marathi) | `app/services/work_proof.py` |
| turnover grids | `app/services/year_tables.py` |
| executed BOQ quantities | `app/services/key_quantities.py` |
| nature of work | `app/services/nature.py` |
| verdict rules | `app/services/evaluation.py` |
| background jobs, resume | `app/services/jobs.py`, `app/services/pipeline.py` (`process_bidder`, `resume_interrupted`) |
| bid runner + scorer | `scripts/bid_report.py`, `scripts/score_bids.py`, `tests/test_bid_corpus.py` |
