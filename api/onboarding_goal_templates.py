"""
Static goal templates for the onboarding goal wizard (Track 2, Phase 4c).

These are *defaults* for the UI — the canonical inflation mapping for live goals
still comes from :mod:`api.services.inflation_service` and ``GOAL_INFLATION_MAP``.
"""

from __future__ import annotations

import math
from typing import Any

from sqlmodel import Session

from api.services.inflation_service import (
    GOAL_INFLATION_MAP,
    INFLATION_CATEGORY_LABELS,
    INFLATION_DEFAULTS,
    merge_rates_from_db,
)

# All amounts are INR (numeric, not strings).
# Timeframes in years; emergency uses fractional years (0.5 = six months).
#
# ``goal_class`` drives onboarding UX and POST /api/goals validation:
#   POINT_IN_TIME — lump-sum / corpus toward ``target_date``
#   RECURRING_CASH_FLOW — ``recurrence_amount`` + ``recurrence_frequency`` (EMI, annual trip, …)
GOAL_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "house",
        "name": "Buy a house",
        "icon": "🏠",
        "default_target_amount_min": 5_000_000,
        "default_target_amount_max": 20_000_000,
        "default_timeframe_years_min": 5.0,
        "default_timeframe_years_max": 10.0,
        "suggested_priority": 1,
        "default_expected_return_rate": 10.0,
        "goal_type": "SAVINGS",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "HOME_PURCHASE",
        "time_horizon": "MULTI_YEAR",
        "funding_mode": "ACCUMULATION",
    },
    {
        "id": "vehicle",
        "name": "Buy a car / vehicle",
        "icon": "🚗",
        "default_target_amount_min": 500_000,
        "default_target_amount_max": 2_500_000,
        "default_timeframe_years_min": 1.0,
        "default_timeframe_years_max": 5.0,
        "suggested_priority": 2,
        "default_expected_return_rate": 7.0,
        "goal_type": "SAVINGS",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "VEHICLE",
        "time_horizon": "MULTI_YEAR",
        "funding_mode": "ACCUMULATION",
    },
    {
        "id": "wedding",
        "name": "Wedding fund",
        "icon": "💒",
        "default_target_amount_min": 1_000_000,
        "default_target_amount_max": 5_000_000,
        "default_timeframe_years_min": 1.0,
        "default_timeframe_years_max": 5.0,
        "suggested_priority": 2,
        "default_expected_return_rate": 7.0,
        "goal_type": "SAVINGS",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "WEDDING",
        "time_horizon": "MULTI_YEAR",
        "funding_mode": "EVENT",
    },
    {
        "id": "retirement",
        "name": "Retirement corpus",
        "icon": "🌴",
        "default_target_amount_min": 10_000_000,
        "default_target_amount_max": 50_000_000,
        "default_timeframe_years_min": 10.0,
        "default_timeframe_years_max": 30.0,
        "suggested_priority": 1,
        "default_expected_return_rate": 10.0,
        "goal_type": "INVESTMENT",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "RETIREMENT",
        "time_horizon": "DECADE",
        "funding_mode": "ACCUMULATION",
    },
    {
        "id": "emergency",
        "name": "Emergency fund",
        "icon": "🧯",
        "default_target_amount_min": 300_000,
        "default_target_amount_max": 1_000_000,
        "default_timeframe_years_min": 0.5,
        "default_timeframe_years_max": 2.0,
        "suggested_priority": 1,
        "default_expected_return_rate": 4.0,
        "goal_type": "EMERGENCY_FUND",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "EMERGENCY_FUND",
        "time_horizon": "QUARTERLY",
        "funding_mode": "MAINTENANCE",
    },
    {
        "id": "travel",
        "name": "Annual vacation & travel",
        "icon": "✈️",
        "default_target_amount_min": 100_000,
        "default_target_amount_max": 500_000,
        "default_timeframe_years_min": 1.0,
        "default_timeframe_years_max": 1.0,
        "suggested_priority": 4,
        "default_expected_return_rate": 4.0,
        "goal_type": "SAVINGS",
        "goal_class": "RECURRING_CASH_FLOW",
        "goal_subtype": "TRAVEL",
        "time_horizon": "ANNUAL",
        "funding_mode": "EVENT",
        "recurrence_amount_hint": 150_000,
        "recurrence_frequency": "ANNUAL",
    },
    {
        "id": "loan_emi",
        "name": "Loan / EMI payments",
        "icon": "🏦",
        "default_target_amount_min": 15_000,
        "default_target_amount_max": 120_000,
        "default_timeframe_years_min": 2.0,
        "default_timeframe_years_max": 10.0,
        "suggested_priority": 1,
        "default_expected_return_rate": 0.0,
        "goal_type": "DEBT_PAYOFF",
        "goal_class": "RECURRING_CASH_FLOW",
        "goal_subtype": "LOAN_PAYOFF",
        "time_horizon": "MONTHLY",
        "funding_mode": "CONSTRAINT",
        "recurrence_amount_hint": 50_000,
        "recurrence_frequency": "MONTHLY",
    },
    {
        "id": "custom",
        "name": "Custom goal",
        "icon": "✨",
        "default_target_amount_min": 10_000,
        "default_target_amount_max": 1_000_000_000,
        "default_timeframe_years_min": 0.25,
        "default_timeframe_years_max": 40.0,
        "suggested_priority": 3,
        "default_expected_return_rate": 7.0,
        "goal_type": "SAVINGS",
        "goal_class": "POINT_IN_TIME",
        "goal_subtype": "CUSTOM",
        "time_horizon": "MULTI_YEAR",
        "funding_mode": "ACCUMULATION",
    },
]

