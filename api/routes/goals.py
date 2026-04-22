"""
Goals CRUD endpoints — Phase 4.5d + Phase B.3 hierarchy

POST   /api/goals           — create a goal
GET    /api/goals           — list goals (scoped to the authenticated user)
GET    /api/goals/{id}      — single goal with computed progress
PATCH  /api/goals/{id}      — update goal fields or current_value
DELETE /api/goals/{id}      — delete a goal

B.3: Hierarchy fields (tier, pyramid_id, activation_*, allocations) are validated
on write. ``user_id`` is always taken from the session — never from the request body
or cross-user query params. Tree/allocation/ancestors routes live in ``goal_tree.py``
and are registered before this router so static paths win.

Progress computation:
  - EXPENSE_LIMIT goals: auto-computed from transactions DB (current month spend)
  - All other goal types: use goal.current_value vs goal.target_amount
  - Response includes ``computed_percentage`` (0–100+), separate from
    ``activation_status`` (PENDING / ACTIVE / COMPLETED / PAUSED).
"""

from __future__ import annotations

import datetime
import logging
from typing import TypeGuard

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import get_session
from api.models import Goal, GoalLink
from api.services.goal_decomposer import (
    LoanParams,
    decompose_debt_goal,
    decompose_point_in_time_goal,
    parent_has_decompose_children,
    spec_to_goal_row,
)
from api.services.goal_graph import validate_link
from api.services.inflation_service import (
    resolve_goal_inflation,
    simulation_inflation_ema_span,
)
from api.services.simulation import MIN_MONTHLY_GOAL_CONTRIBUTION_INR
from api.services.surplus_calculator import compute_surplus
from api.services.activation_engine import (
    ConditionParseError,
    check_and_update_activations,
    validate_condition,
)
from api.services.chart_metrics import (
    CHART_KEY_EXPENSE_NEED_WANT_STACK,
    CHART_KEY_INVESTMENT_NET,
    validate_chart_key_for_goal,
)
from api.services.goal_evaluator import compute_progress
from api.services.priority_scorer import PriorityResult, compute_priority_scores

logger = logging.getLogger(__name__)
router = APIRouter()


# ───────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ───────────────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    """Create payload — ``user_id`` is not accepted; the API uses the logged-in user."""

    name: str = Field(min_length=1, max_length=512)
    goal_type: str
    target_amount: float | None = None
    target_date: str | None = None
    target_metric: str | None = Field(default=None, max_length=4000)
    priority: int = Field(default=3, ge=1, le=5)
    linked_layer: int = Field(default=3, ge=1, le=5)
    linked_category: str | None = Field(default=None, max_length=256)
    chart_key: str | None = Field(default=None, max_length=128)
    progress_cadence: str | None = None
    current_value: float | None = None
    notes: str | None = Field(default=None, max_length=4000)
    # Phase B.0 / B.3 — pyramid & activation (all optional on create)
    pyramid_id: str | None = Field(default=None, max_length=10)
    tier: str | None = Field(default=None, max_length=32)
    time_horizon: str | None = Field(default=None, max_length=32)
    funding_mode: str | None = Field(default=None, max_length=32)
    activation_status: str | None = Field(default=None, max_length=32)
    activation_condition: str | None = Field(default=None, max_length=500)
    monthly_allocation: float | None = Field(default=None, ge=0)
    allocation_priority: int | None = Field(default=None, ge=1, le=100)
    interruptible: bool | None = None
    sensitivity_to_returns: str | None = Field(default=None, max_length=16)
    # Goals architecture V2 (simulation / surplus) — optional on create
    goal_class: str | None = Field(default=None, max_length=32)
    recurrence_amount: float | None = Field(default=None, ge=0)
    recurrence_frequency: str | None = Field(default=None, max_length=16)
    recurrence_start: str | None = None
    recurrence_end: str | None = None
    goal_specific_inflation_rate: float | None = Field(default=None, ge=0, le=50)
    expected_return_rate: float | None = Field(default=None, ge=0, le=50)
    starting_balance: float | None = Field(default=None, ge=0)
    goal_subtype: str | None = Field(default=None, max_length=64)


