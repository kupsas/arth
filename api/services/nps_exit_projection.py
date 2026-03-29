"""NPS Tier I — normal exit at age 60 and an illustrative balance projection.

The subscriber date of birth is read from the API process environment (root ``.env``)
as ``DOB=YYYY-MM-DD``. The dashboard's ``.env.local`` is not visible to FastAPI unless
you duplicate the variable in the root env file the API loads.
"""

from __future__ import annotations

import datetime
import os

from api.services.ppf_projection import ppf_projected_balance_at_maturity

# Shown next to the projection on the holdings UI — keeps expectations honest.
NPS_PROJECTION_STATIC_NOTE = (
    "Illustrative only: today’s balance grown at a constant nominal rate until your normal "
    "exit date (60th birthday). Ignores future contributions, CRA charges, glide-path "
    "changes, and market risk. NPS is market-linked — not financial advice."
)


def parse_subscriber_dob_from_env() -> datetime.date | None:
    """Parse ``DOB`` (``YYYY-MM-DD``). Returns ``None`` if unset or invalid."""
    raw = os.environ.get("DOB", "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def nps_normal_exit_date(dob: datetime.date) -> datetime.date:
    """60th birthday in calendar terms (Feb 29 → Feb 28 when year 60 is not a leap year)."""
    try:
        return dob.replace(year=dob.year + 60)
    except ValueError:
        return dob.replace(month=2, day=28, year=dob.year + 60)


def nps_projection_annual_rate_percent() -> float:
    """
    Nominal annual return assumption for the illustration.

    Override with ``NPS_PROJECTION_ANNUAL_RATE_PCT`` (e.g. ``11`` or ``12``).
    Default ``10`` is a common conservative planning figure; lifecycle funds and
    your realised XIRR can differ materially.
    """
    raw = os.environ.get("NPS_PROJECTION_ANNUAL_RATE_PCT", "").strip()
    if raw:
        return float(raw)
    return 10.0


def nps_projected_balance_at_normal_exit(
    *,
    balance_today: float,
    exit_date: datetime.date,
    today: datetime.date,
    annual_rate_percent: float,
) -> float | None:
    """
    Same continuous-year math as PPF projection: ``balance * (1+r)^(days/365.25)``.

    If ``exit_date`` is already past (or today), returns rounded current balance.
    """
    if balance_today <= 0:
        return None
    if exit_date <= today:
        return round(balance_today, 0)
    return ppf_projected_balance_at_maturity(
        balance_today=balance_today,
        maturity_date=exit_date,
        today=today,
        annual_rate_percent=annual_rate_percent,
    )
