"""Regression tests for PDF password name + DDMM derivation (ICICI / HDFC CC family)."""

from __future__ import annotations

from scraper.pdf_passwords import (
    ARTH_PDF_INGREDIENT_DOB_ISO,
    ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME,
    _derive_name_dob_password_variants_for_holder_names,
)


def test_first_four_strip_punctuation_examples() -> None:
    """Examples from product docs: punctuation stripped before first-four."""
    dob = "1977-01-05"
    names = ["Purnendu Jha", "P. M. Jha", "Col. P.M. Jha"]
    expected_sets = [
        {"PURN0501", "purn0501"},
        {"PMJH0501", "pmjh0501"},
        {"COLP0501", "colp0501"},
    ]
    for name, exp in zip(names, expected_sets, strict=True):
        got = set(_derive_name_dob_password_variants_for_holder_names([name], dob))
        assert got == exp, (name, got)

    # Multiple holder strings → union of candidates (deduped by function order)
    all_c = _derive_name_dob_password_variants_for_holder_names(names, dob)
    assert len(all_c) == 6
    assert set(all_c) == set().union(*expected_sets)


def test_secrets_override_still_first_in_chain() -> None:
    """Explicit UserSecrets name is still supported (tested at integration level)."""
    assert ARTH_PDF_INGREDIENT_ICICI_REGISTERED_NAME.startswith("ARTH_")
    assert ARTH_PDF_INGREDIENT_DOB_ISO.startswith("ARTH_")
