"""Rules provenance + email review-queue policy.

The rules classifier used to leave ``classification_source`` unset on many
branches; :func:`pipeline.rules_classifier.classify_rules` now finalizes
``RULES_GENERIC`` so review confidence matches rule-driven outcomes.

``should_auto_review_email`` can keep HIGH rows visible in the review UI during
QA via ``ARTH_REVIEW_QUEUE_INCLUDE_HIGH``.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from pipeline.models import (
    CanonicalTransaction,
    ClassificationSource,
    Direction,
    TxnType,
)
from pipeline.review_confidence import compute_review_confidence, should_auto_review_email
from pipeline.rules_classifier import classify_rules
from pipeline.user_config import default_user_classification_config


def _minimal_txn(*, raw_description: str, account_id: str = "HDFC_SAVINGS_TEST") -> CanonicalTransaction:
    """Build a canonical row the way transform would (classification fields empty)."""
    return CanonicalTransaction(
        txn_id="T_20260101",
        txn_date=datetime.date(2026, 1, 15),
        account_id=account_id,
        source_statement="test.csv",
        direction=Direction.OUTFLOW,
        amount=Decimal("99.00"),
        raw_description=raw_description,
    )


def test_rules_branch_without_explicit_source_gets_rules_generic_and_high_review() -> None:
    """Processing-fee path sets txn_type + channel but historically skipped ``_set_rules_source``."""
    txn = _minimal_txn(raw_description="BANK PROCESSING FEE JAN 2026")
    assert txn.classification_source is None

    classify_rules([txn], default_user_classification_config())

    assert txn.txn_type == TxnType.EXPENSE_OTHER
    assert txn.classification_source == ClassificationSource.RULES_GENERIC
    assert compute_review_confidence(txn) == "HIGH"


def test_no_rules_match_leaves_source_none_and_medium_review() -> None:
    """Narration that matches no channel / type rule stays unclassified → no provenance stamp."""
    txn = _minimal_txn(raw_description="MYSTERY INTERNAL GLYPH XYZ123")
    classify_rules([txn], default_user_classification_config())

    assert txn.channel is None
    assert txn.txn_type is None
    assert txn.classification_source is None
    assert compute_review_confidence(txn) == "MEDIUM"


def test_should_auto_review_high_respects_include_high_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTH_EMAIL_AUTO_REVIEW", "1")
    monkeypatch.delenv("ARTH_REVIEW_QUEUE_INCLUDE_HIGH", raising=False)
    assert should_auto_review_email("HIGH") is True

    monkeypatch.setenv("ARTH_REVIEW_QUEUE_INCLUDE_HIGH", "1")
    assert should_auto_review_email("HIGH") is False
