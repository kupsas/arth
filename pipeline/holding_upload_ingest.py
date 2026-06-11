"""
Ingest path for **manual portfolio uploads** (Coverage step fallback when Gmail had no holdings).

Maps detection ``source_type`` strings to the same parsers used by email + CSV CLI ingest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlmodel import Session

from pipeline.holding_pipeline import ingest_holdings, ingest_investment_transactions
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY
from parsers.holdings.icici_direct_equity_statement_pdf import (
    parse_icici_direct_equity_statement_pdf,
)
from parsers.holdings.icici_direct_mf import derive_mf_holdings
from parsers.holdings.icici_direct_mf_statement_pdf import parse_icici_direct_mf_statement_pdf
from parsers.holdings.icici_ppf_pdf import parse_icici_ppf_from_combined_pdf
from parsers.holdings.zerodha_demat_statement_pdf import (
    derive_zerodha_holdings,
    parse_zerodha_demat_statement_pdf,
)

logger = logging.getLogger(__name__)

_VALID_HOLDING_UPLOAD_TYPES = frozenset(
    {
        "icici_direct_mf",
        "icici_ppf",
        "icici_direct_equity_statement_pdf",
        "icici_direct_mf_statement_pdf",
        "icici_ppf_pdf",
        "zerodha_tradebook",
        "zerodha_demat_statement_pdf",
    }
)

# Public alias for API routing (unified upload + /upload/holdings).
VALID_PORTFOLIO_UPLOAD_SOURCE_TYPES = _VALID_HOLDING_UPLOAD_TYPES


def ingest_portfolio_file(
    *,
    path: Path,
    source_type: str,
    user_id: str,
    session: Session,
) -> dict[str, Any]:
    """Parse *path* and upsert holdings / investment transactions for *user_id*.

    Returns aggregate counters suitable for JSON responses.
    """
    sk = source_type.strip()
    if sk not in _VALID_HOLDING_UPLOAD_TYPES:
        raise ValueError(f"Unsupported holding upload source_type: {sk!r}")

    h_stats: dict[str, int] = {"inserted": 0, "updated": 0, "errors": 0}
    inv_stats: dict[str, Any] = {}

    if sk in HOLDING_PARSER_REGISTRY:
        parser_cls = HOLDING_PARSER_REGISTRY[sk]
        holdings, txns = parser_cls().parse_path(path)
        h_stats = ingest_holdings(session, holdings, user_id=user_id, dry_run=False)
        inv_stats = ingest_investment_transactions(session, txns, user_id=user_id, dry_run=False)
        return {"holdings": h_stats, "investment_txns": inv_stats}

    if sk == "icici_direct_equity_statement_pdf":
        txns = parse_icici_direct_equity_statement_pdf(path)
        inv_stats = ingest_investment_transactions(session, txns, user_id=user_id, dry_run=False)
        return {"holdings": h_stats, "investment_txns": inv_stats}

    if sk == "icici_direct_mf_statement_pdf":
        txns = parse_icici_direct_mf_statement_pdf(path)
        holdings = derive_mf_holdings(txns)
        h_stats = ingest_holdings(session, holdings, user_id=user_id, dry_run=False)
        inv_stats = ingest_investment_transactions(session, txns, user_id=user_id, dry_run=False)
        return {"holdings": h_stats, "investment_txns": inv_stats}

    if sk == "icici_ppf_pdf":
        holdings, txns = parse_icici_ppf_from_combined_pdf(
            path,
            source_label="manual_upload_icici_ppf_pdf",
        )
        h_stats = ingest_holdings(session, holdings, user_id=user_id, dry_run=False)
        inv_stats = ingest_investment_transactions(session, txns, user_id=user_id, dry_run=False)
        return {"holdings": h_stats, "investment_txns": inv_stats}

    if sk == "zerodha_demat_statement_pdf":
        _holdings_unused, txns = parse_zerodha_demat_statement_pdf(path)
        holdings = derive_zerodha_holdings(txns)
        h_stats = ingest_holdings(session, holdings, user_id=user_id, dry_run=False)
        inv_stats = ingest_investment_transactions(session, txns, user_id=user_id, dry_run=False)
        return {"holdings": h_stats, "investment_txns": inv_stats}

    raise ValueError(f"Unhandled holding source_type {sk!r}")