class GoalReorderItem(BaseModel):
    """Single entry in a priority reorder request (Sub-Plan E)."""

    goal_id: int = Field(ge=1)
    allocation_priority: int = Field(ge=1, le=100)


class GoalReorderBody(BaseModel):
    """New surplus funding order; does not change ``system_priority_score``."""

    goal_order: list[GoalReorderItem] = Field(min_length=1)


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    target_amount: float | None = None
    target_date: str | None = None
    target_metric: str | None = Field(default=None, max_length=4000)
    priority: int | None = Field(default=None, ge=1, le=5)
    linked_category: str | None = Field(default=None, max_length=256)
    chart_key: str | None = Field(default=None, max_length=128)
    progress_cadence: str | None = None
    current_value: float | None = None
    notes: str | None = Field(default=None, max_length=4000)
    pyramid_id: str | None = Field(default=None, max_length=10)
    tier: str | None = Field(default=None, max_length=32)
    time_horizon: str | None = Field(default=None, max_length=32)
    funding_mode: str | None = Field(default=None, max_length=32)
    activation_status: str | None = Field(default=None, max_length=32)
    activation_condition: str | None = Field(default=None, max_length=500)
    monthly_allocation: float | None = Field(default=None, ge=0)
    allocation_priority: int | None = Field(default=None, ge=1, le=100)
    interruptible: bool | None = None
    sensitivity_to_returns: str | None = Field(default=None, max_length=16)
    goal_class: str | None = Field(default=None, max_length=32)
    recurrence_amount: float | None = Field(default=None, ge=0)
    recurrence_frequency: str | None = Field(default=None, max_length=16)
    recurrence_start: str | None = None
    recurrence_end: str | None = None
    goal_specific_inflation_rate: float | None = Field(default=None, ge=0, le=50)
    expected_return_rate: float | None = Field(default=None, ge=0, le=50)
    starting_balance: float | None = Field(default=None, ge=0)


_VALID_GOAL_TYPES = {
    "SAVINGS", "EXPENSE_LIMIT", "EMERGENCY_FUND",
    "INVESTMENT", "DEBT_PAYOFF", "INSURANCE", "TAX",
}

_VALID_PROGRESS_CADENCE = {"MONTHLY", "ANNUAL"}

_VALID_TIME_HORIZON = frozenset(
    {"MONTHLY", "QUARTERLY", "ANNUAL", "MULTI_YEAR", "DECADE"}
)
_VALID_FUNDING_MODE = frozenset({"ACCUMULATION", "CONSTRAINT", "EVENT", "MAINTENANCE"})
_VALID_ACTIVATION_STATUS = frozenset({"PENDING", "ACTIVE", "COMPLETED", "PAUSED"})
_VALID_SENSITIVITY = frozenset({"LOW", "MEDIUM", "HIGH"})

# L1–L4 plus legacy labels (normalised to L* on write).
_VALID_TIERS = frozenset(
    {"L1", "L2", "L3", "L4", "VISION", "STRATEGY", "TACTIC", "OPERATIONAL"}
)
_VALID_GOAL_CLASSES = frozenset({"POINT_IN_TIME", "RECURRING_CASH_FLOW"})
_VALID_GOAL_SUBTYPES = frozenset(
    {
        "HOME_PURCHASE",
        "VEHICLE",
        "WEDDING",
        "RETIREMENT",
        "CHILD_EDUCATION",
        "EMERGENCY_FUND",
        "TRAVEL",
        "LOAN_PAYOFF",
        "CUSTOM",
    }
)
_VALID_RECURRENCE_FREQUENCY = frozenset({"MONTHLY", "QUARTERLY", "ANNUAL"})

_LEGACY_TIER_TO_L = {
    "VISION": "L1",
    "STRATEGY": "L2",
    "TACTIC": "L3",
    "OPERATIONAL": "L4",
}


def _normalize_tier_value(tier: str | None) -> str | None:
    """Persist legacy VISION…OPERATIONAL as L1…L4."""
    if tier is None:
        return None
    return _LEGACY_TIER_TO_L.get(tier, tier)


