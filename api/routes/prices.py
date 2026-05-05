"""
Prices API — Phase A.3.4

Trigger a refresh (NSE / AMFI / yfinance per ``price_feed`` rules) and read history.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from api.database import get_session
from api.models import Price
from api.services.price_feed import canonical_nse_symbol, normalize_equity_symbol, refresh_all_prices

logger = logging.getLogger(__name__)

router = APIRouter()

# Reject absurdly long path segments (log injection / accidental paste).
_MAX_SYMBOL_LEN = 64


class PricePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    date: datetime.date
    close_price: float
    source: str


class RefreshPricesOut(BaseModel):
    """Passthrough from ``refresh_all_prices`` (counts + symbol lists)."""

    as_of: str
    price_rows_upserted: int
    holdings_updated: int
    nse_symbols: list[str]
    mf_codes: list[str]
    international_yfinance_symbols: list[str]


def _symbol_variants(symbol: str) -> list[str]:
    s = symbol.strip()
    out = list(
        dict.fromkeys(
            [s, normalize_equity_symbol(s), canonical_nse_symbol(s)],
        )
    )
    return out


@router.post("/refresh", response_model=RefreshPricesOut)
def post_refresh_prices(
    *,
    session: Session = Depends(get_session),
    user_id: str | None = Query(default=None, description="Limit to this user's market-priced holdings"),
):
    uid = user_id.strip() if user_id and user_id.strip() else None
    result = refresh_all_prices(session, user_id=uid)
    session.commit()
    logger.info(
        "Prices refreshed — saved %s price points · updated %s holdings",
        int(cast(Any, result["price_rows_upserted"])),
        int(cast(Any, result["holdings_updated"])),
    )
    # Normalise typed return for response_model
    return RefreshPricesOut(
        as_of=str(result["as_of"]),
        price_rows_upserted=int(cast(Any, result["price_rows_upserted"])),
        holdings_updated=int(cast(Any, result["holdings_updated"])),
        nse_symbols=list(cast(Any, result["nse_symbols"])),
        mf_codes=list(cast(Any, result["mf_codes"])),
        international_yfinance_symbols=list(cast(Any, result["international_yfinance_symbols"])),
    )


@router.get("/{symbol}/history", response_model=list[PricePointOut])
def get_price_history(
    symbol: str,
    *,
    session: Session = Depends(get_session),
    start_date: str | None = Query(default=None, description="YYYY-MM-DD inclusive"),
    end_date: str | None = Query(default=None, description="YYYY-MM-DD inclusive"),
    limit: int = Query(default=2000, ge=1, le=20_000),
):
    if len(symbol) > _MAX_SYMBOL_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"symbol too long (max {_MAX_SYMBOL_LEN} characters)",
        )
    variants = _symbol_variants(symbol)
    q = select(Price).where(col(Price.symbol).in_(variants))
    if start_date:
        try:
            d0 = datetime.date.fromisoformat(start_date[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date")
        q = q.where(Price.date >= d0)
    if end_date:
        try:
            d1 = datetime.date.fromisoformat(end_date[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date")
        q = q.where(Price.date <= d1)
    q = q.order_by(col(Price.date).asc()).limit(limit)
    rows = list(session.exec(q).all())
    return rows
