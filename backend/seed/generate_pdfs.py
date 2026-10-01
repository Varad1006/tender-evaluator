"""Generate the seeded demo corpus: one PMC tender PDF and three fabricated bidder PDFs.

The bidder documents are written to produce a deliberate verdict mix (PoC Build Brief,
"Test Data & Corpus"):

  * Sunrise Energy Systems  - clean pass on every criterion (becomes L1)
  * GreenVolt Infra         - cheapest, but fails the turnover gate (disqualified)
  * Urja Solar Tech         - warranty stated as 10 years on p.9 and 7 years on p.27
                              (contradiction -> unknown) and no ESIC registration
                              anywhere in the submission (not found -> unknown)

Run:  uv run python seed/generate_pdfs.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT_DIR = Path(__file__).parent / "pdfs"

TENDER_NO = "PMC/ELEC/2026/0417"
NAME_OF_WORK = (
    "Supply, Installation &amp; Commissioning of Grid-Connected Rooftop Solar PV Systems "
    "at Municipal Buildings"
)

STYLES = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=17, leading=22, alignment=TA_CENTER, spaceAfter=6),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica-Bold", fontSize=12, leading=16, alignment=TA_CENTER, spaceAfter=18),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=9),
    "center": ParagraphStyle("center", fontName="Helvetica", fontSize=10.5, leading=15, alignment=TA_CENTER, spaceAfter=6),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555")),
}


# --------------------------------------------------------------------------------------
# Rendering helpers
# --------------------------------------------------------------------------------------

def _flow(block):
    kind, content = block
    if kind == "spacer":
        return Spacer(1, content * mm)
    if kind == "table":
        data = [[Paragraph(cell, STYLES["p"]) for cell in row] for row in content]
        table = Table(data, colWidths=[62 * mm, 105 * mm])
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F2F2")),
        ]))
        return table
    return Paragraph(content, STYLES[kind])


def build_pdf(path: Path, pages: list[list[tuple]], running_header: str) -> None:
    total = len(pages)

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        width, height = A4
        canvas.drawString(20 * mm, height - 12 * mm, running_header)
        canvas.drawRightString(width - 20 * mm, 10 * mm, f"Page {doc.page} of {total}")
        canvas.restoreState()

    story = []
    for index, blocks in enumerate(pages):
        story.extend(_flow(b) for b in blocks)
        if index < total - 1:
            story.append(PageBreak())

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=24 * mm, bottomMargin=22 * mm,
        title=path.stem, author="Tender Evaluator PoC seed generator",
    )
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)


def fill_pages(total: int, fixed: dict[int, list[tuple]], filler: list[list[tuple]]) -> list[list[tuple]]:
    """Place `fixed` content on exact 1-based page numbers; cycle filler content elsewhere."""
    pages, cursor = [], 0
    for number in range(1, total + 1):
        if number in fixed:
            pages.append(fixed[number])
        else:
            pages.append(filler[cursor % len(filler)])
            cursor += 1
    return pages


# --------------------------------------------------------------------------------------
# Tender document
# --------------------------------------------------------------------------------------

def tender_pages() -> list[list[tuple]]:
    return [
        # 1 - cover
        [
            ("spacer", 30),
            ("title", "PUNE MUNICIPAL CORPORATION"),
            ("subtitle", "Electrical Department"),
            ("spacer", 10),
            ("title", "TENDER DOCUMENT"),
            ("spacer", 12),
            ("center", f"Tender No.: {TENDER_NO}"),
            ("center", f"Name of Work: {NAME_OF_WORK}"),
            ("center", "Estimated Cost: Rs. 45,00,000/- (Rupees Forty Five Lakh only)"),
            ("center", "Date of Issue: 12-01-2026"),
            ("spacer", 30),
            ("center", "Issued by: Executive Engineer (Electrical), Pune Municipal Corporation, Shivajinagar, Pune 411005"),
        ],
        # 2 - NIT
        [
            ("h1", "Notice Inviting Tender"),
            ("p", "The Pune Municipal Corporation (PMC), through its Electrical Department, invites online bids from "
                  "eligible bidders for the work described below. Bidders are advised to read the complete tender "
                  "document before submitting their bid."),
            ("table", [
                ["Name of Work", NAME_OF_WORK],
                ["Tender Reference", TENDER_NO],
                ["Estimated Cost", "Rs. 45,00,000/-"],
                ["Earnest Money Deposit", "Rs. 45,000/- payable online"],
                ["Tender Fee", "Rs. 1,500/- (non-refundable)"],
                ["Last date of bid submission", "05-02-2026, 17:00 hrs"],
                ["Opening of technical bids", "07-02-2026, 11:00 hrs"],
                ["Bid validity", "120 days from the date of opening of technical bids"],
            ]),
        ],
        # 3 - contents
        [
            ("h1", "Contents"),
            ("p", "Section 1 - Instructions to Bidders (pages 4 to 8)"),
            ("p", "Section 2 - General Eligibility Criteria (page 9)"),
            ("p", "Section 3 - Scope of Work (page 10)"),
            ("p", "Section 4 - Commercial Terms and Conditions (pages 11 to 13)"),
            ("p", "Section 5 - Financial Eligibility (pages 14 to 15)"),
            ("p", "Section 6 - Technical Qualification (pages 16 to 17)"),
            ("p", "Section 7 - Technical Specifications (pages 18 to 19)"),
            ("p", "Section 8 - Manpower Requirements (page 20)"),
            ("p", "Section 9 - Method of Evaluation and Award (page 21)"),
            ("p", "Forms A and B (page 22)"),
        ],
        # 4
        [
            ("h1", "Section 1 - Instructions to Bidders"),
            ("h2", "1.1 General"),
            ("p", "Bids shall be submitted online through the PMC e-tendering portal. Physical bids will not be accepted."),
            ("h2", "1.2 Two-part bid"),
            ("p", "The bid shall be submitted in two parts: Part A (Technical Bid) containing the eligibility and "
                  "qualification documents, and Part B (Financial Bid) containing the price schedule in Form B."),
            ("h2", "1.3 Signing of bid"),
            ("p", "All documents submitted with the bid shall be signed and stamped on every page by the authorised "
                  "signatory of the bidder."),
            ("h2", "1.4 Conditional bids"),
            ("p", "Conditional bids shall be summarily rejected."),
        ],
        # 5
        [
            ("h2", "1.5 Cost of bidding"),
            ("p", "The bidder shall bear all costs associated with the preparation and submission of its bid, and PMC "
                  "will in no case be responsible for those costs."),
            ("h2", "1.6 Clarifications"),
            ("p", "Any clarification regarding the tender document may be sought in writing up to seven days before "
                  "the last date of bid submission."),
            ("h2", "1.7 Pre-bid meeting"),
            ("p", "A pre-bid meeting will be held at the office of the Executive Engineer (Electrical). Minutes of the "
                  "meeting will be published on the portal and shall form part of the tender document."),
        ],
        # 6
        [
            ("h2", "1.8 Earnest Money Deposit"),
            ("p", "The bidder shall pay an Earnest Money Deposit of Rs. 45,000/- online. Bids without the Earnest "
                  "Money Deposit shall be rejected."),
            ("h2", "1.9 Language of bid"),
            ("p", "The bid and all correspondence shall be in English or Marathi."),
            ("h2", "1.10 Amendments"),
            ("p", "PMC may amend the tender document at any time before the last date of bid submission by issuing a "
                  "corrigendum on the portal."),
        ],
        # 7
        [
            ("h2", "1.11 Opening of bids"),
            ("p", "Technical bids will be opened online on the date stated in the Notice Inviting Tender in the "
                  "presence of bidders who choose to attend."),
            ("h2", "1.12 Evaluation process"),
            ("p", "Technical bids will be evaluated first against the criteria in Sections 2, 4, 5, 6, 7 and 8. "
                  "Financial bids of only technically qualified bidders will be opened."),
        ],
        # 8
        [
            ("h2", "1.13 Fraud and corrupt practices"),
            ("p", "PMC requires bidders to observe the highest standard of ethics during the procurement process. A "
                  "bidder found to have engaged in corrupt or fraudulent practice shall be rejected."),
            ("h2", "1.14 Right to accept or reject"),
            ("p", "PMC reserves the right to accept or reject any bid, or to annul the tender process, without "
                  "assigning any reason."),
        ],
        # 9 - eligibility
        [
            ("h1", "Section 2 - General Eligibility Criteria"),
            ("p", "2.1 Bidders shall submit a valid GST registration certificate as proof of statutory compliance."),
            ("p", "2.2 A valid EPF and ESIC registration certificate is mandatory for all bidders employing contract labour."),
            ("p", "2.3 Bidders must satisfy every eligibility criterion on the last date of bid submission."),
            ("p", "2.4 A bidder not meeting any criterion in this Section shall be declared non-responsive and its "
                  "financial bid shall not be opened."),
        ],
        # 10 - scope
        [
            ("h1", "Section 3 - Scope of Work"),
            ("p", "3.1 The work comprises design, supply, installation, testing and commissioning of grid-connected "
                  "rooftop solar PV systems of aggregate capacity 150 kWp across six municipal buildings."),
            ("p", "3.2 The scope includes mounting structures, modules, inverters, cabling, earthing, lightning "
                  "protection, net-metering liaison with MSEDCL and training of PMC staff."),
            ("p", "3.3 The system shall be designed for a service life of not less than 25 years."),
            ("p", "3.4 The bidder shall carry out a site survey of each building before finalising the design."),
        ],
        # 11 - commercial
        [
            ("h1", "Section 4 - Commercial Terms and Conditions"),
            ("p", "4.1 Supply, installation and commissioning shall be completed within 90 days of issue of the work order."),
            ("p", "4.2 Payment terms: 60% of the contract value shall be released on supply of material at site, 30% "
                  "on successful commissioning, and 10% after acceptance by the Engineer-in-Charge."),
            ("p", "4.3 The successful bidder shall furnish a performance security of 5% of the contract value within "
                  "15 days of issue of the Letter of Acceptance."),
        ],
        # 12
        [
            ("p", "4.4 Liquidated damages at 0.5% of the contract value per week of delay shall be levied, subject "
                  "to a maximum of 10% of the contract value."),
            ("p", "4.5 Prices shall be firm and inclusive of all taxes and duties for the entire contract period."),
            ("p", "4.6 The contractor shall insure all material at site until handing over to PMC."),
        ],
        # 13
        [
            ("p", "4.7 Neither party shall be liable for delay caused by force majeure events, provided the other "
                  "party is notified in writing."),
            ("p", "4.8 Disputes arising out of this contract shall be subject to the jurisdiction of courts in Pune."),
        ],
        # 14 - financial eligibility
        [
            ("h1", "Section 5 - Financial Eligibility"),
            ("p", "5.1 The bidder shall furnish audited financial statements evidencing an average annual turnover "
                  "of not less than Rs. 5,00,00,000/- (Rupees Five Crore only) over the preceding three financial years."),
            ("p", "5.2 The financial years to be considered are FY 2022-23, FY 2023-24 and FY 2024-25."),
            ("p", "5.3 The audited statements shall be signed by a Chartered Accountant with a valid UDIN."),
        ],
        # 15
        [
            ("p", "5.4 In case of any discrepancy between figures stated in the bid and the audited statements, the "
                  "audited statements shall prevail."),
            ("p", "5.5 The tender committee reserves the right to seek clarification on any financial document."),
        ],
        # 16 - technical qualification
        [
            ("h1", "Section 6 - Technical Qualification"),
            ("p", "6.1 The bidder must have successfully completed at least one rooftop solar installation project of "
                  "value not less than Rs. 2,00,00,000/- (Rupees Two Crore only) within the last five years."),
            ("p", "6.2 Documentary proof in support of Clause 6.1, such as the work order and the client's completion "
                  "letter, shall be enclosed."),
        ],
        # 17
        [
            ("p", "6.3 Projects executed for a sister concern or group company of the bidder shall not be considered."),
            ("p", "6.4 The value of a project shall mean the value of work actually executed, excluding taxes."),
        ],
        # 18 - specs
        [
            ("h1", "Section 7 - Technical Specifications"),
            ("p", "7.1 PV modules shall be mono-PERC or TOPCon, of minimum 540 Wp rating, conforming to IEC 61215 and IEC 61730."),
            ("p", "7.2 The bidder shall attach a manufacturer authorization certificate for the solar PV modules "
                  "proposed to be supplied."),
        ],
        # 19
        [
            ("p", "7.3 Inverters shall be grid-tied string inverters conforming to IEC 62109 with remote monitoring."),
            ("p", "7.4 The system, including panels and inverters, shall carry a minimum comprehensive warranty of 10 "
                  "years from the date of commissioning."),
            ("p", "7.5 Mounting structures shall be hot-dip galvanised steel designed for a wind speed of 170 km/h."),
        ],
        # 20 - manpower
        [
            ("h1", "Section 8 - Manpower Requirements"),
            ("p", "8.1 The bidder shall deploy at least one certified electrical engineer at the project site "
                  "throughout the execution period."),
            ("p", "8.2 All workers shall be provided with personal protective equipment and shall follow the safety "
                  "instructions of the Engineer-in-Charge."),
        ],
        # 21 - evaluation method
        [
            ("h1", "Section 9 - Method of Evaluation and Award"),
            ("p", "9.1 The contract shall be awarded on the basis of L1, that is, the least cost among technically "
                  "qualified bidders."),
            ("p", "9.2 Only the financial bids of bidders who meet all criteria in Sections 2, 4, 5, 6, 7 and 8 shall be opened."),
            ("p", "9.3 In case two or more bidders quote the same lowest price, the committee shall decide by draw of lots."),
        ],
        # 22 - forms
        [
            ("h1", "Forms"),
            ("h2", "Form A - Covering Letter"),
            ("p", "To, The Executive Engineer (Electrical), Pune Municipal Corporation. We hereby submit our bid for "
                  "the above work and accept all terms and conditions of the tender document."),
            ("h2", "Form B - Price Schedule"),
            ("p", "Lump-sum price for the complete scope of work, inclusive of all taxes and duties, in figures and in words."),
        ],
    ]


# --------------------------------------------------------------------------------------
# Bidder documents
# --------------------------------------------------------------------------------------

BIDDER_FILLER = [
    [("h1", "Methodology"),
     ("p", "Our execution methodology follows a design-first approach: a detailed site survey, shadow analysis "
           "and structural assessment of each roof precede the final layout."),
     ("p", "Material is dispatched only after design approval by the Engineer-in-Charge, which avoids rework at site.")],
    [("h1", "Project Schedule"),
     ("p", "Weeks 1 and 2 cover site survey and design approval. Weeks 3 to 6 cover procurement. Weeks 7 to 10 "
           "cover installation, and week 11 covers testing and handover.")],
    [("h1", "Quality Assurance Plan"),
     ("p", "Every lot of modules and inverters is inspected on receipt against the approved datasheets. Torque "
           "values, cable routing and earthing continuity are checked and recorded for every building.")],
    [("h1", "Health, Safety and Environment"),
     ("p", "All rooftop work is carried out with fall-arrest systems and edge protection. A toolbox talk is held "
           "at the start of every shift and recorded in the site register.")],
    [("h1", "Operation and Maintenance Plan"),
     ("p", "Modules are cleaned fortnightly and inverters are inspected monthly. Breakdown complaints are attended "
           "within 48 hours of being reported.")],
    [("h1", "Training Plan"),
     ("p", "Two half-day sessions will be conducted for PMC staff covering safe isolation, daily monitoring and "
           "basic fault identification.")],
    [("h1", "Organisation Chart"),
     ("p", "The project is led by a Project Manager who reports to a Director. Site execution is handled by a "
           "site-in-charge supported by supervisors and technicians.")],
    [("h1", "List of Tools and Equipment"),
     ("p", "Insulation resistance tester, earth resistance tester, clamp meter, IV-curve tracer, thermal camera, "
           "torque wrenches and safety harnesses.")],
    [("h1", "Compliance Statement"),
     ("p", "We confirm compliance with the technical specifications in Section 7 of the tender document without deviation.")],
    [("h1", "Declaration"),
     ("p", "We declare that our firm has not been debarred by any government department or public sector "
           "undertaking as on the date of this bid.")],
    [("h1", "Power of Attorney"),
     ("p", "The Board of Directors has authorised the undersigned to sign and submit this bid and all related "
           "documents on behalf of the company.")],
    [("h1", "Client List"),
     ("p", "Our clients include textile units, hospitals, educational institutions, dairies and housing societies "
           "across Maharashtra.")],
]


def cover(bidder: str, date: str, signatory: str) -> list[tuple]:
    return [
        ("spacer", 30),
        ("title", "TECHNICAL AND FINANCIAL BID"),
        ("spacer", 8),
        ("center", f"Bidder: {bidder}"),
        ("center", f"Tender No.: {TENDER_NO}"),
        ("center", f"Name of Work: {NAME_OF_WORK}"),
        ("center", "Submitted to: The Executive Engineer (Electrical), Pune Municipal Corporation"),
        ("center", f"Date: {date}"),
        ("spacer", 20),
        ("center", f"Authorised Signatory: {signatory}"),
    ]


def covering_letter(bidder: str) -> list[tuple]:
    return [
        ("h1", "Form A - Covering Letter"),
        ("p", f"We, {bidder}, hereby submit our bid for the above work. We have read and understood all terms and "
              "conditions of the tender document and accept them unconditionally."),
        ("p", "Our bid shall remain valid for 120 days from the date of opening of technical bids."),
    ]


def boq(rows: list[tuple[str, str]]) -> list[tuple]:
    return [
        ("h1", "Bill of Quantities"),
        ("table", [["Item", "Amount"]] + [[a, b] for a, b in rows]),
        ("p", "All rates are inclusive of GST and freight to site."),
    ]


def sunrise() -> list[list[tuple]]:
    name = "Sunrise Energy Systems Pvt Ltd"
    fixed = {
        1: cover(name, "03-02-2026", "Anil Kulkarni, Director"),
        2: covering_letter(name),
        3: [("h1", "Section 1 - Company Profile"),
            ("p", "Sunrise Energy Systems Pvt Ltd was incorporated in 2011 and is headquartered in Pune, Maharashtra. "
                  "The company designs and builds rooftop solar PV systems for industrial, institutional and municipal clients."),
            ("p", "CIN: U12345MH2020PTC123456. PAN: ABCDE1234F.")],
        4: [("h1", "Annexure B - Financial Statements"),
            ("p", "Our audited financial statements for FY2022-23 to FY2024-25 show an average annual turnover of Rs. 6,10,00,000/-."),
            ("p", "Turnover for FY 2022-23, FY 2023-24 and FY 2024-25 was Rs. 5.20 Crore, Rs. 6.05 Crore and Rs. 7.05 Crore respectively."),
            ("p", "Audited balance sheets signed by M/s Joshi and Associates, Chartered Accountants, are enclosed.")],
        5: [("h1", "Section 2 - Organisation and Resources"),
            ("p", "We employ 64 permanent staff, including design engineers, supervisors and trained technicians, "
                  "working from offices in Pune and Nashik.")],
        6: [("h1", "Section 3 - Past Projects"),
            ("p", "We completed the 500 kW rooftop solar installation for XYZ Textiles Ltd, Bhiwandi in 2023, valued at Rs. 2.3 Crore."),
            ("p", "We also commissioned a 120 kW rooftop solar system for Shree Hospital, Nashik in 2021, valued at Rs. 68 Lakh."),
            ("p", "Completion letters from both clients are enclosed in Annexure F.")],
        7: [("h1", "Section 4 - Commercial Proposal"),
            ("p", "Complete supply, installation and commissioning will be delivered within 75 days of work order issuance."),
            ("p", "We accept the payment terms specified in Clause 4.2 of the tender document without deviation.")],
        8: [("h1", "Section 5 - Technical Specifications"),
            ("p", "We offer a comprehensive warranty of 12 years on the complete system, covering panels, inverters and workmanship."),
            ("p", "Modules offered are 545 Wp mono-PERC conforming to IEC 61215 and IEC 61730.")],
        9: [("h1", "Section 6 - Technical Team"),
            ("p", "Mr. Rahul Deshmukh, Certified Electrical Engineer (License No. EE/2019/4521), will be deployed "
                  "full-time at the project site."),
            ("p", "He will be supported by two supervisors and a team of twelve trained technicians.")],
        22: [("h1", "Annexure C - Statutory Compliance"),
             ("p", "GST Registration Certificate enclosed as Annexure C, GSTIN 27ABCDE1234F1Z5."),
             ("p", "Legal name: Sunrise Energy Systems Private Limited. Principal place of business: Pune.")],
        23: [("h1", "Annexure D - Labour Registrations"),
             ("p", "EPF Registration No. MH/PUN/0098765 and ESIC Registration No. 33-00-112233 are enclosed as Annexure D.")],
        25: [("h1", "Annexure E - Manufacturer Authorization"),
             ("p", "Authorization letter from panel manufacturer Waaree Energies Ltd, dated 20-01-2026, is enclosed as Annexure E.")],
        27: boq([("Supply of PV modules and inverters", "Rs. 27,40,000/-"),
                 ("Mounting structures and balance of system", "Rs. 9,10,000/-"),
                 ("Installation, testing and commissioning", "Rs. 6,00,000/-")]),
        28: [("h1", "Financial Bid - Form B Price Schedule"),
             ("p", "Our total quoted price for the complete scope of work, inclusive of all taxes and duties, is "
                   "Rs. 42,50,000/- (Rupees Forty Two Lakh Fifty Thousand only).")],
    }
    return fill_pages(28, fixed, BIDDER_FILLER)


def greenvolt() -> list[list[tuple]]:
    name = "GreenVolt Infra Solutions"
    fixed = {
        1: cover(name, "04-02-2026", "Meera Patil, Managing Partner"),
        2: covering_letter(name),
        3: [("h1", "Part 1 - About GreenVolt"),
            ("p", "GreenVolt Infra Solutions is a partnership firm established in 2016 in Satara, Maharashtra, "
                  "executing rooftop solar and electrical infrastructure works.")],
        5: [("h1", "Annexure B - Financial Statements"),
            ("p", "Our average annual turnover for the last three financial years stands at Rs. 3,80,00,000/-."),
            ("p", "Audited statements for FY 2022-23 to FY 2024-25 are enclosed.")],
        6: [("h1", "Part 3 - Past Projects"),
            ("p", "We executed a 350 kW rooftop solar project for Deccan Sugar Mills, Baramati in 2022, worth Rs. 2.1 Crore."),
            ("p", "Work order and completion letter are enclosed.")],
        8: [("h1", "Part 4 - Commercial Proposal"),
            ("p", "Supply, installation and commissioning within 85 days of work order issuance.")],
        10: [("h1", "Part 5 - Technical Specifications"),
             ("p", "We provide a 10-year comprehensive warranty on all system components.")],
        11: [("h1", "Part 6 - Technical Team"),
             ("p", "A certified electrical engineer will be deployed at the site for the full execution period.")],
        20: [("h1", "Statutory Compliance"),
             ("p", "GST Registration Certificate No. 27ABCDE5678G1Z3 enclosed.")],
        21: [("h1", "Labour Registrations"),
             ("p", "EPF and ESIC registration certificates are enclosed as Annexure D.")],
        24: [("h1", "Technical Annexure"),
             ("p", "Authorization letter from panel manufacturer Tata Power Solar Systems Ltd is enclosed.")],
        25: boq([("Supply of PV modules and inverters", "Rs. 25,20,000/-"),
                 ("Mounting structures and balance of system", "Rs. 8,90,000/-"),
                 ("Installation, testing and commissioning", "Rs. 5,80,000/-")]),
        26: [("h1", "Financial Bid - Form B Price Schedule"),
             ("p", "Our total quoted price for the complete scope of work, inclusive of all taxes and duties, is "
                   "Rs. 39,90,000/- (Rupees Thirty Nine Lakh Ninety Thousand only).")],
    }
    return fill_pages(26, fixed, BIDDER_FILLER[3:] + BIDDER_FILLER[:3])


def urja() -> list[list[tuple]]:
    name = "Urja Solar Tech Pvt Ltd"
    fixed = {
        1: cover(name, "05-02-2026", "Sandeep Joshi, Director"),
        2: covering_letter(name),
        3: [("h1", "Chapter 1 - Company Overview"),
            ("p", "Urja Solar Tech Pvt Ltd, incorporated in 2014 and based in Kolhapur, specialises in commercial and "
                  "institutional rooftop solar systems.")],
        5: [("h1", "Annexure B - Financial Statements"),
            ("p", "Average annual turnover for FY2022-23 to FY2024-25 is Rs. 5,45,00,000/-."),
            ("p", "Audited balance sheets are enclosed for all three years.")],
        7: [("h1", "Chapter 3 - Past Projects"),
            ("p", "We completed a 400 kW rooftop solar installation for Konkan Dairy Ltd, Ratnagiri in 2024, valued at Rs. 2.4 Crore."),
            ("p", "The client's completion letter is enclosed.")],
        9: [("h1", "Chapter 5 - Technical Specifications"),
            ("p", "The proposed system shall carry a manufacturer warranty of 10 years covering panels and inverters.")],
        12: [("h1", "Chapter 4 - Commercial Proposal"),
             ("p", "Commissioning to be completed within 80 days of work order issuance.")],
        14: [("h1", "Chapter 6 - Technical Team"),
             ("p", "One certified electrical engineer will remain on-site throughout execution.")],
        19: [("h1", "Statutory Compliance"),
             ("p", "GST Registration Certificate enclosed, GSTIN 27ABCDE9012H1Z4.")],
        # Deliberately no ESIC registration anywhere in this submission.
        20: [("h1", "Labour Registration"),
             ("p", "EPF Registration No. MH/PUN/0076543 enclosed as Annexure D.")],
        23: [("h1", "Technical Annexure"),
             ("p", "Manufacturer authorization from Vikram Solar Ltd enclosed.")],
        # Deliberate contradiction with the 10-year figure on page 9.
        27: [("h1", "Commercial and BOQ Annexure"),
             ("p", "Warranty coverage under this offer: 7 years, extendable on request."),
             ("table", [["Item", "Amount"],
                        ["Supply of PV modules and inverters", "Rs. 28,60,000/-"],
                        ["Mounting structures and balance of system", "Rs. 9,70,000/-"],
                        ["Installation, testing and commissioning", "Rs. 6,50,000/-"]])],
        28: [("h1", "Financial Bid - Form B Price Schedule"),
             ("p", "Our total quoted price for the complete scope of work, inclusive of all taxes and duties, is "
                   "Rs. 44,80,000/- (Rupees Forty Four Lakh Eighty Thousand only).")],
    }
    return fill_pages(28, fixed, BIDDER_FILLER[6:] + BIDDER_FILLER[:6])


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_pdf(OUT_DIR / "PMC_SolarRooftop_Tender_2026.pdf", tender_pages(),
              f"Pune Municipal Corporation | Tender No. {TENDER_NO}")
    build_pdf(OUT_DIR / "sunrise_energy_technical_financial_bid.pdf", sunrise(),
              "Sunrise Energy Systems Pvt Ltd | Bid for Tender No. " + TENDER_NO)
    build_pdf(OUT_DIR / "greenvolt_infra_combined_bid.pdf", greenvolt(),
              "GreenVolt Infra Solutions | Bid for Tender No. " + TENDER_NO)
    build_pdf(OUT_DIR / "urja_solar_tech_combined_bid.pdf", urja(),
              "Urja Solar Tech Pvt Ltd | Bid for Tender No. " + TENDER_NO)
    for pdf in sorted(OUT_DIR.glob("*.pdf")):
        print(f"wrote {pdf.relative_to(OUT_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
