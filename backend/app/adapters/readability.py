"""Page readability classification.

Real PMC tender bundles contain four kinds of page (observed across the MahaTender corpus):

  english  - a genuine English text layer; rules can read it
  marathi  - a Unicode Devanagari text layer. pdfplumber extracts it lossily (conjuncts and
             matras are dropped or reordered), so it is good for locating a clause (Devanagari
             digits such as ४०% survive) but never for quoting it
  garbled  - a text layer that exists but is nonsense: a bad OCR layer on a scan, or Marathi
             typed in a legacy ASCII-mapped font ("Q|Sa Jmofdmam" = "टेंडर गोषवारा")
  image    - no text layer at all (scanned / flattened)

A "has text" check is not enough: garbled pages pass it and would silently yield no
requirements. Anything not `english` is reported as not machine-readable so the committee
knows what the system could not read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMMON_WORDS = frozenset("""
the of and to in a is be for shall on by with as or any this that are will from at not an all
work works tender contractor bidder cost pmc per rs date period no which under such other its
have has been may should must it if than time amount estimated deposit security earnest money
name corporation pune municipal department engineer details form item quantity rate total cubic
metre providing laying complete bid bids submitted submit documents certificate years year last
""".split())

_WEIRD = re.compile(r"[{}|~`$_^\\<>]")
_CID = re.compile(r"\(cid:\d+\)")
_ODD_TOKEN = re.compile(r"[A-Za-z][^A-Za-z\s.,:;()'\-/0-9%&][A-Za-z]")
_DEV = re.compile(r"[ऀ-ॿ]")
_LAT = re.compile(r"[A-Za-z]")

ENGLISH_KINDS = {"english", "ocr_en"}  # English rules can run on these
QUOTABLE_KINDS = {"english", "ocr_en", "ocr_mr"}  # text we are willing to show as a verbatim quote
NEEDS_OCR = {"image", "garbled", "marathi"}  # local text is missing, junk, or lossy Devanagari


# Some scans come back with a space between almost every letter — "W O R K C O M PL E T I O N
# CERTI F I CA TE", "Ac c ept ed t en der Am oun t". Every word-level check above passes on such a
# page, and its numbers come out as "2t7.t2" for 217.12, so it reads as English and yields
# nonsense. Measured across bidder submissions and tender volumes the two populations do not
# overlap: damaged pages sit at 0.40 and every clean page below 0.10.
_SPACED_OUT = 0.20


@dataclass(frozen=True)
class PageQuality:
    kind: str  # english | marathi | garbled | image | blank | ocr_en | ocr_mr
    common_ratio: float = 0.0
    weird_ratio: float = 0.0
    odd_ratio: float = 0.0
    spaced_ratio: float = 0.0

    @property
    def machine_readable(self) -> bool:
        return self.kind in ENGLISH_KINDS

    @property
    def quotable(self) -> bool:
        return self.kind in QUOTABLE_KINDS


def classify_page(text: str) -> PageQuality:
    t = (text or "").strip()
    if len(t) < 40:
        return PageQuality("image")
    tokens = t.split()
    dev, lat = len(_DEV.findall(t)), len(_LAT.findall(t))
    words = [w.lower().strip(".,:;()[]'\"") for w in tokens]
    common = sum(1 for w in words if w in COMMON_WORDS) / max(1, len(words))
    weird = (len(_WEIRD.findall(t)) + 3 * len(_CID.findall(t))) / max(1, len(t))
    odd = sum(1 for w in tokens if _ODD_TOKEN.search(w)) / max(1, len(tokens))
    letters = re.findall(r"[A-Za-z]+", t)
    spaced = sum(1 for w in letters if len(w) == 1) / len(letters) if len(letters) >= 25 else 0.0
    if dev > 80 and dev > 0.6 * lat:
        return PageQuality("marathi", common, weird, odd, spaced)
    if lat > 50 and common >= 0.08 and weird < 0.02 and odd < 0.12 and spaced < _SPACED_OUT:
        return PageQuality("english", common, weird, odd, spaced)
    return PageQuality("garbled", common, weird, odd, spaced)
