"""Deterministic value parsing for Indian tender language (Rs./INR/₹, Lakh/Crore, number words)."""

from __future__ import annotations

import re
from dataclasses import dataclass

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}

MULTIPLIERS = {"crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
               "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
               "million": 1_000_000,
               # Marathi. A PMC works tender states its money in these and in Devanagari digits:
               # "र.रु.१.५० कोटी", "रक्कम रुपये २५.०० लाख", "निविदाधीन रक्कमेच्या १%".
               "कोटी": 10_000_000, "कोटि": 10_000_000,
               "लाख": 100_000, "लक्ष": 100_000, "हजार": 1_000}

# Bidder submissions are scans, and their text layers break Indian digit grouping in two ways.
#
#   "Rs. 1,87 ,87 ,157/-"   a group separated by stray spaces. The spaces carry no information,
#                           so they are removed. Matching only "1,87" here returned ₹187 for a
#                           ₹1.87 crore turnover — a hundred-thousand-fold error, in the direction
#                           that disqualifies a bidder (Vimal Enterprises' CA certificate, p.7).
#   "Rs. 7O,62,407/-"       a letter standing in for a digit. This is NOT repaired: "O" is a 0
#                           here and a 6 elsewhere, and inventing the digit would put a number the
#                           document never states under a page citation. The amount is refused,
#                           which costs a lookup; guessing it costs a wrong verdict.
_GROUPED = r"\d[\d,]*(?:\s*,\s*\d[\d,]*)*(?:\s*\.\d+)?"

# "रु.", "र.रु.", "रुपये" and "₹" are the same marker as "Rs."; a Marathi tender uses them
# interchangeably and often with no space before the figure ("र.रु.१.५० कोटी").
# The optional "र." prefix has to require its stop: written as `र\.?` it eats the र of रु itself,
# and "रु. १,२३,४५,६७८/-" then matches nothing at all.
_MARKER = r"(?:rs\.?|inr|₹|(?:र\.\s*)?रुपये|(?:र\.\s*)?रु\.?)"
_MULT = r"crores?|cr\b\.?|lakhs?|lacs?|million|कोटी|कोटि|लाख|लक्ष|हजार"
_CURRENCY = re.compile(
    r"(?:" + _MARKER + r"\s*(?P<a>" + _GROUPED + r")(?:\s*/-)?\s*(?P<am>" + _MULT + r")?"
    r"|(?P<b>" + _GROUPED + r")\s*(?P<bm>crores?|lakhs?|lacs?|कोटी|कोटि|लाख|लक्ष)\b)",
    re.IGNORECASE,
)

# A digit-shaped letter that continues the number the match just stopped on: the value is cut short.
_TRUNCATED_BY_LETTER = re.compile(r"^\s*[,.]?\s*[OoIlSsZzBbGgq]\s*[\d,]")

# Marathi inflects the unit rather than adding a separate word: वर्ष, वर्षे, वर्षात, वर्षातील,
# वर्षांसाठी, वर्षांकरिता are all "years", so the unit is matched by its stem and whatever follows.
_DURATION = re.compile(
    r"\(?\b(?P<n>\d+|" + "|".join(NUMBER_WORDS) + r")\)?\s*(?:-\s*)?"
    # "गेल्या ५ (पाच) वर्षातील", "5 (five) years": the figure is repeated in words in brackets.
    r"(?:\([^)]{1,20}\)\s*)?"
    r"(?P<unit>years?\b|yrs?\b|days?\b|months?\b|वर्ष\S*|वर्षां\S*|महिन\S*|महीन\S*|दिवस\S*)",
    re.IGNORECASE,
)
_MR_YEAR = re.compile(r"^(?:वर्ष|वर्षां)")
_MR_MONTH = re.compile(r"^(?:महिन|महीन)")

_YEAR = re.compile(r"\b(20[0-4]\d|19[89]\d)\b")

# "2020-2021", "2020-21", "FY 2024-25", "F.Y.2019-20". The two halves must be consecutive years,
# which is what separates a financial year from a range, a clause number or an OCR mangling
# ("2O2O-27" and "2027-22" both appear in one bidder's turnover certificate and are not years).
_FY = re.compile(r"\b(?:F\.?\s?Y\.?\s*)?(?P<y1>(?:19|20)\d{2})\s*[-–—/]\s*(?P<y2>(?:19|20)\d{2}|\d{2})\b")

GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")


@dataclass(frozen=True)
class Amount:
    value: float
    raw: str


@dataclass(frozen=True)
class Duration:
    value: int
    unit: str  # "years" | "days" | "months"
    raw: str


_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def ascii_digits(text: str) -> str:
    """Marathi documents print numbers in Devanagari digits ("१२,३४,५६७/-", "सन २०२४-२५"). Parse on
    this copy; quote the original, which is what the page says."""
    return (text or "").translate(_DEVANAGARI_DIGITS)


def _scan_amounts(text: str) -> tuple[list[Amount], list[str]]:
    """(amounts read, fragments refused as corrupt).

    Scanned on a copy with Devanagari digits folded to ASCII, and quoted from the original. The
    translation is one character for one character, so every offset the scan reports is an offset
    into the text the page actually prints — the committee is shown "१,२३,४५,६७८", not "1,23,45,678".
    """
    out: list[Amount] = []
    refused: list[str] = []
    scan = ascii_digits(text)
    for m in _CURRENCY.finditer(scan):
        digits = m.group("a") or m.group("b")
        group = "a" if m.group("a") else "b"
        mult_word = (m.group("am") or m.group("bm") or "").lower().rstrip(".")
        if _TRUNCATED_BY_LETTER.match(scan[m.end(group): m.end(group) + 4]):
            refused.append(text[m.start(): m.end(group) + 8].strip())
            continue
        # A bare "2,000 days" style number without a currency marker or multiplier is not money.
        try:
            number = float(re.sub(r"[\s,]", "", digits))
        except ValueError:
            continue
        multiplier = MULTIPLIERS.get(mult_word, 1)
        out.append(Amount(value=number * multiplier, raw=text[m.start(): m.end()].strip()))
    return out, refused


