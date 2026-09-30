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

---

# What three more bundles added

Added Sep 2026: the PMC drainage bundle **work_2286446**, the **PCMC encroachment-monitoring RFP**
(03/2024) and the **PMC DBT / Benefit Management re-tender** (PMC/IT/2025/17). Two are IT services
RFPs and one is a works bundle, which is what makes the first finding worth the most.

## 11. The experience ladder is a shape, not a PMC works template
All three state past experience as a *choice*, and all three use the same ladder:

| | 3 works | 2 works | 1 work | implied contract value |
|---|---|---|---|---|
| PMC drainage works 2286446 | 40% | 50% | 80% | ₹48,72,695 (stated) |
| PCMC encroachment RFP | ₹17 L | ₹21 L | ₹34 L | ₹42.3 L (inferred) |
| PMC DBT RFP | ₹32 L | ₹40 L | ₹64 L | ₹80 L (inferred) |

Only the vocabulary changes — "completed works (sewer/storm water line/retaining wall) costing not
less than", "different projects with each Project value of more than", "projects of minimum value
INR … each". Two structural facts follow, and `app/services/experience_ladder.py` rests on them:

* **Every rung divides back to the same contract value.** 17/0.4, 21/0.5 and 34/0.8 agree to 1.2%;
  32/40/64 agree exactly. A rung that divides to something else was misread, which is the only
  check here that catches a *wrong number* rather than a missing one.
* **Ratios identify the convention**, so an RFP that never prints a contract value still yields
  one. Both RFPs turn out to be 40/50/80 ladders.

**Reading one rung is worse than reading none.** The rungs sit in separate table cells — in the DBT
RFP either side of a page break — so a single-sentence rule sees only "One project of minimum value
INR 64 Lakhs" and states the requirement three times too strictly. A bidder with three ₹33 L
projects satisfies the tender and would have been rejected.

## 12. Past money is indexed before it is compared
Beside each financial criterion the PMC conditions print a table, and it governs turnover, the cost
of completed similar works, and the `A` term of bid capacity:

> Turnover of previous years shall be given weightage of 10% per year … to bring them to current
> year price level. — FY 2024-25 × 1.10, 2023-24 × 1.21, 2022-23 × 1.33, 2021-22 × 1.46, 2020-21 × 1.61

Against 2286446's ₹36,54,521 turnover bar, a best year of ₹30 L in FY 2022-23 is a fail at face
value and a **pass** at the ₹39.9 L the tender itself offers. Every bidder tested moved across the
line. Ignoring the table manufactures wrong disqualifications, so `app/services/indexation.py`
reads it from the tender (never assumes it), checks that the factors form one geometric
progression, and refuses to apply a table that does not.

Two consequences: "maximum turnover in any of the last five years" is a maximum *after* indexation,
so an older smaller year can win; and indexation only ever moves a bidder's figure up, so applying
it can turn a fail into a pass but never the reverse.

## 13. A clause's own label can name the wrong base
Clause 2 of the qualification criteria says EMD is "1% of the tender cost **(Total Estimated
Cost)**". The tender's EMD is ₹48,727, which is 1% of ₹48,72,695 — the cost put to tender excluding
GST — and not 1% of the ₹59,49,875 total. The same is true of the turnover formula, whose printed
numerator is "Total Estimated Cost of work". **The label is prose; the arithmetic is the evidence.**
Where a tender states a figure, deciding which base it used is a matter of trying both and seeing
which one it matches — which is what `_reconcile` does, and why it reports the base it found.

The turnover clause also contradicts itself: "Maximum Turnover should be at least 75% of the annual
outlay" where *annual outlay* is already defined as `E × 0.75 ÷ years`. Applied literally that is
0.5625·E. The boxed formula governs, and the tender's own stated ₹36.55 L confirms it.

## 14. When the bundle contradicts itself, the signed work-specific sheet wins
Defect liability period in 2286446:

| source | says |
|---|---|
| conditions volume p.8 (unsigned printed template) | 2 years |
| signed data sheet, handwritten over the printed line | 5 years |
| Marathi tender goshwara, item 7 | 60 महिने |

Normalising months to years shows two independent sources agreeing at 5 years and one stale
template default. The rule that generalises: a value handwritten onto a signed, work-specific sheet
supersedes the same field's printed value in the generic conditions volume — and a second source in
different units is what confirms it, so durations must be compared after normalisation, never as
printed.

The 4% / 5% security deposit reconciles here for a second time: 5% total (₹2,43,635) less the 1%
EMD converted into it leaves the ₹1,94,908 the notice prints as 4%.

## 15. A qualification of the bidder and an obligation of the contractor read alike
The same volume states both, 16 pages apart:

* p.94 — "Contractor should keep two nos of experienced Civil Engineers B.E (civil) **at each
  site** … minimum of 3 years' experience of execution of drainage lines."
* p.110 — qualification table, Staff: "Engineer B.E. Civil / DCE / Technical Staff … **min. 5 Years**"

Both name a role, a degree and a number of years. Only the second is a gate, and reading the first
sets the bar at 3 years when the tender requires 5 — passing a bidder the committee would reject.
The discriminator is a deployment verb beside a site, not the page number.

## 16. In an RFP, certifications inside a marks table are not requirements
The DBT rubric offers CMMi 3/4/5 for 3/4/5 marks and ISO 27001/9001/20000-1 for 3/1/1, capped at
ten, under "Bidder **may** provide". A bidder with none of them scores zero on that block and is
still fully qualified. Recording them as mandatory documents would disqualify compliant bidders —
the same error the corpus produced once before with a "choose any two of four" menu.

## 17. Three different things are called L1
| tender | what is ranked |
|---|---|
| PMC works 2286446 | percentage above/below on the estimated cost (Percentage BoQ, B-1 form) |
| PMC DBT RFP | lowest total price, but only among bidders scoring ≥ 70% of 100 technical marks |
| PCMC encroachment RFP | lowest **per-person** rate; no technical marks at all, eligibility is pass/fail |

"L1" alone does not say what to sort by, so the bid basis has to be carried with it.

## 18. Additional performance security is a piecewise function of the discount
Clause 8 of the qualification criteria, on the cost put to tender excluding GST, royalty and
testing charges:

| offer | additional security |
|---|---|
| up to 1% below | nil |
| >1% to 10% below | 1% |
| >10% to 15% below | 1% + (d − 10)% — a 15% discount gives 6% |
| >15% below | 1% + 5% + 2 × (d − 15)% — a 19% discount gives 14% |

Payable by NEFT/RTGS within 8 days of the financial bid opening; failure forfeits the EMD and L-2 is
asked to match. This is a bidder-phase calculation and is not implemented yet.

## 19. Templates carry contamination the tender never corrects
PCMC's RFP for a mobile web application requires, in criterion (d), "experience in establishing
prefab hospital structures/units on turnkey basis". Its turnover criterion asks for the best 3
"successive financial years out of 5 Years" and then lists six. Both are extracted as written: the
tender is the requirement, however it got there, and a reader that quietly repaired either would be
inventing a criterion.

## Open items for the bidder phase
Percentage-rate bids and the additional-performance-security ladder (§18); approved-make compliance
(electrical); similar-work nature judgement using the per-tender (sometimes handwritten) nature
text; bid-capacity certificates, including the "tentative will not be considered", CA UDIN and
one-year validity rules; statements I–IV formats; the RFP technical rubrics (§16) — the DBT
scorecard is read only as its ≥70% gate, not as the 100-mark breakdown behind it.
