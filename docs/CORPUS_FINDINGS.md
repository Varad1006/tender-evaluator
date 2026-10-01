# What real tenders and bids taught us

The rules were built by reading real MahaTender downloads (PMC ward offices, Road, Drainage,
Electrical works, and IT RFPs) and real bid envelopes, page by page. **No document's content is
in this repository**: no identifiers, names, figures or sentences. What follows are the *patterns*
those documents showed, with invented examples. The documents, their ground truth and a detailed
version of these notes stay on the machine that has them (`backend/tests/local/`, git-ignored).

## 1. A tender is a bundle, not a PDF
A typical download is a notice PDF, a shared general-conditions PDF, a BOQ `.xls`, and sometimes a
`.rar` with the data sheet, detailed conditions, specifications, SOPs and QAPs. Eligibility often
lives **outside** the notice: a notice can leave its eligibility pages blank while the criteria
sit in a scanned conditions file. Some bundles also carry internal approvals and committee minutes
that are not bidder-facing.

## 2. Much of the eligibility text is not machine-readable
* Fully scanned bundles, some pages rotated 90°.
* Scans carrying a **garbage OCR layer** from a phone scanning app.
* Marathi typed in a **legacy ASCII font**, where "कामाचे नाव" arrives as Latin punctuation soup.
* Unicode Marathi whose text layer drops conjuncts and splits words.
* **Handwriting**: qualification thresholds written by hand on a printed sheet, and a printed
  nature-of-work line struck out and rewritten by hand.

## 3. PMC works eligibility is a standard template of formulas
For every works tender above ₹2 lakh:

| criterion | rule |
|---|---|
| turnover | max annual turnover in any of the last 5 years ≥ 0.75 × E ÷ (period in years) |
| similar works | 3 works ≥ 40 % of E, or 2 ≥ 50 %, or 1 ≥ 80 % (last 5 years) |
| bid capacity | (A × N × 1.5) − B ≥ E, CA-certified for this tender |
| key quantities | 30 % of the 3–5 major BOQ quantities in any one year |
| registration | compulsory up to ₹1.5 Cr (any government or semi-government registration) |
| JV | where the tender prohibits it (§32) |

It is department-agnostic. Some road volumes add a "one similar work ≥ 50 % in the last 3 years"
clause that contradicts the 40/50/80 % / 5-year rule; both are shown and the committee decides.

## 4. Printed numbers are unreliable
* **Template unit errors**: a rupee figure printed with "Lakhs" after it, read literally 100,000×
  too large, while the amount in words and the formula agree on the rupee figure.
* **Blank placeholders**: "Rs. ………… Lakhs", undated pre-bid lines.
* **A different base**: a tender computing turnover, similar works and bid capacity on the
  GST-inclusive cost rather than the cost put to tender.
* **Self-contradiction** inside one volume: a clause against its annexure, weightages in the text
  against the table, stale financial-year dates copied from an older document.
* **Security deposit** printed as 4 % (balance) and 5 % (total): consistent, not a conflict.

## 5. The structured portal forms are the reliable anchor
The notice box, the work input form, the B-1 row, the conditions tender table, the abstract total
and the BOQ spreadsheet each state the estimated cost; in ward notices up to six sources agree.

## 6. Documentary requirements are enumerated lists
A check list of documents to submit, a qualification clause followed by numbered items, an RFP's
pre-qualification table (criterion · evidence · form). The items carry no "shall", so sentence
rules miss them and a list/table extractor catches them.

## 7. Price is not a price
PMC works use the NIC **Percentage BoQ**: bidders quote above / below / at par on the estimated
cost. L1 is the lowest resulting amount; bids ≥ 10 % below need rate analysis, ≥ 15 % below need
additional performance security. That is ranking, not extraction.

## 8. Award mechanisms seen
* Works: lowest evaluated responsive bid / Percentage Rate B-1 → **L1**; ties go to revised offers.
* IT RFPs: **technical score ≥ a threshold of a marks rubric, then L1**.

## 9. Execution conditions are not eligibility
Specifications, SOPs, QAPs and contract clauses are full of "within 10 days", licensed personnel
and approved-make lists. Sentence rules are not run on execution documents.

## 10. Edge cases found by scoring against ground truth
* **Run-on lists**: fifteen numbered items with no punctuation between them, read as one sentence.
* **Words run together** by the text layer: "EARNESTMONEYDEPOSIT", "calendarmonths".
* **Blank templates**: a key-quantity grid listing items with no quantities.
* **Checklist look-alikes**: data sheets (key → value), financial formats with empty year columns,
  boxed conditions, numbered chapter headings, a clause that merely mentions "qualification
  criteria".
* **Restated requirements**: summaries and submission lists repeating the criteria as documents.
* **Blank scans**: backsides and separators; OCR on one produced invented text.

## 11. The experience ladder is a shape, not a PMC works template
Works tenders and IT RFPs alike state past experience as a *choice* with one shape — more projects
of lower value, or fewer of higher value (3 × 40 %, 2 × 50 %, 1 × 80 %, or the same as plain
amounts). Every rung divides back to one contract value, which is the only check that catches a
*wrong number* rather than a missing one, and the ratios identify the convention even when no
contract value is printed. **Reading one rung is worse than reading none**: the rungs sit in separate
cells, sometimes across a page break, and a single-sentence rule states the requirement three times
too strictly.

