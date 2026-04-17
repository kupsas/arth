"""
Weekly portfolio data refresh — prices, NSE reference cache, holdings enrichment.

Used by:
  - ``scraper.scheduler`` (APScheduler while the API process is running)
  - ``scripts/weekly_market_data_refresh.py`` (manual / one-off CLI)

Order matches a careful manual run: ``refresh_all_prices`` → ``refresh_nse_equity_reference``
→ ``enrich_holdings``.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from api.database import get_engine
from api.services.holding_enrichment import enrich_holdings
from api.services.nse_equity_reference import refresh_nse_equity_reference
from api.services.price_feed import refresh_all_prices

logger = logging.getLogger(__name__)


def run_weekly_market_data_refresh(*, user_id: str | None = None) -> dict[str, Any]:
    """
    Run the three-step refresh. Each step uses its own DB session and commits.

    Args:
        user_id: If set, limit ``refresh_all_prices`` and ``enrich_holdings`` to this user.
                 ``refresh_nse_equity_reference`` is always global (one cache table).

    Returns:
        A JSON-serialisable summary dict (for logs / CLI).
    """
    engine = get_engine()
    summary: dict[str, Any] = {"user_id_filter": user_id}

    with Session(engine) as session:
        summary["prices"] = refresh_all_prices(session, user_id=user_id)
        session.commit()

    with Session(engine) as session:
        summary["nse_equity_reference"] = refresh_nse_equity_reference(session, commit=True)

    with Session(engine) as session:
        report = enrich_holdings(session, user_id=user_id, commit=True)
        summary["enrich_holdings"] = report.as_dict()

    logger.info(
        "Weekly market data refresh done — as_of=%s symbols_total=%s equities_cap_updated=%s",
        (summary.get("prices") or {}).get("as_of"),
        (summary.get("nse_equity_reference") or {}).get("symbols_total"),
        (summary.get("enrich_holdings") or {}).get("equities_cap_updated"),
    )
    return summary