def _warn_goal_class_recurrence_consistency(
    goal_class: str | None,
    recurrence_amount: float | None,
    recurrence_frequency: str | None,
    *,
    recurrence_start: datetime.date | None = None,
    recurrence_end: datetime.date | None = None,
) -> None:
    """Log soft warnings when recurrence fields disagree with goal_class (V2)."""
    if goal_class == "RECURRING_CASH_FLOW":
        if recurrence_amount is None or recurrence_frequency is None:
            logger.warning(
                "RECURRING_CASH_FLOW goal missing recurrence_amount or "
                "recurrence_frequency (amount=%r, frequency=%r).",
                recurrence_amount,
                recurrence_frequency,
            )
    elif goal_class is not None and goal_class != "RECURRING_CASH_FLOW":
        if any(
            x is not None
            for x in (
                recurrence_amount,
                recurrence_frequency,
                recurrence_start,
                recurrence_end,
            )
        ):
            logger.warning(
                "goal_class=%r but recurrence fields are set — expected only for "
                "RECURRING_CASH_FLOW.",
                goal_class,
            )


def _validate_progress_cadence(goal_type: str, cadence: str | None) -> str:
    raw = (cadence or "MONTHLY").strip().upper()
    if raw not in _VALID_PROGRESS_CADENCE:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid progress_cadence: {cadence!r}. Use MONTHLY or ANNUAL.",
        )
    if raw == "ANNUAL" and goal_type != "EXPENSE_LIMIT":
        raise HTTPException(
            status_code=400,
            detail="progress_cadence ANNUAL is only allowed for EXPENSE_LIMIT goals.",
        )
    return raw


def _default_chart_key_on_create(goal_type: str, linked_category: str | None, chart_key: str | None) -> str | None:
    if chart_key is not None:
        return chart_key
    if goal_type == "INVESTMENT":
        return CHART_KEY_INVESTMENT_NET
    if goal_type == "EXPENSE_LIMIT" and linked_category is None:
        return CHART_KEY_EXPENSE_NEED_WANT_STACK
    return None


def _ensure_chart_key_unique(
    session: Session,
    user_id: str,
    chart_key: str | None,
    *,
    exclude_goal_id: int | None = None,
) -> None:
    if chart_key is None:
        return
    q = select(Goal).where(Goal.user_id == user_id).where(Goal.chart_key == chart_key)
    if exclude_goal_id is not None:
        q = q.where(Goal.id != exclude_goal_id)
    if session.exec(q).first():
        raise HTTPException(
            status_code=400,
            detail=f"Another goal already uses chart_key {chart_key!r} for this user.",
        )


def _ensure_pyramid_id_unique(
    session: Session,
    user_id: str,
    pyramid_id: str | None,
    *,
    exclude_goal_id: int | None = None,
) -> None:
    if not pyramid_id or not pyramid_id.strip():
        return
    pid = pyramid_id.strip()
    q = (
        select(Goal)
        .where(Goal.user_id == user_id)
        .where(Goal.pyramid_id == pid)
    )
    if exclude_goal_id is not None:
        q = q.where(Goal.id != exclude_goal_id)
    if session.exec(q).first():
        raise HTTPException(
            status_code=400,
            detail=f"Another goal already uses pyramid_id {pid!r} for this user.",
        )


def _validate_optional_enum(
    field_name: str,
    value: str | None,
    allowed: frozenset[str],
) -> str | None:
    if value is None:
        return None
    v = value.strip().upper()
    if not v:
        return None
    if v not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name}: {value!r}. Valid: {sorted(allowed)}",
        )
    return v


def _validate_activation_condition_or_400(raw: str | None) -> None:
    try:
        validate_condition(raw)
    except ConditionParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _goal_owned(goal: Goal | None, current_user: str) -> TypeGuard[Goal]:
    """True when ``goal`` exists and belongs to ``current_user`` (narrows type for mypy)."""
    return goal is not None and goal.user_id == current_user


