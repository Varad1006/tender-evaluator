# What the PMC / MahaTender corpus taught us

Corpus: 13 tender downloads from mahatenders.gov.in (PMC ward offices, Road, Drainage, Electrical)
plus one IT RFP — ~1,100 pages. Every finding below is encoded in the extraction code and, where
possible, pinned by `tests/test_corpus_golden.py`.

## 1. A tender is a bundle, not a PDF
Typical download: the tender notice PDF, a shared general-conditions PDF, a BOQ `.xls`, and
sometimes a `.rar` with the data sheet, detailed conditions, technical specifications, SOPs and QAPs.
Eligibility often lives **outside** the notice (e.g. notice 245 has blank eligibility pages; the
criteria are in the scanned `TERMS.pdf`). Some bundles also carry internal approvals and committee
minutes (scancopies_275 p.1–7, p.47–61) that are not bidder-facing.

## 2. Much of the eligibility text is not machine-readable
* Fully scanned bundles (scancopies_235: 48 pages, scancopies_275: 62 pages), some rotated 90°.
* Scans with a **garbage OCR layer** (TERMS.pdf, newatisharti.pdf, phone scans from "OKEN Scanner").
* Marathi typed in a **legacy ASCII font** (155.pdf Goshwara: "H$m_mMo Zmd" = कामाचे नाव).
* Unicode Marathi whose text layer drops conjuncts ("किन अिभयंता" for कनिष्ठ अभियंता).
* **Handwriting**: the drainage "Parameters for Qualification Criteria" sheet has the thresholds
  handwritten, and the similar-work nature printed text **struck out and rewritten by hand**.

## 3. PMC works eligibility is a standard template of formulas
For every works tender above ₹2 lakh (clause 2.9 of the PMC tender conditions; Annexure B in road volumes):

| criterion | rule |
|---|---|
| turnover | max annual turnover in any of the last 5 years ≥ 0.75 × E ÷ (period in years) |
| similar works | 3 works ≥ 40 % of E, or 2 ≥ 50 %, or 1 ≥ 80 % (last 5 years) |
| bid capacity | (A × N × 1.5) − B ≥ E, CA-certified for this tender |
| key quantities | 30 % of the 3–5 major BOQ quantities in any one year |
| registration | compulsory up to ₹1.5 Cr (PMC/PCMC/PWD/MSRDC/CPWD/NHAI/other Govt.) |
| JV | not allowed |

It is department-agnostic (Road, Drainage, Electrical all use it). Road volumes add a
"one similar work ≥ 50 % in the last **3** years" clause that contradicts the 40/50/80 % / 5-year rule.

## 4. Printed numbers are unreliable
* **Template unit error**: "i.e., Rs.423647 **Lakhs** (Rs. Four Lakhs Twenty Three Thousand…)" —
  read literally, 100,000× too large. Words and the formula agree on ₹4,23,647.
* **Blank placeholders**: "Rs. ………… Lakhs", "Pre-bid … /…/2019".
* **Different base**: drainage tender 94 computes turnover, similar works and bid capacity on the
  GST-inclusive ₹34.99 L, not the ₹29.61 L put to tender (tender 89 uses the latter).
* **Self-contradiction** inside one volume (road volume clause 15 vs Annexure B; RFP weightage text
  30/50/20 vs table 20/35/25/20; stale FY 2018–21 dates in a 2026 RFP).
* **Security deposit** appears as 4 % (balance) and 5 % (total) — consistent, not a conflict.

## 5. The structured portal forms are the reliable anchor
Tender Notice box, Tender Work Input Form, B-1 row, conditions tender table, Tender Abstract total and
the BOQ spreadsheet each state the estimated cost; in the ward notices up to six sources agree.

## 6. Documentary requirements are enumerated lists
"CHECK LIST … should be submitted", "To qualify … furnish the information detailed out below", and
the RFP's PQ table (criterion · documentary evidence · form PQ_n). The items have no "shall", so
sentence rules miss them; a list/table extractor catches them.

## 7. Price is not a price
PMC works use the NIC **Percentage BoQ**: bidders quote Excess (+) / Less (−) / At Par on the
estimated cost. L1 is the lowest resulting amount; bids ≥ 10 % below need rate analysis, ≥ 15 % below
need additional performance security. → affects the bidder phase (ranking), not tender extraction.

## 8. Award mechanisms seen
* Works: "lowest evaluated responsive bid" / Percentage Rate B-1 → **L1**, tie → revised offers, then lots.
* IT RFP: **technical ≥ 70 % of a 100-mark rubric, then L1**; tie → most projects in 3 years.

## 9. Execution conditions are not eligibility
Specs, SOPs, QAPs and 100+ pages of contract clauses contain "within 10 days", "licensed electrical
personnel", approved-make lists, GIS-mapping directives. Sentence rules are therefore not run on
execution documents, and completion/warranty-style rules are off for PMC works bundles.

## 10. Edge cases found by scoring against ground truth
* **Run-on lists.** Road volume 269's clause 15(h) prints items 1–15 without punctuation between them;
  read as one sentence, the GST rule claimed it and PF/ESI, Works Contract, ITR and PAN vanished.
* **Words run together** in the text layer: "EARNESTMONEYDEPOSIT", "depositofRs. 178063/-",
  "completion ofwork is 12 calendarmonths" — the only place 269 states its EMD and period.
* **Blank templates.** 269's key-quantity grid lists nine items with no quantities.
* **Checklist look-alikes**: RFP data sheet (key → value), financial-capability formats with year
  columns, boxed conditions ("AWARD CRITERIA The Owner may…"), numbered chapter headings, a 19-clause
  NDA reached through a mid-sentence mention of "qualification criteria".
* **Restated requirements**: conditions summaries ("Staff", "Machinery", "Registration") and RFP
  submission lists repeat the criteria as documents.
* **Blank scans**: 16 of the scanned pages are backsides or separators; OCR on one produced invented
  Hindi text.

## Open items for the bidder phase
Percentage-rate bids; approved-make compliance (electrical); similar-work nature judgement using the
per-tender (sometimes handwritten) nature text; bid-capacity certificates; statements I–IV formats.