# Shipped on every ``GET /api/onboarding/goal-templates`` so the client can group
# cards without hard-coding copy. Order is the suggested UX order (PIT first).
TEMPLATE_LIST_SECTIONS: list[dict[str, str]] = [
    {
        "goal_class": "POINT_IN_TIME",
        "title": "One-time goals",
        "description": (
            "Save toward a target date — home, vehicle, wedding, retirement, emergency "
            "fund, or your own amount (custom)."
        ),
    },
    {
        "goal_class": "RECURRING_CASH_FLOW",
        "title": "Recurring cash flows",
        "description": (
            "Budgets that repeat on a schedule — annual vacation set-aside or monthly "
            "loan EMI (principal is nominal; planning copy below is display-only)."
        ),
    },
]


def _inflation_category_for_subtype(subtype: str | None) -> str:
    raw = GOAL_INFLATION_MAP.get((subtype or "CUSTOM") or "CUSTOM", "CPI_GENERAL")
    return raw if raw else "CPI_GENERAL"


def _annual_inflation_pct(session: Session, category: str) -> float:
    merged = merge_rates_from_db(session)
    if category in merged:
        return float(merged[category])
    return float(INFLATION_DEFAULTS.get(category, 6.0))


def _future_value_today_denominated(
    target_today: float, annual_inflation_pct: float, years: float
) -> float:
    if years <= 0 or target_today <= 0:
        return max(0.0, target_today)
    r = annual_inflation_pct / 100.0
    return target_today * ((1.0 + r) ** years)


def _period_word(recurrence_frequency: str | None) -> str:
    f = (recurrence_frequency or "ANNUAL").strip().upper()
    return {"MONTHLY": "month", "QUARTERLY": "quarter", "ANNUAL": "year"}.get(
        f, "period"
    )


def _pit_template_preview(
    t_amt: float,
    t_y: float,
    *,
    infl_pct: float,
    lbl: str,
) -> dict[str, Any]:
    fv = _future_value_today_denominated(t_amt, infl_pct, t_y)
    return {
        "target_today_in_inr": t_amt,
        "horizon_years": t_y,
        "inflation_annual_percent_used": infl_pct,
        "inflation_fv_inr": round(fv, 2),
        "preview_mechanism": "POINT_IN_TIME",
        "copy": (
            f"Target: ₹{t_amt:,.0f} in today's rupees — "
            f"inflation-adjusted ≈ ₹{fv:,.0f} in ~{t_y:.1f}y "
            f"at {infl_pct:.1f}%/yr ({lbl})."
        ),
    }


def _recurring_template_preview(
    t_amt: float,
    t_y: float,
    *,
    goal_subtype: str,
    recurrence_frequency: str | None,
    infl_pct: float,
    lbl: str,
) -> dict[str, Any]:
    period = _period_word(recurrence_frequency)
    if goal_subtype == "LOAN_PAYOFF":
        fv = t_amt
        copy = (
            f"EMI-style outflow: about ₹{t_amt:,.0f} per {period} (today's rupees), "
            f"planning window ~{t_y:.1f} years. Loan payments are tracked in nominal "
            "rupees (no leisure-style inflation compounding on this line)."
        )
    else:
        fv = _future_value_today_denominated(t_amt, infl_pct, t_y)
        copy = (
            f"Recurring budget: about ₹{t_amt:,.0f} per {period} (today's rupees). "
            f"If similar costs rise ~{infl_pct:.1f}%/yr ({lbl}), a rough run-rate after "
            f"~{t_y:.1f}y might be nearer ₹{fv:,.0f} per {period} — display only."
        )
    return {
        "target_today_in_inr": t_amt,
        "horizon_years": t_y,
        "inflation_annual_percent_used": infl_pct,
        "inflation_fv_inr": round(fv, 2),
        "preview_mechanism": "RECURRING_CASH_FLOW",
        "copy": copy,
    }


