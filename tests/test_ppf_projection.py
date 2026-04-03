"""PPF maturity balance illustration + Wikipedia rate parsing."""

from __future__ import annotations

import datetime

import pytest

from api.services.ppf_projection import ppf_projected_balance_at_maturity
from api.services.ppf_reference_rate import _parse_rate_from_extract, clear_ppf_rate_cache_for_tests


def test_parse_wikipedia_rate_sentence() -> None:
    blob = (
        "Something something. The current interest rate is 7.1% annually "
        "(Q1 of FY 2025-26). More text."
    )
    assert _parse_rate_from_extract(blob) == pytest.approx(7.1)


def test_parse_rate_returns_none_when_missing() -> None:
    assert _parse_rate_from_extract("No rate here") is None


def test_projected_balance_fractional_years() -> None:
    today = datetime.date(2026, 3, 29)
    maturity = datetime.date(2036, 3, 31)
    fv = ppf_projected_balance_at_maturity(
        balance_today=100_000.0,
        maturity_date=maturity,
        today=today,
        annual_rate_percent=7.1,
    )
    assert fv is not None
    days = (maturity - today).days
    years = days / 365.25
    expected = round(100_000.0 * (1.0 + 0.071) ** years, 0)
    assert fv == expected


def test_projected_balance_past_maturity_is_today_balance() -> None:
    today = datetime.date(2026, 1, 1)
    maturity = datetime.date(2020, 1, 1)
    assert ppf_projected_balance_at_maturity(
        balance_today=55_000.0,
        maturity_date=maturity,
        today=today,
        annual_rate_percent=7.1,
    ) == pytest.approx(55_000.0)


@pytest.fixture(autouse=True)
def _clear_ppf_rate_cache() -> None:
    clear_ppf_rate_cache_for_tests()
    yield
    clear_ppf_rate_cache_for_tests()
