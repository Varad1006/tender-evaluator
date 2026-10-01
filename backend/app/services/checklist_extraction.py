"""Enumerated documentary requirements.

In real PMC tenders most submission requirements are numbered list items under an intro line:
  * conditions volumes: a CHECK LIST of the documents to be submitted with the tender
  * road volumes: a qualification clause (to qualify, the tenderer shall furnish the following)
    followed by numbered items, running onto the next page
  * an undertaking annexure whose numbered items "1) ... 2) ... 6)" sit inside one table cell
  * RFPs: a "Pre-Qualification Criteria" table (criterion | documentary evidence | file label)
The items themselves carry no "shall"/"must", so sentence-level rules never see them.

Edge cases handled: intros split over two lines, words run together in the text layer
("detailed outbelow"), lists continuing across pages, tables continuing after the intro page,
and inline "1) … 2) …" sub-items within a single cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.adapters.pdf_parser import ParsedDocument, Sentence

INTRO = re.compile(
    r"check\s*-?\s*list|documents?.{0,80}(?:should|shall|must)\s*be\s*(?:submitted|uploaded|enclosed)"
    r"|to\s*qualify.{0,160}(?:furnish|submit)|detailed\s*out\s*below|pre-?\s*qualification\s*criteria"
    r"|following\s*undertakings?|undertakings?.{0,60}stamp\s*paper|qualification\s*criteria",
    re.I,
)
ITEM = re.compile(r"^\(?(\d{1,2})[.)]?\s+([A-Za-z].{4,})$")
# Case-sensitive on purpose: an ALL-CAPS line is a new heading; an ordinary line is list continuation.
STOP = re.compile(r"^((?i:note)\s*[:\-(]|(?i:chapter)\b|\d{1,2}\s*\.\s*[A-Z][A-Z ]{6,}$|[A-Z][A-Z ]{8,}$)")
INLINE_ITEM = re.compile(r"(?:^|\s)(\d{1,2})\)\s+")
DOCUMENTARY = re.compile(
    r"certificate|copy|statement|document|details|list of|return|registration|deed|\bpan\b|\bgst\b|calculation|proof"
    r"|undertaking|affidavit|declaration|attorney|methodology|programme|program|earnest|turnover|experience|audit"
    r"|balance sheet|net worth|staff|professional|projects?|\biso\b|cmmi|licen[cs]e|pf\b|esic?|blacklist|debar|insolven"
    r"|registered|qualification|equipment|machinery|litigation|report|arbitration|relative|financial standing"
    r"|subcontract|monetary value|pan\s*card|similar\s*nature|work\s*of\s*similar",
    re.I,
)
LIST_WINDOW_PAGES = 6  # a table/list may continue this many pages after its intro
REJECT_INTRO = re.compile(r"(?:liable\s*to\s*be|shall\s*be|will\s*be)\s*rejected[^.]{0,40}\bif\b", re.I)
LETTER_ITEM = re.compile(r"^\(?([a-h])[.)]\s+(.{6,})$")


@dataclass
class ChecklistItem:
    page: int
    number: int
    text: str
    evidence: str | None = None
    form_ref: str | None = None
    group: str | None = None  # e.g. "Undertaking on ₹100 stamp paper"


def _label(text: str) -> str:
    cut = re.split(r"\.\s|;|\s+along with\b|\s+as stated\b|\s+duly\b|\s+if the\b|\s+for this\b", text, maxsplit=1)[0]
    cut = re.sub(r"^(the\s+)?(bidder|tenderer|company|contractor)\s+(shall|should|must|has to)\s+(have|be|upload|submit)\s+(a\s+)?", "",
                 cut, flags=re.I)
    cut = re.sub(r"^(undertaking\s+)?stating\s+that\s+", "", cut, flags=re.I)
    cut = re.sub(r"^\(?(?:[ivx]{1,4}|[a-h])\)\s*", "", cut, flags=re.I)  # "I)Maximum Turnover …", "b) …"
    cut = cut.strip(" .,:-")
    if len(cut) < 24:  # the split left a stub ("Self-Declaration to be"); keep more of the item
        cut = text.strip(" .,:-")
    cut = cut[0].upper() + cut[1:] if cut else cut
    return cut if len(cut) <= 90 else cut[:87].rsplit(" ", 1)[0] + "…"


def _same(a: str, b: str) -> bool:
    if SequenceMatcher(None, a, b).ratio() > 0.8:
        return True
    sa, sb = a.replace(" ", ""), b.replace(" ", "")
    short, long_ = sorted((sa, sb), key=len)
    return len(short) >= 24 and short in long_


_ENUM = re.compile(r"^\(?(?:\d{1,2}|[a-h]|[ivx]{1,4})[.)]?\s+", re.I)


def _claimed(text: str, claimed_texts: list[str]) -> bool:
    """The item is what a numeric rule already used. A long claimed sentence that merely contains
    the item (a whole run-on list read as one sentence) does not count."""
    for c in claimed_texts:
        c = _ENUM.sub("", c or "")  # "15 A certificate of one …" is the item "A certificate of one …"
        if len(c) <= 20:
            continue
        if c in text or (text in c and len(text) >= 0.5 * len(c)):
            return True
    return False


def _bucket(text: str) -> str:
    t = text.lower()
    if re.search(r"turnover|net worth|financial|balance sheet|profit|audit|bid capacity|solvency|income tax|monetary", t):
        return "Financial"
    if re.search(r"experience|similar|projects?|staff|professional|equipment|machinery|methodology|personnel|cmmi|\biso\b", t):
        return "Technical"
    return "Eligibility"


def _split_inline(text: str) -> list[str]:
    """'... following undertaking ... 1) Contractor has to ... 2) No arbitration ...' -> sub-items."""
    marks = list(INLINE_ITEM.finditer(text))
    numbers = [int(m.group(1)) for m in marks]
    if len(marks) < 2 or numbers[:2] != [1, 2]:
        return []
    parts = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        parts.append(text[m.end():end].strip(" .;"))
    return [p for p in parts if len(p) > 8]


def _intro_hit(text: str) -> bool:
    # "… as per the qualification criteria in clause 6" mentions the criteria; only a
    # line that ends on them ("Pre-Qualification Criteria:") introduces a list.
    # A wrapped line can end on the phrase mid-sentence, so bare "…criteria" counts only on a short
    # heading-like line.
    pattern = r"(?i)(?:pre-?\s*)?qualification\s*criteria(?!\s*[:\-–]\s*$)" if len(text) >= 60 else \
        r"(?i)(?:pre-?\s*)?qualification\s*criteria(?!\s*(?:[:\-–]\s*)?$)"
    return bool(INTRO.search(re.sub(pattern, " ", text)))


def _is_intro(line: str, nxt: str) -> bool:
    if re.search(r"\.{4,}|…{2,}", line):
        return False  # a table-of-contents entry
    if ITEM.match(line) and len(line) < 60:
        return False  # a numbered section heading that merely names the topic ("4) Pre-Qualification Criteria")
    return _intro_hit(line) or (_intro_hit(f"{line} {nxt}") and not ITEM.match(line))


def _from_lines(doc: ParsedDocument, readable: set[int]) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    active, expected, current, intro_page = False, 1, None, 0
    run_start = 0  # index in items where the current list began

    def close(drop_headings: bool = True):
        """End the list. A 'list' whose items are each the first line of successive pages is a run of
        chapter headings ("1) Glossary", "2) Invitation for Proposal", …), not requirements."""
        nonlocal active, current
        run = items[run_start:]
        if drop_headings and len(run) >= 2 and len({it.page for it in run}) == len(run) \
                and all(getattr(it, "_first_line", False) for it in run):
            del items[run_start:]
        active, current = False, None

    for page in doc.pages:
        if page.number not in readable:
            close()
            continue
        if active and page.number - intro_page > LIST_WINDOW_PAGES:
            close()
        # A list continues onto the next page, not across unrelated pages in between.
        if active and ((current is not None and page.number - current.page > 1)
                       or (current is None and page.number - intro_page > 1)):
            close()
        lines = page.lines
        for i, line in enumerate(lines):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if not active and _is_intro(line, nxt):
                active, expected, current, intro_page = True, 1, None, page.number
                run_start = len(items)
                continue
            if not active:
                continue
            if STOP.match(line) and not ITEM.match(line):
                close()
                continue
            m = ITEM.match(line)
            if m and int(m.group(1)) == expected and re.match(rf"{expected}\.\d", nxt):
                close()  # "2) Invitation for Proposal" followed by "2.1 RFP Notice": a chapter, not an item
                continue
            if m and int(m.group(1)) == expected:
                current = ChecklistItem(page.number, expected, m.group(2).strip())
                current._first_line = i == 0  # type: ignore[attr-defined]
                items.append(current)
                expected += 1
            elif m and int(m.group(1)) == 1 and expected > 2:
                # numbering restarted: a new list without an intro of its own is not ours
                close()
            elif current is not None and not m and len(current.text) < 600:
                current.text += " " + line
    close()
    return items


def _rejection_grounds(doc: ParsedDocument, readable: set[int]) -> list[ChecklistItem]:
    """A clause listing the grounds on which a tender is rejected outright (a) … b) …) — explicit gating rules."""
    items: list[ChecklistItem] = []
    for page in doc.pages:
        if page.number not in readable:
            continue
        active, expected, current = False, "a", None
        for line in page.lines:
            if REJECT_INTRO.search(line):
                active, expected, current = True, "a", None
                continue
            if not active:
                continue
            m = LETTER_ITEM.match(line)
            if m and m.group(1) == expected:
                current = ChecklistItem(page.number, ord(expected) - 96, m.group(2).strip(), group="Rejection ground")
                items.append(current)
                expected = chr(ord(expected) + 1)
            elif m or ITEM.match(line) or STOP.match(line):
                active, current = False, None
            elif current is not None and len(current.text) < 300:
                current.text += " " + line
    return items


CLAUSE_BOX = re.compile(r"^[A-Z][A-Z&/ ]{8,}\s+(?:\d\)\s*)?[A-Z][a-z]")
YEAR_CELL = re.compile(r"^(?:FY\s*)?20\d\d\s*[-–/]\s*\d{2,4}$|^(?:Year|FY)\s*\d?$", re.I)


def _form_or_data_sheet(rows: list[list[str]]) -> bool:
    """Tables with numbered rows that are not requirement lists: a financial-capability format to
    fill in (year columns: '2018-19 | 2019-20 | 2020-21'), or a data sheet of key/value facts
    ('3 | Tender Fee | Rs.1000/-', '6 | Place, time of opening | as per the portal …')."""
    if any(sum(1 for c in r if YEAR_CELL.match(c.strip())) >= 2 for r in rows[:3]):
        return True
    numbered = [[c for c in r if c] for r in rows]
    numbered = [c for c in numbered if len(c) >= 3 and re.fullmatch(r"\d{1,2}\.?", c[0])]
    if len(numbered) < 3:
        return False
    value_like = sum(1 for c in numbered if len(c[2]) < 45 or re.search(r"https?://|www\.", c[2])
                     or re.fullmatch(r"[\d.,\s]+", c[2]))
    return value_like >= 0.6 * len(numbered)


def _from_tables(doc: ParsedDocument, readable: set[int]) -> list[ChecklistItem]:
    items: list[ChecklistItem] = []
    intro_page = -100
    for page in doc.pages:
        if page.number not in readable:
            continue
        if INTRO.search(page.text) or any(INTRO.search(h) for h in page.headings):
            intro_page = page.number
        if page.number - intro_page > LIST_WINDOW_PAGES or not page.tables:
            continue
        for t, rows in enumerate(page.tables):
            header = " ".join(c for r in rows[:2] for c in r if len(c) < 40).lower()
            if re.search(r"type of equipment|equipment|machinery", header):
                continue  # plant & machinery lists feed the machinery criterion, not the document checklist
            if _form_or_data_sheet(rows):
                continue
            # A row split by the page break: the first row of the page's first table has no number
            # and only continuation text ("quality within the time period.").
            if t == 0 and rows and items and items[-1].page == page.number - 1:
                first = [c for c in rows[0] if c]
                if len(first) == 1 and not rows[0][0] and len(first[0]) < 300:
                    items[-1].text = f"{items[-1].text.rstrip()} {first[0]}"
            for row in rows:
                cells = [c for c in row if c]
                if len(cells) < 2 or not re.fullmatch(r"\d{1,2}\.?", cells[0]):
                    continue
                number = int(cells[0].rstrip("."))
                text = cells[1]
                if CLAUSE_BOX.match(text):
                    continue  # a boxed conditions clause ("AWARD CRITERIA …")
                rest = cells[2:]
                form_ref = next((c for c in rest if re.fullmatch(r"[A-Z]{2}_\d+", c)), None)
                evidence = next((c for c in rest if not re.fullmatch(r"[A-Z]{2}_\d+", c)), None)
                subs = _split_inline(evidence or "") if evidence else []
                if subs and re.search(r"undertaking|declar|affidavit", text + " " + (evidence or ""), re.I):
                    stamp = "₹100 stamp paper, notarised" if re.search(r"stamp\s*paper", evidence or "", re.I) else None
                    for k, sub in enumerate(subs, 1):
                        items.append(ChecklistItem(page.number, number, sub, group=f"{text}" + (f" ({stamp})" if stamp else ""),
                                                   form_ref=f"{number}.{k}"))
                    continue
                items.append(ChecklistItem(page.number, number, text, evidence, form_ref))
    return items


def extract_checklist(doc: ParsedDocument, claimed_texts: list[str]):
    """Yields (label, bucket, sentence, params) for documentary requirements not already covered
    by a numeric rule (claimed_texts are the sentences those rules used)."""
    readable = set(doc.readable_pages)
    seen: set[str] = set()
    seen_texts: list[str] = []
    for item in _from_tables(doc, readable) + _from_lines(doc, readable) + _rejection_grounds(doc, readable):
        text = re.sub(r"\s+", " ", item.text).strip()
        if not (DOCUMENTARY.search(text) or item.group) or len(text) < 5:
            continue
        if _claimed(text, claimed_texts):
            continue
        label = _label(text)
        if item.group == "Rejection ground":
            label = _label("Rejected if: " + text[0].lower() + text[1:])
        elif item.group and re.search(r"undertaking", item.group, re.I):
            label = _label("Undertaking: " + re.sub(r"^(contractor\s+has\s+to\s+upload\s+(a\s+)?undertaking\s+stating\s+that\s+)", "", text, flags=re.I))
        slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]
        norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        # A summary-table row ("Staff" | "List of Technical Personnel … (Statement IV)") restates a
        # requirement through its evidence column: compare that too.
        forms = [norm] + ([re.sub(r"[^a-z0-9]+", " ", item.evidence.lower()).strip()]
                          if item.evidence and len(text) < 40 else [])
        # The same requirement is often repeated (annexure + envelope summary) with small wording
        # changes, or cut short by a cell boundary (a word fused to the next cell's first word).
        if slug in seen or any(_same(f, t) for f in forms for t in seen_texts):
            continue
        seen.add(slug)
        seen_texts.extend(forms)
        if item.evidence and len(item.evidence) >= 24:  # a later list that restates this row's evidence is the same item
            seen_texts.append(re.sub(r"[^a-z0-9]+", " ", item.evidence.lower()).strip())
        terms = [w for w in re.findall(r"[A-Za-z]{3,}", label) if w.lower() not in
                 {"the", "and", "copy", "valid", "attested", "details", "list", "with", "for", "from", "under", "last", "this",
                  "undertaking", "that", "his", "firm", "has", "not", "been"}][:3]
        yield label, _bucket(text), Sentence(page=item.page, text=text, section=item.group or "Checklist",
                                             clause=str(item.form_ref or item.number)), {
            "terms": terms, "evidence": item.evidence, "form_ref": item.form_ref, "checklist_no": item.number,
            "group": item.group,
        }