def build_goal_templates_response(
    session: Session,
    *,
    target_amount: float | None = None,
    years: float | None = None,
    template_id: str | None = None,
) -> dict[str, Any]:
    """
    Enrich every template with resolved inflation category + % from DB/defaults, and
    (when client passes ``target_amount`` + ``years``) a preview row for that scenario.

    The math is **display-only** — live goals still use the simulation stack.

    Pass ``target_amount`` + ``years`` together with ``template_id`` to get a
    per-template *preview*: lump-sum FV for ``POINT_IN_TIME``, recurring wording
    for ``RECURRING_CASH_FLOW``. Without ``template_id``, ``headline_preview``
    (one-time FV) and ``headline_preview_recurring`` (run-rate hint) are both
    included when those query params are set.

    ``template_sections`` is always returned so clients can group the grid by
    ``goal_class`` without duplicating product copy.
    """
    headline = _annual_inflation_pct(session, "CPI_GENERAL")

    out_templates: list[dict[str, Any]] = []
    for row in GOAL_TEMPLATES:
        tid = str(row["id"])
        gsubtype = str(row.get("goal_subtype") or "CUSTOM")
        infl_cat = _inflation_category_for_subtype(gsubtype)
        infl_pct = _annual_inflation_pct(session, infl_cat)
        lbl = INFLATION_CATEGORY_LABELS.get(
            infl_cat, "India headline CPI (all items)"
        )
        enriched: dict[str, Any] = {
            **row,
            "inflation_rate_category": infl_cat,
            "inflation_rate_label": lbl,
            "inflation_annual_percent": round(infl_pct, 2),
        }

        want = (
            target_amount is not None
            and years is not None
            and template_id is not None
            and str(template_id) == tid
        )
        if want:
            t_amt = float(target_amount)  # type: ignore[arg-type]
            t_y = float(years)  # type: ignore[arg-type]
            if math.isfinite(t_amt) and math.isfinite(t_y) and t_amt > 0 and t_y > 0:
                gc = str(row.get("goal_class") or "POINT_IN_TIME")
                if gc == "RECURRING_CASH_FLOW":
                    enriched["preview"] = _recurring_template_preview(
                        t_amt,
                        t_y,
                        goal_subtype=gsubtype,
                        recurrence_frequency=str(
                            row.get("recurrence_frequency") or "ANNUAL"
                        ),
                        infl_pct=infl_pct,
                        lbl=lbl,
                    )
                else:
                    enriched["preview"] = _pit_template_preview(
                        t_amt, t_y, infl_pct=infl_pct, lbl=lbl
                    )

        out_templates.append(enriched)

    extra: dict[str, Any] = {
        "headline_cpi_annual_percent": round(headline, 2),
        "templates": out_templates,
        "template_sections": [dict(s) for s in TEMPLATE_LIST_SECTIONS],
    }
    if (
        target_amount is not None
        and years is not None
        and template_id is None
    ):
        t_amt = float(target_amount)
        t_y = float(years)
        if math.isfinite(t_amt) and math.isfinite(t_y) and t_amt > 0 and t_y > 0:
            fv = _future_value_today_denominated(t_amt, headline, t_y)
            extra["headline_preview"] = {
                "target_today_in_inr": t_amt,
                "horizon_years": t_y,
                "inflation_annual_percent_used": headline,
                "inflation_fv_inr": round(fv, 2),
                "preview_mechanism": "POINT_IN_TIME",
                "copy": (
                    "One-time goals: we grow today's rupees using India headline CPI (all items) — "
                    f"₹{t_amt:,.0f} → about ₹{fv:,.0f} over ~{t_y:.1f}y "
                    f"(display only; pick a template below for category-specific hints)."
                ),
            }
            extra["headline_preview_recurring"] = {
                "target_today_in_inr": t_amt,
                "horizon_years": t_y,
                "inflation_annual_percent_used": headline,
                "inflation_fv_inr": round(t_amt, 2),
                "preview_mechanism": "RECURRING_CASH_FLOW",
                "copy": (
                    "Recurring goals: use the amount as a per-month or per-year run-rate "
                    f"(here ₹{t_amt:,.0f}) over ~{t_y:.1f}y — open a recurring template for "
                    "wording that matches EMI vs annual vacation."
                ),
            }
    return extra