def parse_amounts(text: str) -> list[Amount]:
    return _scan_amounts(text)[0]


# A comma-grouped token whose groups are not all numeric: "L,26,40 ,423" for 1,26,40,423, and
# "2, L7 ,12,07 4" for 2,17,12,074. Commas are what make this safe to look for — a PAN, a UDIN and
# a firm registration number all mix letters and digits, and none of them is comma-grouped. Three
# groups are required rather than two: "Flat No. 402, D Wing" and "September 17, 2025" have one
# comma each, and at lakh scale and above an Indian amount always has at least two.
_GROUPED_TOKEN = re.compile(r"(?<![\w,])([0-9A-Za-z]{1,3}(?:\s*,\s*[0-9A-Za-z]{1,4}){2,5})(?!\w)")
_HAS_DIGIT = re.compile(r"\d")
_HAS_ALPHA = re.compile(r"[A-Za-z]")


def mangled_numbers(text: str) -> list[str]:
    """Grouped numbers with a letter standing where a digit belongs.

    These never reach `parse_amounts` at all — a figure that begins with the letter matches no
    currency pattern — so a page can look clean while its largest numbers are missing entirely.
    """
    out = []
    for m in _GROUPED_TOKEN.finditer(text):
        token = m.group(1)
        if _HAS_DIGIT.search(token) and _HAS_ALPHA.search(token):
            out.append(token.strip())
    return out


def corrupt_amounts(text: str) -> list[str]:
    """Fragments that state money the text layer has mangled beyond safe reading.

    Surfaced so the page can be sent to OCR and the committee told what was not read, instead of a
    truncated figure being shown as if it were the bidder's.
    """
    return _scan_amounts(text)[1] + mangled_numbers(text)


def parse_durations(text: str, unit: str | None = None) -> list[Duration]:
    """Scanned with Devanagari digits folded to ASCII; quoted from the original, as amounts are."""
    out = []
    scan = ascii_digits(text)
    for m in _DURATION.finditer(scan):
        n = m.group("n").lower()
        value = NUMBER_WORDS.get(n) if not n.isdigit() else int(n)
        if value is None:
            continue
        u = m.group("unit").lower()
        if u.startswith(("year", "yr")) or _MR_YEAR.match(u):
            norm = "years"
        elif u.startswith("day") or u.startswith("दिवस"):
            norm = "days"
        elif u.startswith("month") or _MR_MONTH.match(u):
            norm = "months"
        else:
            continue
        if unit and norm != unit:
            continue
        out.append(Duration(value=value, unit=norm, raw=text[m.start(): m.end()].strip()))
    return out


def parse_years(text: str) -> list[int]:
    return [int(y) for y in _YEAR.findall(ascii_digits(text))]


def fy_start(y1: str | int, y2: str | int) -> int | None:
    """The year a financial year begins in, or None when the pair is not a financial year.

    '2020-2021' and '2020-21' both start in 2020. '2027-22' does not start anywhere: a scan's text
    layer turned 2021-22 into it, and reading it as a year would file a turnover under a year the
    certificate never states.
    """
    start = int(y1)
    s2 = str(y2)
    end = int(s2) if len(s2) == 4 else int(str(start)[: 4 - len(s2)] + s2)
    return start if end == start + 1 else None


def parse_financial_years(text: str) -> list[int]:
    """Every financial-year start in the text, in order, ignoring pairs that are not consecutive."""
    out = []
    for m in _FY.finditer(ascii_digits(text)):
        start = fy_start(m.group("y1"), m.group("y2"))
        if start is not None:
            out.append(start)
    return out


def format_inr(value: float) -> str:
    """Indian digit grouping: 50000000 -> '₹5,00,00,000'."""
    whole = int(round(value))
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"₹{s}"


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
           "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000}


def words_to_amount(text: str) -> float | None:
    """Indian-system amount in words -> number.

    "Four Lakhs Twenty Three Thousand Six Hundred Forty Seven Rupees" -> 423647
    "One Crore Eighty Lakhs ... Six Rupees and Twenty Five Paise"      -> 18049886.25
    Returns None when the text contains no number words.
    """
    m = re.search(r"\band\s+([a-z\s]+?)\s+paise\b", text, re.I)
    paise_part = m.group(1) if m else ""
    rupee_part = text[: m.start()] + text[m.end():] if m else text

    def parse(chunk: str) -> float | None:
        total, current, seen = 0.0, 0.0, False
        for word in re.findall(r"[a-z]+", chunk.lower()):
            if word in _UNITS:
                current += _UNITS[word]
                seen = True
            elif word == "hundred":
                current = (current or 1) * 100
                seen = True
            elif word in _SCALES:
                total += (current or 1) * _SCALES[word]
                current = 0
                seen = True
        return total + current if seen else None

    rupees = parse(rupee_part)
    if rupees is None:
        return None
    paise = parse(paise_part) if paise_part else None
    return rupees + (paise or 0) / 100


def _trim(number: float) -> str:
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_inr_short(value: float) -> str:
    """Compact form for grids: 23000000 -> '₹2.3 Cr', 6800000 -> '₹68 L'."""
    if value >= 10_000_000:
        return f"₹{_trim(value / 10_000_000)} Cr"
    if value >= 100_000:
        return f"₹{_trim(value / 100_000)} L"
    return format_inr(value)
