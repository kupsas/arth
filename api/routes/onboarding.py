"""
Onboarding wizard API (Track 2).

State endpoints persist :class:`~api.models.OnboardingState`. Discovery and
chunk-based backfill use ``scraper.discovery`` and ``scraper.onboarding_orchestrator``.
Phase 3 adds pre-classification identity, inline unknown batching + classify,
and optional per-user LLM keys (see ``POST /api/onboarding/api-key``).
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from api.auth import get_current_user
from api.database import get_session
from api.models import AppUser, OnboardingState, Transaction, UserSecrets
from api.onboarding_goal_templates import build_goal_templates_response
from api.routes.transactions import upsert_user_merchant_correction_rule
from api.services.classifier_runtime import (
    effective_onboarding_unknown_threshold,
    user_has_classifier_api_key,
)
from api.services.preclassification_identity import build_self_aliases_from_names
from api.services.user_classification import (
    get_or_create_user_classification_settings,
    merge_starter_pack_for_user,
)
from pipeline import config as pipeline_cfg
from scraper.config_loader import get_bank_senders_config
from scraper.discovery import discover_sources, discovered_sources_to_json
from scraper.gap_detector import detect_gaps
from scraper.gmail_client import GmailClient, GmailReauthRequiredError
from scraper.onboarding_orchestrator import (
    count_classification_unknowns,
    list_classification_unknown_transactions,
    pause_backfill_state,
    resume_backfill_state,
    run_onboarding_backfill,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _parse_json_object(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except json.JSONDecodeError:
        return default


def _get_or_create_state(session: Session, user_id: str) -> OnboardingState:
    row = session.exec(select(OnboardingState).where(OnboardingState.user_id == user_id)).first()
    if row:
        return row
    row = OnboardingState(user_id=user_id)
    session.add(row)
    session.flush()
    return row


def _gmail_client_connected() -> GmailClient:
    """Return an authenticated Gmail client or raise HTTP errors."""
    client = GmailClient()
    try:
        client.authenticate(allow_interactive_oauth=False)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except GmailReauthRequiredError as e:
        raise HTTPException(
            status_code=401,
            detail={
                "message": str(e),
                "hint": "Complete Gmail OAuth via POST /api/scraper/oauth/init on this machine.",
            },
        ) from e
    except Exception as e:
        logger.exception("Gmail authentication failed")
        raise HTTPException(status_code=503, detail=f"Gmail authentication failed: {e}") from e
    return client


class OnboardingStateResponse(BaseModel):
    """Serializable wizard snapshot for the dashboard."""

    current_step: str
    completed_steps: list[Any]
    discovery_results: dict[str, Any]
    backfill_progress: dict[str, Any]
    created_at: str | None
    updated_at: str | None


class OnboardingStatePatch(BaseModel):
    """Partial update from the client (e.g. step change after completing a screen)."""

    current_step: str | None = None
    completed_steps: list[Any] | None = None
    discovery_results: dict[str, Any] | None = None
    backfill_progress: dict[str, Any] | None = None


@router.get("/state", response_model=OnboardingStateResponse)
def get_onboarding_state(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> OnboardingStateResponse:
    row = _get_or_create_state(session, current_user)
    session.commit()
    return OnboardingStateResponse(
        current_step=row.current_step,
        completed_steps=_parse_json_object(row.completed_steps_json, []),
        discovery_results=_parse_json_object(row.discovery_results_json, {}),
        backfill_progress=_parse_json_object(row.backfill_progress_json, {}),
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.patch("/state", response_model=OnboardingStateResponse)
def patch_onboarding_state(
    body: OnboardingStatePatch,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> OnboardingStateResponse:
    row = _get_or_create_state(session, current_user)
    data = body.model_dump(exclude_unset=True)
    if "current_step" in data and data["current_step"] is not None:
        row.current_step = data["current_step"].strip() or row.current_step
    if "completed_steps" in data and data["completed_steps"] is not None:
        row.completed_steps_json = json.dumps(data["completed_steps"])
    if "discovery_results" in data and data["discovery_results"] is not None:
        row.discovery_results_json = json.dumps(data["discovery_results"])
    if "backfill_progress" in data and data["backfill_progress"] is not None:
        row.backfill_progress_json = json.dumps(data["backfill_progress"])
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return OnboardingStateResponse(
        current_step=row.current_step,
        completed_steps=_parse_json_object(row.completed_steps_json, []),
        discovery_results=_parse_json_object(row.discovery_results_json, {}),
        backfill_progress=_parse_json_object(row.backfill_progress_json, {}),
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.post("/discover")
def onboarding_discover(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Scan Gmail for configured bank senders (fast existence + rough counts)."""
    bank = get_bank_senders_config(session, current_user)
    client = _gmail_client_connected()

    rows = discover_sources(client, bank)
    payload_list = discovered_sources_to_json(rows)
    envelope = {
        "discovered_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "sources": payload_list,
    }

    row = _get_or_create_state(session, current_user)
    row.discovery_results_json = json.dumps(envelope)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()

    return {"status": "ok", **envelope}


