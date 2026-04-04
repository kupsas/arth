"""Tests for :mod:`api.services.nse_ticker_resolve` (mocked — no live NSE)."""

from __future__ import annotations

from unittest.mock import patch

from api.services.nse_ticker_resolve import first_valid_ticker


@patch("api.services.nse_ticker_resolve.ticker_valid_for_nse_equity")
def test_first_valid_ticker_skips_invalid(mock_valid: object) -> None:
    mock_valid.side_effect = [False, False, True]
    out = first_valid_ticker(["AAA", "BBB", "TCS"])
    assert out == "TCS"
    assert mock_valid.call_count == 3


@patch("api.services.nse_ticker_resolve.ticker_valid_for_nse_equity", return_value=False)
def test_first_valid_ticker_returns_none_when_all_invalid(_mock: object) -> None:
    assert first_valid_ticker(["X", "Y"]) is None
