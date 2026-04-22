"""Unit tests for onboarding self-alias generation (Track 2 Phase 3a)."""

import datetime
from decimal import Decimal

import pytest

from api.services.preclassification_identity import build_self_aliases_from_names


def test_build_aliases_full_name_permutations():
    display, aliases = build_self_aliases_from_names("Sai Sashank", "Kuppa")
    assert display == "Sai Sashank Kuppa"
    assert "KUPPA SAI SASHANK" in aliases
    assert "SAI SASHANK KUPPA" in aliases
    assert "SAI SASHANK" in aliases
    assert "KUPPA" not in aliases


def test_build_aliases_first_only_when_no_last():
    display, aliases = build_self_aliases_from_names("Ada", "")
    assert display == "Ada"
    assert aliases == ["ADA"]


def test_extras_merged_and_deduped():
    display, aliases = build_self_aliases_from_names("Ada", "Lovelace", extra_aliases=["ADA", " COUNTESS "])
    assert display == "Ada Lovelace"
    assert "ADA" in aliases
    assert "COUNTESS" in aliases


def test_classify_llm_no_keys_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3c: with ``LLM_MODEL=auto`` and empty keys, skip LLM without raising."""
    from pipeline import config
    from pipeline.llm_classifier import classify_llm
    from pipeline.models import CanonicalTransaction, Channel, Direction, TxnType

    monkeypatch.setattr(config, "LLM_MODEL", "auto", raising=False)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "GOOGLE_API_KEY", "", raising=False)

    txn = CanonicalTransaction(
        txn_id="T_20260101",
        txn_date=datetime.date(2026, 1, 1),
        account_id="TEST_ACCT",
        source_statement="test",
        direction=Direction.OUTFLOW,
        amount=Decimal("50.00"),
        raw_description="UPI/123456789012/SOME MERCHANT",
        txn_type=TxnType.UPI_EXPENSE,
        channel=Channel.UPI,
        upi_type=None,
        counterparty=None,
        counterparty_category=None,
    )
    out = classify_llm([txn])
    assert out[0].counterparty is None
    assert out[0].counterparty_category is None