# ───────────────────────────────────────────────────────────────────────────
# POST / — create a goal
# ───────────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_goal(
    body: GoalCreate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    if body.goal_type not in _VALID_GOAL_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid goal_type: {body.goal_type!r}. Valid: {sorted(_VALID_GOAL_TYPES)}",
        )

    target_date = None
    if body.target_date:
        try:
            target_date = datetime.date.fromisoformat(body.target_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid target_date format: {body.target_date!r}. Use YYYY-MM-DD.",
            )

    resolved_ck = _default_chart_key_on_create(
        body.goal_type, body.linked_category, body.chart_key
    )
    # investment_net is unique per user; additional INVESTMENT goals stay unlinked (chart_key=None).
    if body.goal_type == "INVESTMENT" and resolved_ck == CHART_KEY_INVESTMENT_NET:
        q_inv = (
            select(Goal)
            .where(Goal.user_id == current_user)
            .where(Goal.chart_key == CHART_KEY_INVESTMENT_NET)
        )
        if session.exec(q_inv).first():
            resolved_ck = None
    try:
        validate_chart_key_for_goal(body.goal_type, resolved_ck)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _ensure_chart_key_unique(session, current_user, resolved_ck)

    pc = _validate_progress_cadence(body.goal_type, body.progress_cadence)

    tier_raw = _validate_optional_enum("tier", body.tier, _VALID_TIERS)
    tier = _normalize_tier_value(tier_raw)
    time_horizon = _validate_optional_enum(
        "time_horizon", body.time_horizon, _VALID_TIME_HORIZON
    )
    funding_mode = _validate_optional_enum(
        "funding_mode", body.funding_mode, _VALID_FUNDING_MODE
    )
    act_status = body.activation_status
    if act_status is None:
        activation_status = "ACTIVE"
    else:
        validated_act = _validate_optional_enum(
            "activation_status", act_status, _VALID_ACTIVATION_STATUS
        )
        # Empty or whitespace-only client values normalize to default ACTIVE.
        activation_status = validated_act if validated_act is not None else "ACTIVE"
    sensitivity = _validate_optional_enum(
        "sensitivity_to_returns", body.sensitivity_to_returns, _VALID_SENSITIVITY
    )

    _validate_activation_condition_or_400(body.activation_condition)
    _ensure_pyramid_id_unique(session, current_user, body.pyramid_id)

    goal_class = _validate_optional_enum(
        "goal_class", body.goal_class, _VALID_GOAL_CLASSES
    )
    goal_subtype = _validate_optional_enum(
        "goal_subtype", body.goal_subtype, _VALID_GOAL_SUBTYPES
    )
    recurrence_frequency = _validate_optional_enum(
        "recurrence_frequency",
        body.recurrence_frequency,
        _VALID_RECURRENCE_FREQUENCY,
    )

    recurrence_start = None
    if body.recurrence_start:
        try:
            recurrence_start = datetime.date.fromisoformat(body.recurrence_start)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid recurrence_start format: "
                    f"{body.recurrence_start!r}. Use YYYY-MM-DD."
                ),
            ) from None

    recurrence_end = None
    if body.recurrence_end:
        try:
            recurrence_end = datetime.date.fromisoformat(body.recurrence_end)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Invalid recurrence_end format: "
                    f"{body.recurrence_end!r}. Use YYYY-MM-DD."
                ),
            ) from None

    _warn_goal_class_recurrence_consistency(
        goal_class,
        body.recurrence_amount,
        recurrence_frequency,
        recurrence_start=recurrence_start,
        recurrence_end=recurrence_end,
    )
    _validate_recurring_monthly_minimum_or_400(
        goal_class, body.recurrence_amount, recurrence_frequency
    )

    goal = Goal(
        name=body.name,
        goal_type=body.goal_type,
        target_amount=body.target_amount,
        target_date=target_date,
        target_metric=body.target_metric,
        priority=body.priority,
        linked_layer=body.linked_layer,
        linked_category=body.linked_category,
        chart_key=resolved_ck,
        progress_cadence=pc,
        user_id=current_user,
        current_value=body.current_value,
        notes=body.notes,
        pyramid_id=body.pyramid_id.strip() if body.pyramid_id and body.pyramid_id.strip() else None,
        tier=tier,
        time_horizon=time_horizon,
        funding_mode=funding_mode,
        activation_status=activation_status,
        activation_condition=body.activation_condition.strip() if body.activation_condition and body.activation_condition.strip() else None,
        monthly_allocation=body.monthly_allocation,
        allocation_priority=body.allocation_priority,
        interruptible=True if body.interruptible is None else body.interruptible,
        sensitivity_to_returns=sensitivity,
        goal_class=goal_class,
        recurrence_amount=body.recurrence_amount,
        recurrence_frequency=recurrence_frequency,
        recurrence_start=recurrence_start,
        recurrence_end=recurrence_end,
        goal_specific_inflation_rate=body.goal_specific_inflation_rate,
        expected_return_rate=body.expected_return_rate,
        starting_balance=body.starting_balance,
        goal_subtype=goal_subtype,
    )
    session.add(goal)
    session.commit()
    session.refresh(goal)

    if goal.activation_status == "COMPLETED":
        check_and_update_activations(session, current_user)
        session.commit()
        session.refresh(goal)

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress, session=session)


