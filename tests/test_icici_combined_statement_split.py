"""Guards ICICI combined PDFs: PPF table band vs savings-only band (annual + monthly)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from parsers.uploads import icici_savings as mod


def test_combined_ppf_y_window_monthly_pp_f_then_savings_headers() -> None:
    """Monthly email uses ``PPF Account`` in the section title — y-band ends at Savings header."""
    fake_lines = [
        (50.0, "noise", []),
        (100.0, "Statement of Transactions in PPF Account XXXXXXXX2383", []),
        (200.0, "01-03-2026 B/F", []),
        (300.0, "Statement of Transactions in Savings Account XXXXXXXX6118", []),
    ]
    page = MagicMock()
    with patch.object(mod, "_line_rows_from_page", return_value=fake_lines):
        win = mod.combined_ppf_y_window_page1(page)
    assert win == (100.0, 300.0)


def test_needs_savings_only_band_monthly() -> None:
    pdf = MagicMock()
    p0 = MagicMock()
    pdf.pages = [p0]
    p0.extract_text.return_value = (
        "Statement of Transactions in PPF Account … "
        "Statement of Transactions in Savings Account …"
    )
    assert mod._needs_savings_only_band(pdf) is True


def test_needs_savings_only_band_savings_only_monthly() -> None:
    pdf = MagicMock()
    p0 = MagicMock()
    pdf.pages = [p0]
    p0.extract_text.return_value = "Statement of Transactions in Savings Account …"
    assert mod._needs_savings_only_band(pdf) is False