class BackfillAdvanceBody(BaseModel):
    """Advance chunk-based onboarding backfill."""

    chunk_size: int = Field(default=10, ge=1, le=100)
    after: datetime.date | None = Field(
        default=None,
        description="Inclusive Gmail after: date (defaults to wide historical window).",
    )
    before: datetime.date | None = Field(
        default=None,
        description="Exclusive Gmail before: date (defaults to tomorrow).",
    )
    resume_after_classification: bool = Field(
        default=False,
        description="Clear needs_classification gate after user fixed merchant rules.",
    )
    resume_from_pause: bool = Field(
        default=False,
        description="Clear paused status before processing the next chunk.",
    )


class BackfillProgressResponse(BaseModel):
    """Public progress snapshot for REST polling (Phase 2 plan schema)."""

    source: str
    status: str
    emails_found: int = 0
    emails_processed: int = 0
    transactions_parsed: int = 0
    unknowns_pending: int = 0
    error_message: str | None = None


def _strip_internal_keys(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def _merge_and_save_backfill(
    session: Session,
    user_id: str,
    source_key: str,
    progress_blob: dict[str, Any],
) -> None:
    row = _get_or_create_state(session, user_id)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    all_bf[source_key] = progress_blob
    row.backfill_progress_json = json.dumps(all_bf)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)


