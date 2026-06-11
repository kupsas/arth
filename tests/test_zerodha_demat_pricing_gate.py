"""Market-price backfill runs only on the Gmail demat email path."""

from __future__ import annotations

from unittest.mock import patch

from parsers.holdings.zerodha_demat_statement_pdf import parse_statement_of_account_text


@patch(
    "parsers.holdings.zerodha_demat_statement_pdf.apply_market_prices_to_zerodha_demat_txns",
)
def test_parse_pdf_does_not_price_by_default(mock_price: object) -> None:
    from parsers.holdings.zerodha_demat_statement_pdf import parse_zerodha_demat_statement_pdf
    import tempfile
    from pathlib import Path

    # Minimal PDF path won't work without pdfplumber content — test via text path instead.
    text = """
Statement of Account from 2026-04-01 to 2026-04-30:
ISIN: INE736A01011 Symbol: CDSL
2026-04-05 Delivery In 0 1.000 1.000
Holdings as on 2026-04-30:
"""
    txns = parse_statement_of_account_text(text)
    assert txns
    mock_price.assert_not_called()

    # Email path flag would call pricing — exercised in zerodha_demat.py; gate is on parse_zerodha_demat_statement_pdf.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with patch(
            "parsers.holdings.zerodha_demat_statement_pdf._extract_statement_text",
            return_value=text,
        ):
            parse_zerodha_demat_statement_pdf(tmp_path, apply_market_prices=False)
            mock_price.assert_not_called()
            parse_zerodha_demat_statement_pdf(tmp_path, apply_market_prices=True)
            mock_price.assert_called_once()
    finally:
        tmp_path.unlink(missing_ok=True)
