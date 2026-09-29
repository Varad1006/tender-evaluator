from app.services.values import format_inr, format_inr_short, parse_amounts, parse_durations, parse_years


def test_indian_currency_forms():
    assert [a.value for a in parse_amounts("not less than Rs. 5,00,00,000/- (Rupees Five Crore only)")] == [50_000_000]
    assert [a.value for a in parse_amounts("valued at Rs. 2.3 Crore")] == [23_000_000]
    assert [a.value for a in parse_amounts("worth INR 68 Lakh")] == [6_800_000]
    assert [a.value for a in parse_amounts("₹42,50,000")] == [4_250_000]
    assert [a.value for a in parse_amounts("a 3.8 Cr. order")] == []  # no currency marker and "Cr" alone is ambiguous
    assert [a.value for a in parse_amounts("Rs 3.8 Cr. order")] == [38_000_000]
    assert parse_amounts("500 kW rooftop system, 120 days") == []


def test_durations_and_years():
    assert [(d.value, d.unit) for d in parse_durations("a 10-year comprehensive warranty")] == [(10, "years")]
    assert [d.value for d in parse_durations("within the last five years", unit="years")] == [5]
    assert [d.value for d in parse_durations("completed within 90 days of issue", unit="days")] == [90]
    assert parse_durations("none years of anything") == []
    assert parse_years("in 2023, valued at Rs. 2,00,00,000") == [2023]


def test_formatting():
    assert format_inr(50_000_000) == "₹5,00,00,000"
    assert format_inr(4_250_000) == "₹42,50,000"
    assert format_inr_short(23_000_000) == "₹2.3 Cr"
    assert format_inr_short(6_800_000) == "₹68 L"
