"""
Regression: ICICI **Equity Transaction Statement** PDF table parse (not NSE trade mailers).

Sample PDFs under ``data/samples/icici_direct_equity/`` are gitignored; tests skip if missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SAMPLE_ANNUAL = (
    REPO
    / "data"
    / "samples"
    / "icici_direct_equity"
    / "decrypted_19d66e33a721_Equity_Transaction_Statement_from_01-Apr-2025_to_31-Mar-2026_TRX-Equity_04-04-2026_1828171.pdf"
)


@pytest.mark.skipif(not SAMPLE_ANNUAL.is_file(), reason="sample equity statement PDF not present")
def test_annual_statement_last_grid_row_is_bharti_airtel_resolved() -> None:
    """Last trade row on the statement grid must parse (Bharti Airtel, INE397D01024 → BHARTIARTL)."""
    from parsers.holdings.icici_direct_equity_statement_pdf import (
        parse_icici_direct_equity_statement_pdf,
    )

    agg = parse_icici_direct_equity_statement_pdf(SAMPLE_ANNUAL)
    bh = [t for t in agg if t.symbol == "BHARTIARTL" and t.txn_date.isoformat() == "2026-03-30"]
    assert len(bh) == 1
    t = bh[0]
    assert t.txn_type == "BUY"
    assert t.quantity == 18.0
    assert t.total_amount == 32724.0
    assert (t.metadata or {}).get("isin") == "INE397D01024"