## 12. Past money is indexed before it is compared
PMC conditions bring earlier years to current prices — a factor table (FY back one year × 1.10, two
years × 1.21, …) or a sentence stating 10 % per year (§31). It governs turnover, the cost of
completed similar works, and the `A` of bid capacity. Ignoring it manufactures wrong
disqualifications. The factors are read from the tender, checked to be one geometric progression,
and never assumed. A maximum "in any of the last five years" is a maximum *after* indexation.

## 13. A clause's own label can name the wrong base
A clause can say EMD is 1 % of the "total estimated cost" while the EMD it prints is 1 % of the
cost put to tender excluding GST. **The label is prose; the arithmetic is the evidence**: bases are
decided by trying both and reporting which one the tender's own figure matches.

## 14. When the bundle contradicts itself, the signed work-specific sheet wins
A defect-liability period of 2 years in a generic printed volume against 5 years handwritten on the
signed data sheet and 60 months in the Marathi summary: two independent sources agree once units
are normalised. A value written onto a signed, work-specific sheet supersedes the generic volume.

## 15. A qualification of the bidder and an obligation of the contractor read alike
"Engineers to be stationed at each site with some years' experience" (an execution clause) and
"engineer with a minimum of N years" (the qualification table) both name a role, a degree and
years. Only the second is a gate; the discriminator is a deployment verb beside a site.

## 16. In an RFP, certifications inside a marks table are not requirements
Certifications a bidder "may provide" for marks are not mandatory documents. Recording them as
required would disqualify compliant bidders.

## 17. Three different things are called L1
Percentage above/below the estimate (works), lowest total price among technically qualified bidders
(scored RFPs), and lowest unit rate (pass/fail RFPs). The bid basis is carried with "L1".

## 18. Additional performance security is a piecewise function of the discount
Nil to 1 % below; 1 % up to 10 % below; then rising with the discount above 10 % and faster above
15 %. A bidder-phase calculation, not implemented yet.

## 19. Templates carry contamination the tender never corrects
Requirements copied from another tender's template (a criterion about an unrelated industry, a
"best 3 of 5 years" clause listing six). They are extracted as written: the tender is the
requirement, and quietly repairing it would invent a criterion.

## 30. A rule is not a figure, but it fixes one
PMC's tender-procedure circular states EMD as a rule (1 % up to ₹1.5 Cr; above, 0.5 % or ₹1.5
lakh). With no amount printed, the EMD is worked out from the rule and the estimated cost, and says
so. It is computed only when the page states the rule's tiers, never assumed.

## 31. An escalation rule written as a sentence still governs
A sentence asking for a प्रतिवर्षी १०% वाढ (10 % more for each year) with no factor table: the factors
are computed from the stated rate, with the tender's own financial year as the current level.

## 32. A mention is not a prohibition
Joint ventures appear in lists of entities that can be blacklisted and of points to clarify — which
implies JVs may bid. Only a prohibition makes "no joint venture" a criterion.

## 33. OCR renames a criterion; its formula survives
The Marathi name of bid capacity comes back from OCR misspelt or mangled; the formula
"(अ x एन x १.५) - बी" survives and locates the criterion.

## 34. Some bundles never state the completion period
Turnover is 75 % of the *annual* value, which needs the period. When no document prints it, the
criterion shows no number and says why, rather than assuming twelve months.

# What bid envelopes taught us

## 20. A bid is a scan, not a document
Most pages are image scans: one merged PDF per bidder, in no fixed order, with blank backsides.
Pages are typed by their heading (English and Marathi), not by any word on the page. Blank sheets
are detected locally and never sent to OCR.

## 21. Sworn statements restate every criterion
Undertakings on stamp paper (हमीपत्र, प्रतिज्ञापत्र, affidavit) repeat the requirements in the
bidder's voice. They are typed first, so no finder reads a promise as a certificate.

## 22. A work certificate can state four amounts
The estimate (पूर्वगणनपत्रक, पुगप, तरतूद), the tender amount, the contractor's accepted amount
(ठेकेदाराची रक्कम) and the amount of work done (काम पूर्ण रक्कम, final bill). The value of the work
is the first of **done > accepted > tender amount**; the estimate is never the value of a contract.

## 23. "Completed" is a claim the certificate can contradict
Running-bill certificates, "work in progress" (काम चालू, सुरु, बाकी) and percentages below 100 %
arrive under a completion heading. The form's own status line decides.

## 24. When a work finished is often not printed
A financial year (two calendar years), only a tender number (a floor), or only the issue date (a
ceiling). A work that might or might not fall in the window gives `unknown — would qualify once
confirmed`, never `fail`.

## 25. Bids carry other firms' certificates
A related firm's or a sub-contractor's certificate appears as if it were the bidder's. The firm
named on each certificate is compared with the bid's majority name and flagged.

## 26. Bid capacity is recomputed, not copied
A × N × 1.5 − B is recomputed with the tender's N. An A written two ways, or a certificate
addressed to another authority, gives `unknown`.

## 27. Turnover certificates come in every shape
Lakh or crore column headings, FY beside AY, rupees and paise in separate columns, "receipts"
instead of "turnover", bullet-prefixed year cells.

## 28. BOQ certificates print the tender quantity beside the executed one
Only the executed column counts. Items can run across a row with quantities on the next; the same
certificate is often enclosed twice.

## 29. The price is not in the technical envelope
The rate is in the financial envelope, opened later. Nothing is ranked on a guessed price.

## Open items
The financial envelope; bid-capacity certificate formalities (validity, UDIN); statements I–IV
beyond completed works; approved-make compliance; the RFP technical rubrics; and accuracy measured
on bids the rules were not built on.
