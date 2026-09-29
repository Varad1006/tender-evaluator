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
               "million": 1_000_000}

_CURRENCY = re.compile(
    r"(?:(?:rs\.?|inr|₹)\s*(?P<a>\d[\d,]*(?:\.\d+)?)(?:\s*/-)?\s*(?P<am>crores?|cr\b\.?|lakhs?|lacs?|million)?"
    r"|(?P<b>\d[\d,]*(?:\.\d+)?)\s*(?P<bm>crores?|lakhs?|lacs?)\b)",
    re.IGNORECASE,
)

_DURATION = re.compile(
    r"\(?\b(?P<n>\d+|" + "|".join(NUMBER_WORDS) + r")\)?\s*(?:-\s*)?(?P<unit>years?|yrs?|days?|months?)\b",
    re.IGNORECASE,
)

_YEAR = re.compile(r"\b(20[0-4]\d|19[89]\d)\b")

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


def parse_amounts(text: str) -> list[Amount]:
    out = []
    for m in _CURRENCY.finditer(text):
        digits = m.group("a") or m.group("b")
        mult_word = (m.group("am") or m.group("bm") or "").lower().rstrip(".")
        # A bare "2,000 days" style number without a currency marker or multiplier is not money.
        try:
            number = float(digits.replace(",", ""))
        except ValueError:
            continue
        multiplier = MULTIPLIERS.get(mult_word, 1)
        out.append(Amount(value=number * multiplier, raw=m.group(0).strip()))
    return out


def parse_durations(text: str, unit: str | None = None) -> list[Duration]:
    out = []
    for m in _DURATION.finditer(text):
        n = m.group("n").lower()
        value = NUMBER_WORDS.get(n) if not n.isdigit() else int(n)
        u = m.group("unit").lower()
        norm = "years" if u.startswith(("year", "yr")) else "days" if u.startswith("day") else "months"
        if unit and norm != unit:
            continue
        out.append(Duration(value=value, unit=norm, raw=m.group(0).strip()))
    return out


def parse_years(text: str) -> list[int]:
    return [int(y) for y in _YEAR.findall(text)]


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
