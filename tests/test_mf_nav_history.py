"""Historical MF NAV parsing and fetch (HTTP mocked)."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest

from api.models import Price
from api.services.mf_nav_history import (
    fetch_mf_nav_history,
    mfapi_nav_to_prices_in_range,
    parse_amfi_nav_history_report,
    parse_amfi_nav_history_report_line,
    parse_mfapi_history_payload,
)


def test_parse_mfapi_history_payload_dd_mm_yyyy() -> None:
    payload = {
        "data": [
            ["15-03-2025", "12.3456"],
            ["bad-date", "10"],
            ["10-01-2025", "not-a-float"],
            ["01-01-2025", "100"],
            ["20-06-2024", "200"],
        ]
    }
    pairs = parse_mfapi_history_payload(payload)
    # ``01-01-2025`` is DD-MM-YYYY → 1 Jan, not 10 Jan.
    assert pairs == [
        (datetime.date(2025, 3, 15), 12.3456),
        (datetime.date(2025, 1, 1), 100.0),
        (datetime.date(2024, 6, 20), 200.0),
    ]


def test_mfapi_nav_to_prices_in_range_filters() -> None:
    pairs = [
        (datetime.date(2024, 1, 1), 10.0),
        (datetime.date(2024, 6, 15), 11.0),
        (datetime.date(2025, 1, 1), 12.0),
    ]
    rows = mfapi_nav_to_prices_in_range(
        "118551",
        pairs,
        datetime.date(2024, 6, 1),
        datetime.date(2024, 12, 31),
    )
    assert len(rows) == 1
    assert rows[0].symbol == "118551"
    assert rows[0].date == datetime.date(2024, 6, 15)
    assert rows[0].close_price == pytest.approx(11.0)
    assert rows[0].source == "mfapi"


def test_parse_amfi_nav_history_report_line() -> None:
    want = frozenset({"103024"})
    line = "103024;SBI LARGE & MIDCAP FUND- REGULAR PLAN -Growth;INF200K01305;;538.9405;;;03-Mar-2025"
    p = parse_amfi_nav_history_report_line(line, want)
    assert p is not None
    assert p.symbol == "103024"
    assert p.date == datetime.date(2025, 3, 3)
    assert p.close_price == pytest.approx(538.9405)
    assert p.source == "amfi_portal"


def test_parse_amfi_nav_history_report_filters_range() -> None:
    text = (
        "Open Ended Schemes ( Equity )\n"
        "103024;SBI X;INF200K01305;;100.0;;;01-Mar-2025\n"
        "103024;SBI X;INF200K01305;;101.0;;;15-Mar-2025\n"
    )
    rows = parse_amfi_nav_history_report(
        text,
        frozenset({"103024"}),
        datetime.date(2025, 3, 5),
        datetime.date(2025, 3, 20),
    )
    assert len(rows) == 1
    assert rows[0].date == datetime.date(2025, 3, 15)


@patch("api.services.mf_nav_history.fetch_mf_nav_histories_amfi_portal", return_value=[])
def test_fetch_mf_nav_history_with_injected_client(_mock_amfi) -> None:
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {
        "data": [
            ["05-06-2024", "250.25"],
            ["04-06-2024", "249.00"],
        ]
    }
    resp.raise_for_status = MagicMock()
    client.get.return_value = resp

    rows = fetch_mf_nav_history(
        "118551",
        datetime.date(2024, 6, 4),
        datetime.date(2024, 6, 5),
        client=client,
    )
    assert len(rows) == 2
    assert all(isinstance(r, Price) for r in rows)
    sym_dates = {(r.symbol, r.date) for r in rows}
    assert sym_dates == {
        ("118551", datetime.date(2024, 6, 4)),
        ("118551", datetime.date(2024, 6, 5)),
    }
    client.get.assert_called_once()