@router.post("/backfill/{source}")
def onboarding_backfill(
    source: str,
    body: BackfillAdvanceBody | None = None,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Process up to ``chunk_size`` Gmail messages for ``source`` (pipeline source_key)."""
    body = body or BackfillAdvanceBody()
    source_key = source.strip()
    if not source_key:
        raise HTTPException(status_code=400, detail="source must not be empty")

    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    existing = dict(all_bf.get(source_key) or {})

    client = _gmail_client_connected()

    try:
        result = run_onboarding_backfill(
            session=session,
            user_id=current_user,
            source_key=source_key,
            gmail_client=client,
            existing_progress=existing,
            chunk_size=body.chunk_size,
            after=body.after,
            before=body.before,
            resume_after_classification=body.resume_after_classification,
            resume_from_pause=body.resume_from_pause,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    merged_progress = result.progress
    _merge_and_save_backfill(session, current_user, source_key, merged_progress)
    session.commit()

    public = _strip_internal_keys(merged_progress)
    return public


@router.get("/backfill/{source}/progress", response_model=BackfillProgressResponse)
def onboarding_backfill_progress(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> BackfillProgressResponse:
    """Poll backfill progress; unknowns_pending is recomputed from the DB on each GET."""
    source_key = source.strip()
    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    blob = dict(all_bf.get(source_key) or {})

    unknowns_live = count_classification_unknowns(
        session, user_id=current_user, source_key=source_key
    )
    if blob:
        blob["unknowns_pending"] = unknowns_live
        all_bf[source_key] = blob
        row.backfill_progress_json = json.dumps(all_bf)
        row.updated_at = datetime.datetime.now(datetime.UTC)
        session.add(row)
        session.commit()

    status = str(blob.get("status") or "idle")
    return BackfillProgressResponse(
        source=source_key,
        status=status,
        emails_found=int(blob.get("emails_found") or 0),
        emails_processed=int(blob.get("emails_processed") or 0),
        transactions_parsed=int(blob.get("transactions_parsed") or 0),
        unknowns_pending=unknowns_live,
        error_message=blob.get("error_message"),
    )


@router.post("/backfill/{source}/pause")
def onboarding_backfill_pause(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Pause chunk processing — the next POST /backfill must pass resume_from_pause=true."""
    source_key = source.strip()
    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    blob = dict(all_bf.get(source_key) or {})
    blob = pause_backfill_state(blob)
    all_bf[source_key] = blob
    row.backfill_progress_json = json.dumps(all_bf)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return _strip_internal_keys(blob)


@router.post("/backfill/{source}/resume")
def onboarding_backfill_resume_state(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Clear paused-only status so POST /backfill can run the next chunk."""
    source_key = source.strip()
    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    blob = dict(all_bf.get(source_key) or {})
    blob = resume_backfill_state(blob)
    all_bf[source_key] = blob
    row.backfill_progress_json = json.dumps(all_bf)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return _strip_internal_keys(blob)


# ── Phase 3a: pre-classification (self name + aliases; starter pack is DB init) ─


@router.get("/preclassification/preview")
def preclassification_preview(
    first_name: str = Query(..., min_length=1, description="Given / first name(s)"),
    last_name: str = Query("", max_length=128, description="Surname(s) — may be empty"),
) -> dict[str, Any]:
    """Preview ``self_name`` + ``self_aliases`` without writing to the database."""
    display, aliases = build_self_aliases_from_names(first_name, last_name, extra_aliases=[])
    return {"self_name": display, "self_aliases": aliases}


class PreclassificationSaveBody(BaseModel):
    """Collect Layer-2 identity before parsing (Layer-1 starter pack is automatic at user init)."""

    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(default="", max_length=128)
    extra_aliases: list[str] = Field(
        default_factory=list,
        description="Optional nicknames / bank spellings merged into self_aliases.",
    )


@router.post("/preclassification")
def preclassification_save(
    body: PreclassificationSaveBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist ``self_name`` + ``self_aliases_json`` derived from first/last (+ extras)."""
    merge_starter_pack_for_user(session, current_user)
    display, aliases = build_self_aliases_from_names(
        body.first_name,
        body.last_name,
        extra_aliases=body.extra_aliases,
    )
    row = get_or_create_user_classification_settings(session, current_user)
    row.self_name = display
    row.self_aliases_json = json.dumps(aliases)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {
        "ok": True,
        "self_name": display,
        "self_aliases": aliases,
        "starter_pack": "Merchant starter rules are merged at user init — no action required here.",
    }


# ── Phase 3c: optional per-user LLM keys (encrypted JSON in ``UserSecrets``) ─────


class OnboardingClassifierApiKeyBody(BaseModel):
    """Non-empty strings overwrite; ``null`` / omitted leaves that provider unchanged."""

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None


@router.post("/api-key")
def onboarding_store_classifier_api_keys(
    body: OnboardingClassifierApiKeyBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Merge classifier keys into ``UserSecrets`` (same encrypted store as PDF passwords)."""
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == current_user)).first()
    data: dict[str, str] = {}
    if row and row.secrets_json:
        try:
            loaded = json.loads(row.secrets_json)
            if isinstance(loaded, dict):
                data = {str(k): str(v) for k, v in loaded.items()}
        except json.JSONDecodeError:
            data = {}

    touched: list[str] = []
    if body.openai_api_key is not None:
        v = body.openai_api_key.strip()
        if v:
            data["OPENAI_API_KEY_FOR_CLASSIFIER"] = v
            touched.append("OPENAI_API_KEY_FOR_CLASSIFIER")
        else:
            data.pop("OPENAI_API_KEY_FOR_CLASSIFIER", None)
            data.pop("OPENAI_API_KEY", None)
    if body.anthropic_api_key is not None:
        v = body.anthropic_api_key.strip()
        if v:
            data["ANTHROPIC_API_KEY_FOR_CLASSIFIER"] = v
            touched.append("ANTHROPIC_API_KEY_FOR_CLASSIFIER")
        else:
            data.pop("ANTHROPIC_API_KEY_FOR_CLASSIFIER", None)
            data.pop("ANTHROPIC_API_KEY", None)
    if body.google_api_key is not None:
        v = body.google_api_key.strip()
        if v:
            data["GOOGLE_API_KEY_FOR_CLASSIFIER"] = v
            touched.append("GOOGLE_API_KEY_FOR_CLASSIFIER")
        else:
            data.pop("GOOGLE_API_KEY_FOR_CLASSIFIER", None)
            data.pop("GOOGLE_API_KEY", None)

    payload = json.dumps(data)
    if row is None:
        row = UserSecrets(user_id=current_user, secrets_json=payload)
    else:
        row.secrets_json = payload
        row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {"ok": True, "keys_updated": touched}


@router.get("/classifier-status")
def onboarding_classifier_status(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Lightweight hints for the wizard (threshold + whether any LLM key is available)."""
    return {
        "llm_model": pipeline_cfg.LLM_MODEL,
        "has_any_api_key": user_has_classifier_api_key(session, current_user),
        "unknown_threshold": effective_onboarding_unknown_threshold(session, current_user),
    }


# ── Phase 3b: inline classification batch ───────────────────────────────────────


def _txn_brief(t: Transaction) -> dict[str, Any]:
    return {
        "id": t.id,
        "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "amount": t.amount,
        "direction": t.direction,
        "channel": t.channel,
        "raw_description": t.raw_description,
        "txn_type": t.txn_type,
        "upi_type": t.upi_type,
        "counterparty": t.counterparty,
        "counterparty_category": t.counterparty_category,
        "spend_category": t.spend_category,
    }


@router.get("/unknowns")
def onboarding_list_unknowns(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
    source: str = Query(..., min_length=1, description="Pipeline source_key, e.g. hdfc_savings"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Return unknown email-sourced transactions grouped by normalised narration."""
    source_key = source.strip()
    rows = list_classification_unknown_transactions(
        session, user_id=current_user, source_key=source_key, limit=limit
    )
    buckets: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        fp = " ".join((t.raw_description or "").split()).upper()
        if not fp:
            fp = f"__empty__:{t.id}"
        buckets[fp].append(t)

    groups_out: list[dict[str, Any]] = []
    for fp, members in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        groups_out.append(
            {
                "fingerprint": fp,
                "count": len(members),
                "sample_raw_description": members[0].raw_description,
                "transactions": [_txn_brief(x) for x in members],
            }
        )

    return {
        "source": source_key,
        "total_transactions": len(rows),
        "groups": groups_out,
        "unknown_threshold": effective_onboarding_unknown_threshold(session, current_user),
    }


class OnboardingClassifyItem(BaseModel):
    """One user correction during an onboarding classification pause."""

    txn_id: int
    counterparty: str = Field(min_length=1)
    counterparty_category: str = Field(min_length=1)
    spend_category: str | None = None
    txn_type: str | None = None
    upi_type: str | None = None
    apply_to_future: bool = False
    merchant_rule_keyword: str | None = Field(
        default=None,
        description="Optional narration substring for the merchant rule (else counterparty is used).",
    )


class OnboardingClassifyBody(BaseModel):
    source: str = Field(min_length=1)
    items: list[OnboardingClassifyItem]


@router.post("/classify")
def onboarding_classify(
    body: OnboardingClassifyBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply batch corrections, optionally learn merchant rules, then caller resumes backfill."""
    source_key = body.source.strip()
    updated = 0
    rules = 0
    for item in body.items:
        txn = session.get(Transaction, item.txn_id)
        if not txn or txn.user_id != current_user:
            raise HTTPException(status_code=404, detail=f"Transaction {item.txn_id} not found")
        if txn.source_statement != source_key or txn.source_type != "email":
            raise HTTPException(
                status_code=400,
                detail=f"Transaction {item.txn_id} is not an email row for source {source_key!r}",
            )
        txn.counterparty = item.counterparty.strip()
        txn.counterparty_category = item.counterparty_category.strip()
        if item.spend_category is not None:
            txn.spend_category = item.spend_category.strip() or None
        if item.txn_type is not None:
            txn.txn_type = item.txn_type.strip() or None
        if item.upi_type is not None:
            txn.upi_type = item.upi_type.strip() or None
        txn.classification_source = "USER_REVIEWED"
        txn.updated_at = datetime.datetime.now(datetime.UTC)
        session.add(txn)
        updated += 1

        if item.apply_to_future:
            kw_src = (item.merchant_rule_keyword or item.counterparty).strip().upper()
            if len(kw_src) >= 2:
                upsert_user_merchant_correction_rule(
                    session,
                    current_user,
                    keyword=kw_src,
                    display_name=item.counterparty.strip(),
                    counterparty_category=item.counterparty_category.strip(),
                )
                rules += 1

    session.commit()
    return {
        "status": "ok",
        "updated": updated,
        "rules_upserted": rules,
        "resume_hint": (
            "When backfill status is needs_classification, call "
            "POST /api/onboarding/backfill/{source} with resume_after_classification=true "
            "to continue chunk processing."
        ),
    }


@router.get("/gaps")
def onboarding_gaps(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Run :func:`scraper.gap_detector.detect_gaps` over the user's bank-sender config."""
    bank = get_bank_senders_config(session, current_user)
    reports = detect_gaps(session, current_user, bank)
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "reports": reports,
    }


@router.get("/goal-templates")
def onboarding_goal_templates(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
    target_amount: float | None = Query(
        default=None,
        description="Target in today's rupees (optional; with years + template_id for FV preview).",
    ),
    years: float | None = Query(
        default=None,
        ge=0.0,
        le=80.0,
        description="Horizon in years (optional, pairs with target_amount).",
    ),
    template_id: str | None = Query(
        default=None,
        description="When set with target_amount+years, only this template gets a preview block.",
    ),
) -> dict[str, Any]:
    _ = current_user
    return build_goal_templates_response(
        session,
        target_amount=target_amount,
        years=years,
        template_id=template_id,
    )


@router.post("/complete")
def onboarding_complete(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark wizard finished and align with first-run ``setup_completed`` when applicable."""
    row = _get_or_create_state(session, current_user)
    row.current_step = "completed"
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)

    user_row = session.exec(select(AppUser).where(AppUser.username == current_user)).first()
    if user_row and user_row.setup_completed_at is None:
        user_row.setup_completed_at = datetime.datetime.now(datetime.UTC)
        session.add(user_row)

    session.commit()
    return {"ok": True, "current_step": row.current_step}
