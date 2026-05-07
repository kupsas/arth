"""Tests for upload statement routing (:func:`pipeline.detection.resolve_upload_statement_destination`)."""

from __future__ import annotations

from pipeline.detection import (
    ResolveAccountMismatch,
    ResolveConfirmAccount,
    resolve_upload_statement_destination,
)
from pipeline.parsers import PARSER_REGISTRY


def test_resolve_hint_matches_single_source() -> None:
    out = resolve_upload_statement_destination(
        source_type="hdfc_savings",
        account_hint="3703",
        user_source_keys=["hdfc_savings"],
        user_source_configs={"hdfc_savings": {"account_id": "HDFC_SAL_3703"}},
        parser_registry=PARSER_REGISTRY,
        skip_account_validation=False,
    )
    assert out == "hdfc_savings"


def test_resolve_hint_mismatch_returns_struct() -> None:
    out = resolve_upload_statement_destination(
        source_type="hdfc_savings",
        account_hint="9999",
        user_source_keys=["hdfc_savings"],
        user_source_configs={"hdfc_savings": {"account_id": "HDFC_SAL_3703"}},
        parser_registry=PARSER_REGISTRY,
        skip_account_validation=False,
    )
    assert isinstance(out, ResolveAccountMismatch)
    assert out.detected_hint == "9999"
    assert out.hints_on_file == {"hdfc_savings": "3703"}


def test_resolve_no_hint_single_source_needs_confirm() -> None:
    out = resolve_upload_statement_destination(
        source_type="hdfc_savings",
        account_hint=None,
        user_source_keys=["hdfc_savings"],
        user_source_configs={"hdfc_savings": {"account_id": "HDFC_SAL_3703"}},
        parser_registry=PARSER_REGISTRY,
        skip_account_validation=False,
    )
    assert isinstance(out, ResolveConfirmAccount)
    assert out.candidate_keys == ("hdfc_savings",)
    assert out.hints_on_file == {"hdfc_savings": "3703"}


def test_resolve_no_hint_no_last4_on_file_proceeds() -> None:
    """When ``account_id`` does not encode a last-4, we cannot confirm — import proceeds."""
    out = resolve_upload_statement_destination(
        source_type="hdfc_savings",
        account_hint=None,
        user_source_keys=["hdfc_savings"],
        user_source_configs={"hdfc_savings": {"account_id": "UNKNOWN"}},
        parser_registry=PARSER_REGISTRY,
        skip_account_validation=False,
    )
    assert out == "hdfc_savings"