# ───────────────────────────────────────────────────────────────────────────
# GET / — list goals
# ───────────────────────────────────────────────────────────────────────────

@router.get("")
def list_goals(
    goal_type: str | None = Query(None),
    tier: str | None = Query(None),
    activation_status: str | None = Query(None),
    funding_mode: str | None = Query(None),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[dict]:
    """List goals for the authenticated user only (B.3 — no cross-user filter param)."""
    query = select(Goal).where(Goal.user_id == current_user)

    if goal_type is not None:
        query = query.where(Goal.goal_type == goal_type)
    if tier is not None:
        query = query.where(Goal.tier == tier.strip().upper())
    if activation_status is not None:
        query = query.where(
            Goal.activation_status == activation_status.strip().upper()
        )
    if funding_mode is not None:
        query = query.where(Goal.funding_mode == funding_mode.strip().upper())

    query = query.order_by(col(Goal.priority), col(Goal.created_at))
    goals = session.exec(query).all()

    result = []
    for goal in goals:
        progress = compute_progress(goal, session)
        result.append(_goal_to_dict(goal, progress, session=session))
    return result


# ───────────────────────────────────────────────────────────────────────────
# POST /{id}/decompose — preview or create sub-goals (Sub-Plan D)
# Registered before /priorities and /{goal_id} static siblings.
# ───────────────────────────────────────────────────────────────────────────


class DecomposeRequest(BaseModel):
    """Preview decomposition or persist child goals + DECOMPOSES_INTO links."""

    auto_create: bool = False
    loan_params: LoanParams | None = None
    surplus_months: int = Field(default=6, ge=3, le=12)


@router.post("/{goal_id}/decompose")
def decompose_goal(
    goal_id: int,
    body: DecomposeRequest,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Break a goal into sub-goals (PMT / down payment + EMI) with optional persist."""
    goal = session.get(Goal, goal_id)
    if not _goal_owned(goal, current_user):
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    if parent_has_decompose_children(session, goal_id, current_user):
        raise HTTPException(
            status_code=400,
            detail="This goal already has DECOMPOSES_INTO children — remove links first.",
        )

    surplus_res = compute_surplus(session, current_user, months=body.surplus_months)
    surplus = float(surplus_res.monthly_surplus)

    try:
        if body.loan_params is not None:
            if goal.target_date is None:
                raise ValueError("target_date is required for debt decomposition")
            result = decompose_debt_goal(goal, body.loan_params)
            inflation_sim: dict | None = None
        else:
            # Blended IMF CPI YoY (trailing mean) — same scalar for all subtypes until sector series exist.
            res_inf = resolve_goal_inflation(session, goal)
            general_cpi = float(res_inf["annual_pct"])
            result = decompose_point_in_time_goal(
                goal,
                surplus,
                general_cpi=general_cpi,
            )
            inflation_sim = {
                "annual_pct": general_cpi,
                "category": res_inf.get("category"),
                "method": res_inf.get("method"),
                "ema_span": simulation_inflation_ema_span(),
                "detail": res_inf.get("detail"),
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    out: dict = {
        "decomposition": result.model_dump(mode="json"),
        "surplus_headline": surplus,
        "surplus_warnings": surplus_res.warnings,
        "simulation_inflation": inflation_sim,
    }

    if not body.auto_create:
        return out

    created_ids: list[int] = []
    for spec in result.sub_goals:
        child = spec_to_goal_row(spec, user_id=current_user)
        session.add(child)
        session.flush()
        if child.id is None:
            raise HTTPException(status_code=500, detail="Failed to allocate child goal id")
        validate_link(session, goal_id, child.id, current_user)
        link = GoalLink(
            parent_goal_id=goal_id,
            child_goal_id=child.id,
            link_type="DECOMPOSES_INTO",
            user_id=current_user,
            description="Created by goal decomposition",
        )
        session.add(link)
        try:
            session.flush()
        except IntegrityError as e:
            session.rollback()
            raise HTTPException(
                status_code=400,
                detail="Could not create decomposition link (duplicate or constraint).",
            ) from e
        created_ids.append(child.id)

    session.commit()
    out["created_goal_ids"] = created_ids
    return out


# ───────────────────────────────────────────────────────────────────────────
# GET /priorities — system priority scores (Sub-Plan E)
# POST /reorder — user-defined allocation_priority order
# Static paths must be registered before /{goal_id}.
# ───────────────────────────────────────────────────────────────────────────


@router.get("/priorities", response_model=PriorityResult)
def get_goal_priorities(
    persist: bool = Query(
        True,
        description="If true, persist system_priority_score on each active goal.",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> PriorityResult:
    """Compute 4-dimension priority scores and suggested ranks for all active goals."""
    return compute_priority_scores(session, current_user, persist=persist)


@router.post("/reorder")
def reorder_goals(
    body: GoalReorderBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[dict]:
    """
    Update ``allocation_priority`` for the given goals.

    Does **not** modify ``system_priority_score`` (that is only set by GET /priorities).
    """
    ranks = [x.allocation_priority for x in body.goal_order]
    if len(ranks) != len(set(ranks)):
        raise HTTPException(
            status_code=400,
            detail="allocation_priority values must be unique within the request.",
        )

    updated: list[dict] = []
    for item in body.goal_order:
        goal = session.get(Goal, item.goal_id)
        if not _goal_owned(goal, current_user):
            raise HTTPException(
                status_code=404,
                detail=f"Goal {item.goal_id} not found",
            )
        goal.allocation_priority = item.allocation_priority
        goal.updated_at = datetime.datetime.now(datetime.UTC)
        session.add(goal)
        updated.append(
            {
                "id": goal.id,
                "name": goal.name,
                "allocation_priority": goal.allocation_priority,
                "system_priority_score": goal.system_priority_score,
            }
        )

    session.commit()
    return updated


# ───────────────────────────────────────────────────────────────────────────
# GET /{id} — single goal with computed progress
# ───────────────────────────────────────────────────────────────────────────

@router.get("/{goal_id}")
def get_goal(
    goal_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    goal = session.get(Goal, goal_id)
    if not _goal_owned(goal, current_user):
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress, session=session)


# ───────────────────────────────────────────────────────────────────────────
# PATCH /{id} — update a goal
# ───────────────────────────────────────────────────────────────────────────

@router.patch("/{goal_id}")
def update_goal(
    goal_id: int,
    body: GoalUpdate,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict:
    goal = session.get(Goal, goal_id)
    if not _goal_owned(goal, current_user):
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    old_activation = goal.activation_status
    update_data = body.model_dump(exclude_unset=True)

    if "target_date" in update_data and update_data["target_date"] is not None:
        try:
            update_data["target_date"] = datetime.date.fromisoformat(update_data["target_date"])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid target_date format. Use YYYY-MM-DD.",
            )

    if "chart_key" in update_data:
        ck = update_data["chart_key"]
        try:
            validate_chart_key_for_goal(goal.goal_type, ck)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        _ensure_chart_key_unique(session, goal.user_id, ck, exclude_goal_id=goal.id)

    if "progress_cadence" in update_data:
        update_data["progress_cadence"] = _validate_progress_cadence(
            goal.goal_type, update_data["progress_cadence"]
        )

    if "activation_condition" in update_data:
        _validate_activation_condition_or_400(update_data["activation_condition"])
        ac = update_data["activation_condition"]
        update_data["activation_condition"] = (
            ac.strip() if ac and str(ac).strip() else None
        )

    if "pyramid_id" in update_data:
        pv = update_data["pyramid_id"]
        normalized_pyramid = pv.strip() if pv and str(pv).strip() else None
        _ensure_pyramid_id_unique(
            session,
            current_user,
            normalized_pyramid,
            exclude_goal_id=goal.id,
        )
        update_data["pyramid_id"] = normalized_pyramid

    if "tier" in update_data:
        tr = _validate_optional_enum("tier", update_data["tier"], _VALID_TIERS)
        update_data["tier"] = _normalize_tier_value(tr)
    if "time_horizon" in update_data:
        update_data["time_horizon"] = _validate_optional_enum(
            "time_horizon", update_data["time_horizon"], _VALID_TIME_HORIZON
        )
    if "funding_mode" in update_data:
        update_data["funding_mode"] = _validate_optional_enum(
            "funding_mode", update_data["funding_mode"], _VALID_FUNDING_MODE
        )
    if "activation_status" in update_data:
        v = update_data["activation_status"]
        norm = _validate_optional_enum(
            "activation_status", v, _VALID_ACTIVATION_STATUS
        )
        if norm is None and v is not None and str(v).strip() == "":
            raise HTTPException(
                status_code=400,
                detail="activation_status cannot be empty when provided.",
            )
        update_data["activation_status"] = norm
    if "sensitivity_to_returns" in update_data:
        update_data["sensitivity_to_returns"] = _validate_optional_enum(
            "sensitivity_to_returns",
            update_data["sensitivity_to_returns"],
            _VALID_SENSITIVITY,
        )

    if "goal_class" in update_data:
        update_data["goal_class"] = _validate_optional_enum(
            "goal_class", update_data["goal_class"], _VALID_GOAL_CLASSES
        )
    if "recurrence_frequency" in update_data:
        update_data["recurrence_frequency"] = _validate_optional_enum(
            "recurrence_frequency",
            update_data["recurrence_frequency"],
            _VALID_RECURRENCE_FREQUENCY,
        )

    if "recurrence_start" in update_data:
        rs = update_data["recurrence_start"]
        if rs is None:
            update_data["recurrence_start"] = None
        else:
            try:
                update_data["recurrence_start"] = datetime.date.fromisoformat(str(rs))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid recurrence_start format. Use YYYY-MM-DD.",
                ) from None

    if "recurrence_end" in update_data:
        re_end = update_data["recurrence_end"]
        if re_end is None:
            update_data["recurrence_end"] = None
        else:
            try:
                update_data["recurrence_end"] = datetime.date.fromisoformat(str(re_end))
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid recurrence_end format. Use YYYY-MM-DD.",
                ) from None

    eff_class = (
        update_data["goal_class"] if "goal_class" in update_data else goal.goal_class
    )
    eff_ra = (
        update_data["recurrence_amount"]
        if "recurrence_amount" in update_data
        else goal.recurrence_amount
    )
    eff_rf = (
        update_data["recurrence_frequency"]
        if "recurrence_frequency" in update_data
        else goal.recurrence_frequency
    )
    eff_rs = (
        update_data["recurrence_start"]
        if "recurrence_start" in update_data
        else goal.recurrence_start
    )
    eff_re = (
        update_data["recurrence_end"]
        if "recurrence_end" in update_data
        else goal.recurrence_end
    )
    _warn_goal_class_recurrence_consistency(
        eff_class,
        eff_ra,
        eff_rf,
        recurrence_start=eff_rs,
        recurrence_end=eff_re,
    )
    _validate_recurring_monthly_minimum_or_400(eff_class, eff_ra, eff_rf)

    for field, value in update_data.items():
        setattr(goal, field, value)

    goal.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(goal)
    session.flush()

    new_activation = goal.activation_status
    if new_activation == "COMPLETED" and old_activation != "COMPLETED":
        check_and_update_activations(session, current_user)

    session.commit()
    session.refresh(goal)

    progress = compute_progress(goal, session)
    return _goal_to_dict(goal, progress, session=session)


# ───────────────────────────────────────────────────────────────────────────
# DELETE /{id} — delete a goal
# ───────────────────────────────────────────────────────────────────────────

@router.delete("/{goal_id}", status_code=204)
def delete_goal(
    goal_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> None:
    goal = session.get(Goal, goal_id)
    if not _goal_owned(goal, current_user):
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    session.delete(goal)
    session.commit()


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────


def _recurrence_monthly_equivalent_inr(amount: float, frequency: str | None) -> float:
    """Match dashboard `recurrenceAmountToMonthlyInr` / simulation need scaling."""
    if frequency is None:
        return float(amount)
    f = str(frequency).strip().upper()
    if f == "MONTHLY":
        return float(amount)
    if f == "QUARTERLY":
        return float(amount) / 3.0
    if f in ("ANNUAL", "YEARLY"):
        return float(amount) / 12.0
    return float(amount)


def _validate_recurring_monthly_minimum_or_400(
    goal_class: str | None,
    recurrence_amount: float | None,
    recurrence_frequency: str | None,
) -> None:
    if (goal_class or "").strip().upper() != "RECURRING_CASH_FLOW":
        return
    if recurrence_amount is None or recurrence_frequency is None:
        return
    amt = float(recurrence_amount)
    if amt <= 0:
        return
    monthly = _recurrence_monthly_equivalent_inr(amt, recurrence_frequency)
    if 0 < monthly < MIN_MONTHLY_GOAL_CONTRIBUTION_INR:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Recurring goals need at least ₹{MIN_MONTHLY_GOAL_CONTRIBUTION_INR:,.0f}/month "
                "equivalent in today's money (or use zero)."
            ),
        )


def _goal_to_dict(
    goal: Goal,
    progress: dict,
    *,
    session: Session | None = None,
) -> dict:
    """Serialise a Goal + its computed progress to a JSON-safe dict."""
    out: dict = {
        "id": goal.id,
        "name": goal.name,
        "goal_type": goal.goal_type,
        "target_amount": goal.target_amount,
        "target_date": goal.target_date.isoformat() if goal.target_date else None,
        "target_metric": goal.target_metric,
        "priority": goal.priority,
        "linked_layer": goal.linked_layer,
        "linked_category": goal.linked_category,
        "chart_key": goal.chart_key,
        "progress_cadence": goal.progress_cadence,
        "user_id": goal.user_id,
        "current_value": goal.current_value,
        "notes": goal.notes,
        "pyramid_id": goal.pyramid_id,
        "tier": goal.tier,
        "time_horizon": goal.time_horizon,
        "funding_mode": goal.funding_mode,
        "activation_status": goal.activation_status,
        "activation_condition": goal.activation_condition,
        "monthly_allocation": goal.monthly_allocation,
        "allocation_priority": goal.allocation_priority,
        "interruptible": goal.interruptible,
        "sensitivity_to_returns": goal.sensitivity_to_returns,
        "goal_class": goal.goal_class,
        "recurrence_amount": goal.recurrence_amount,
        "recurrence_frequency": goal.recurrence_frequency,
        "recurrence_start": goal.recurrence_start.isoformat()
        if goal.recurrence_start
        else None,
        "recurrence_end": goal.recurrence_end.isoformat() if goal.recurrence_end else None,
        "goal_specific_inflation_rate": goal.goal_specific_inflation_rate,
        "expected_return_rate": goal.expected_return_rate,
        "starting_balance": goal.starting_balance,
        "system_priority_score": goal.system_priority_score,
        "goal_subtype": goal.goal_subtype,
        "computed_current_value": progress["current_value"],
        "computed_percentage": progress["percentage"],
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
        "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
    }
    if session is not None:
        out["inflation_resolution"] = resolve_goal_inflation(session, goal)
    return out
