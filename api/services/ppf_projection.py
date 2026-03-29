"""Illustrative PPF balance at statutory maturity (no further deposits)."""

from __future__ import annotations

import datetime


def ppf_projected_balance_at_maturity(
    *,
    balance_today: float,
    maturity_date: datetime.date,
    today: datetime.date,
    annual_rate_percent: float,
) -> float | None:
    """
    One closed-form illustration: compound **annually** at ``annual_rate_percent``
    for the fractional years from ``today`` (inclusive as start) to ``maturity_date``.

    Real PPF credits interest once a year (with monthly balance rules); this is a
    smooth approximation for dashboard display, not a tax or bank quote.
    """
    if balance_today <= 0:
        return None
    if maturity_date <= today:
        return round(balance_today, 0)
    days = (maturity_date - today).days
    if days <= 0:
        return round(balance_today, 0)
    years = days / 365.25
    r = annual_rate_percent / 100.0
    fv = balance_today * (1.0 + r) ** years
    return round(fv, 0)
