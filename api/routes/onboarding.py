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
import queue
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import SQLiteSerializingSession, get_session
from api.models import AppUser, Holding, OnboardingState, PasswordTemplate, Transaction, UserContact, UserSecrets
from api.onboarding_goal_templates import build_goal_templates_response
from api.routes.transactions import upsert_user_merchant_correction_rule
from api.services.classifier_runtime import (
    effective_onboarding_resume_threshold,
    effective_onboarding_unknown_threshold,
    onboarding_should_resume_after_classify,
    user_stored_classifier_api_key_presence,
)
from api.services.email_import_flow_log import EmailImportFlowLog
from api.services.onboarding_merchant_propagation import (
    propagate_merchant_keyword_hits,
    transaction_to_canonical,
)
from api.services.onboarding_portfolio_derive import (
    portfolio_snapshot_summary,
    run_onboarding_portfolio_derivation,
)
from api.services.preclassification_identity import (
    build_self_aliases_from_names,
    display_and_aliases_for_contact_line,
)
from api.services.user_classification import (
    get_or_create_user_classification_settings,
    merge_starter_pack_for_user,
)
from pipeline import config as pipeline_cfg
from pipeline.rules_classifier import apply_spend_category_heuristics
from scraper.config_loader import BankSendersConfig, get_bank_senders_config
from scraper.discovery import (
    DiscoveredSource,
    discover_sources_iter,
    discovered_sources_to_json,
)
from scraper.gap_detector import detect_gaps
from scraper.gmail_client import GmailClient, GmailReauthRequiredError
from scraper.onboarding_orchestrator import (
    STREAM_DRAIN_CHUNK_SIZE,
    count_all_classification_unknowns,
    count_classification_unknowns,
    list_all_classification_unknown_transactions,
    list_classification_unknown_transactions,
    pause_backfill_state,
    resume_backfill_state,
    run_onboarding_backfill,
)
from scraper.pdf_passwords import (
    ARTH_PDF_INGREDIENT_DOB_ISO,
    ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID,
    ARTH_PDF_INGREDIENT_PAN,
    EMAIL_PARSER_KEY_TO_PASSWORD_TEMPLATE_KEYS,
    list_pdf_password_holder_names,
)
from scraper.source_builder import filter_redundant_nse_broker_sources, persist_scraper_sources_from_discovery
from scraper.scheduler import resume_scheduler

logger = logging.getLogger(__name__)

router = APIRouter()

# Order for chunk backfill: savings first, then credit cards, then brokers (Track 2 wizard).
_SOURCE_TYPE_RANK: dict[str, int] = {"savings": 0, "credit_card": 1, "broker": 2}


def _ordered_backfill_sources(bank: BankSendersConfig) -> list[dict[str, str]]:
    """Unique ``source_key`` values from bank config, ordered for the onboarding wizard."""
    best: dict[str, tuple[int, str]] = {}
    for _sender, cfg in bank.items():
        st_raw = str(cfg.get("source_type") or "unknown").lower().strip()
        rank = _SOURCE_TYPE_RANK.get(st_raw, 5)
        for acct in cfg.get("accounts", {}).values():
            sk = str(acct.get("source_key") or "").strip()
            if not sk:
                continue
            prev = best.get(sk)
            if prev is None or rank < prev[0]:
                best[sk] = (rank, st_raw)
    ordered = sorted(best.items(), key=lambda kv: (kv[1][0], kv[0]))
    return [{"source_key": sk, "source_type": st} for sk, (_r, st) in ordered]


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
    """Return an authenticated Gmail client or raise HTTP errors.

    All ``detail`` strings are end-user copy (no file paths, no REST paths). Use
    503 — not 401 — so the dashboard does not treat Gmail issues as a lost
    Arth session.
    """
    client = GmailClient()
    try:
        client.authenticate(allow_interactive_oauth=False)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail is not set up on this device yet. If you are self-hosting Arth, add Google API "
                "credentials as described in the docs, then use “Connect Gmail” again."
            ),
        ) from None
    except GmailReauthRequiredError:
        raise HTTPException(
            status_code=503,
            detail=(
                "Gmail is not connected to Arth on this computer. Go back to the previous step, "
                "tap “Connect Gmail,” and complete the Google sign-in. When the browser is done, "
                "return here and tap “Re-scan.”"
            ),
        ) from None
    except Exception as e:
        logger.exception("Gmail authentication failed")
        raise HTTPException(
            status_code=503,
            detail=(
                "We could not sign in to Gmail. Check your connection, use “Connect Gmail” again, "
                "or confirm you are still signed in to Google on this machine."
            ),
        ) from e
    return client


class OnboardingStateResponse(BaseModel):
    """Serializable wizard snapshot for the dashboard."""

    current_step: str
    completed_steps: list[Any]
    discovery_results: dict[str, Any]
    backfill_progress: dict[str, Any]
    persist_sources_status: str
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
        persist_sources_status=row.persist_sources_status,
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
        persist_sources_status=row.persist_sources_status,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("/backfill-sources")
def onboarding_backfill_sources(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[dict[str, str]]:
    """Pipeline ``source_key`` list derived from the user's bank-sender config (wizard order)."""
    bank = get_bank_senders_config(session, current_user)
    return _ordered_backfill_sources(bank)


class PasswordRequirementRow(BaseModel):
    """One PDF password recipe the wizard should collect ingredients for."""

    parser_key: str
    display_name: str
    required_fields: list[str]
    notes: str | None = None


class PasswordIngredientsBody(BaseModel):
    """User-supplied values merged into encrypted ``UserSecrets`` for template-derived PDF passwords.

    TODO: Extend with optional literal PDF password fields (per env-key / parser_key) so users
    never need manual .env or SQLite edits when ingredient derivation fails — persist alongside
    ``ARTH_PDF_INGREDIENT_*`` and resolve via ``scraper.secrets_context.resolve_secret_env``.
    """

    pan: str | None = Field(default=None, description="Income-tax PAN (stored uppercase).")
    dob_iso: str | None = Field(default=None, description="Date of birth as YYYY-MM-DD for DDMM derivation.")
    hdfc_customer_id: str | None = Field(
        default=None,
        description="HDFC Bank net-banking customer ID (combined statement PDF password).",
    )


class PasswordIngredientsSaved(BaseModel):
    """Current PDF ingredient values from ``UserSecrets`` (for Config / import UI hydration)."""

    pan: str | None = None
    dob_iso: str | None = None
    hdfc_customer_id: str | None = None


@router.get("/password-ingredients", response_model=PasswordIngredientsSaved)
def get_password_ingredients_saved(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> PasswordIngredientsSaved:
    """Return saved PAN / DOB / HDFC customer ID so the wizard survives refresh."""
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == current_user)).first()
    if row is None or not row.secrets_json:
        return PasswordIngredientsSaved()
    try:
        data = json.loads(row.secrets_json)
    except json.JSONDecodeError:
        return PasswordIngredientsSaved()
    if not isinstance(data, dict):
        return PasswordIngredientsSaved()
    pan_raw = data.get(ARTH_PDF_INGREDIENT_PAN)
    dob_raw = data.get(ARTH_PDF_INGREDIENT_DOB_ISO)
    cid_raw = data.get(ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID)
    pan = str(pan_raw).strip().upper() if pan_raw else None
    dob_iso = str(dob_raw).strip()[:10] if dob_raw else None
    if dob_iso == "":
        dob_iso = None
    digits = "".join(c for c in str(cid_raw) if c.isdigit()) if cid_raw else ""
    hdfc_customer_id = digits if digits else None
    if pan == "":
        pan = None
    return PasswordIngredientsSaved(pan=pan, dob_iso=dob_iso, hdfc_customer_id=hdfc_customer_id)


@router.get("/password-requirements", response_model=list[PasswordRequirementRow])
def onboarding_password_requirements(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> list[PasswordRequirementRow]:
    """Return password templates for bank senders seen during discovery (non-empty mailboxes only)."""
    row = _get_or_create_state(session, current_user)
    envelope = _parse_json_object(row.discovery_results_json, {})
    sources_raw = envelope.get("sources")
    if not isinstance(sources_raw, list):
        return []
    sources_raw = filter_redundant_nse_broker_sources(sources_raw)

    bank = get_bank_senders_config(session, current_user)
    needed_keys: set[str] = set()
    for item in sources_raw:
        if not isinstance(item, dict):
            continue
        sender = str(item.get("sender_email") or "").strip().lower()
        est_raw = item.get("email_count_estimate", 0)
        try:
            est = int(est_raw)
        except (TypeError, ValueError):
            est = 0
        if est <= 0:
            continue
        cfg = bank.get(sender)
        if not isinstance(cfg, dict):
            continue
        pk = str(cfg.get("parser_key") or "").strip()
        if pk in EMAIL_PARSER_KEY_TO_PASSWORD_TEMPLATE_KEYS:
            needed_keys.update(EMAIL_PARSER_KEY_TO_PASSWORD_TEMPLATE_KEYS[pk])

    if not needed_keys:
        return []

    stmt = select(PasswordTemplate).where(col(PasswordTemplate.parser_key).in_(sorted(needed_keys)))
    rows = session.exec(stmt).all()
    out: list[PasswordRequirementRow] = []
    for tmpl in rows:
        try:
            fields_raw = json.loads(tmpl.required_fields_json)
        except json.JSONDecodeError:
            fields_raw = []
        if not isinstance(fields_raw, list):
            fields_raw = []
        out.append(
            PasswordRequirementRow(
                parser_key=tmpl.parser_key,
                display_name=tmpl.display_name,
                required_fields=[str(x) for x in fields_raw],
                notes=tmpl.notes,
            )
        )
    out.sort(key=lambda r: (r.display_name.lower(), r.parser_key))
    return out


@router.get("/pdf-password-name-preview")
def onboarding_pdf_password_name_preview(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Return ordered name strings used for FIRST4+DDMM PDF passwords (profile + optional override)."""
    return {"name_strings": list_pdf_password_holder_names(session, current_user)}


@router.post("/password-ingredients")
def onboarding_password_ingredients(
    body: PasswordIngredientsBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Merge PAN/DOB/account fragments into ``UserSecrets`` for PDF template resolution.

    Future: accept raw PDF password overrides here when we build the UI (see ``PasswordIngredientsBody``).
    """
    row = session.exec(select(UserSecrets).where(UserSecrets.user_id == current_user)).first()
    data: dict[str, Any] = {}
    if row is not None and row.secrets_json:
        try:
            loaded = json.loads(row.secrets_json)
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}

    if body.pan is not None:
        pan = "".join(c for c in body.pan.upper() if c.isalnum())
        if len(pan) > 10:
            raise HTTPException(status_code=400, detail="PAN must be at most 10 characters.")
        data[ARTH_PDF_INGREDIENT_PAN] = pan
    if body.dob_iso is not None:
        raw_d = body.dob_iso.strip()
        if raw_d:
            if len(raw_d) >= 10:
                parts = raw_d[:10].split("-")
                if len(parts) != 3 or len(parts[0]) != 4:
                    raise HTTPException(
                        status_code=400,
                        detail="Use date of birth as YYYY-MM-DD.",
                    )
            data[ARTH_PDF_INGREDIENT_DOB_ISO] = raw_d[:10]
        else:
            data.pop(ARTH_PDF_INGREDIENT_DOB_ISO, None)
    if body.hdfc_customer_id is not None:
        cid = "".join(c for c in body.hdfc_customer_id if c.isdigit())
        if cid:
            data[ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID] = cid
        else:
            data.pop(ARTH_PDF_INGREDIENT_HDFC_CUSTOMER_ID, None)

    payload = json.dumps(data)
    now = datetime.datetime.now(datetime.UTC)
    if row is None:
        session.add(UserSecrets(user_id=current_user, secrets_json=payload, updated_at=now))
    else:
        row.secrets_json = payload
        row.updated_at = now
        session.add(row)
    session.commit()
    return {"ok": True}


def _ndjson_line(payload: dict[str, Any]) -> bytes:
    """One UTF-8 line for ``application/x-ndjson`` streaming."""
    return (json.dumps(payload) + "\n").encode("utf-8")


def _sse_data_line(payload: dict[str, Any]) -> bytes:
    """One Server-Sent Events frame: ``data:`` JSON + blank line (RFC 8895 style)."""
    return (f"data: {json.dumps(payload, default=str)}\n\n").encode("utf-8")


def _http_exception_detail(exc: HTTPException) -> str:
    # ``detail`` is often typed as ``str`` only, but at runtime it may be a dict/list.
    d: Any = exc.detail
    if isinstance(d, str):
        return d
    return json.dumps(d)


@router.post("/discover")
def onboarding_discover(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Stream Gmail discovery progress as NDJSON (start / found / done | error).

    The request-scoped ``session`` may close when this handler returns, so the
    generator opens its own :class:`~sqlmodel.Session` for the final DB write.
    """
    bank = get_bank_senders_config(session, current_user)
    total_senders = len(sorted(bank.keys()))
    # Use the same SQLAlchemy bind as the request-scoped session so streaming discovery
    # writes go to the test engine when ``get_session`` is overridden (``get_engine()`` alone
    # would always point at the app's default SQLite file).
    write_bind = session.get_bind()

    def generate() -> Any:
        try:
            client = _gmail_client_connected()
        except HTTPException as e:
            yield _ndjson_line({"type": "error", "detail": _http_exception_detail(e)})
            return

        yield _ndjson_line({"type": "start", "total": total_senders})

        rows: list[DiscoveredSource] = []
        try:
            for index, src in enumerate(discover_sources_iter(client, bank)):
                rows.append(src)
                one_row = discovered_sources_to_json([src])[0]
                yield _ndjson_line({"type": "found", "index": index, "source": one_row})
        except Exception:
            logger.exception("Onboarding discovery failed mid-stream")
            yield _ndjson_line(
                {
                    "type": "error",
                    "detail": (
                        "We could not finish scanning your mailbox. Check your connection "
                        "and tap “Re-scan,” or use “Connect Gmail” again if sign-in expired."
                    ),
                }
            )
            return

        discovered_at = datetime.datetime.now(datetime.UTC).isoformat()
        payload_list = discovered_sources_to_json(rows)
        envelope: dict[str, Any] = {
            "discovered_at": discovered_at,
            "sources": payload_list,
        }

        with SQLiteSerializingSession(write_bind) as write_session:
            row = _get_or_create_state(write_session, current_user)
            row.discovery_results_json = json.dumps(envelope)
            row.persist_sources_status = "running"
            row.updated_at = datetime.datetime.now(datetime.UTC)
            write_session.add(row)
            write_session.commit()

        uid = current_user
        bind = write_bind

        def _run_persist_sources_bg() -> None:
            status = "done"
            try:
                client = _gmail_client_connected()
                with SQLiteSerializingSession(bind) as bg_session:
                    persist_scraper_sources_from_discovery(
                        bg_session,
                        uid,
                        client,
                        envelope,
                    )
                    bg_session.commit()
            except Exception:
                logger.exception("Background persist-sources failed for user=%r", uid)
                status = "error"
            try:
                with SQLiteSerializingSession(bind) as s2:
                    r = _get_or_create_state(s2, uid)
                    r.persist_sources_status = status
                    r.updated_at = datetime.datetime.now(datetime.UTC)
                    s2.add(r)
                    s2.commit()
            except Exception:
                logger.exception("Could not write persist_sources_status for user=%r", uid)

        threading.Thread(target=_run_persist_sources_bg, daemon=True).start()

        yield _ndjson_line({"type": "done", "discovered_at": discovered_at})

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/persist-sources")
def onboarding_persist_sources(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Write ``ScraperBankSender`` / ``ScraperAccountMapping`` from the last discovery scan.

    Call after ``POST /discover`` completes (``discovery_results`` on onboarding state).
    Samples a few Gmail messages per non-empty sender and infers last-4 → account rows
    so :func:`scraper.config_loader.get_bank_senders_config` no longer falls back to the
    empty template in ``scraper.config``.
    """
    row = _get_or_create_state(session, current_user)
    envelope = _parse_json_object(row.discovery_results_json, {})
    sources = envelope.get("sources")
    if not isinstance(sources, list) or not sources:
        raise HTTPException(
            status_code=400,
            detail="Run account discovery first so we know which email senders to configure.",
        )

    try:
        client = _gmail_client_connected()
    except HTTPException:
        raise

    try:
        summary = persist_scraper_sources_from_discovery(
            session,
            current_user,
            client,
            envelope if isinstance(envelope, dict) else {},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    row.persist_sources_status = "done"
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)
    session.commit()
    return {"ok": True, **summary}


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
    resume_after_password: bool = Field(
        default=False,
        description="Clear needs_password gate after user saved PDF password ingredients in UserSecrets.",
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
    current_phase: str | None = Field(
        default=None,
        description="statements | alerts — which tier of mail is being imported (if applicable).",
    )
    password_parser_key: str | None = Field(
        default=None,
        description="When status is needs_password, which PasswordTemplate row applies (if known).",
    )
    password_failure_message_id: str | None = Field(
        default=None,
        description="Gmail message id that failed PDF decryption (retry after fixing secrets).",
    )
    current_window_label: str | None = Field(
        default=None,
        description="Human label for the active InstaAlert Gmail window (windowed onboarding only).",
    )
    windows_total: int = Field(
        default=0,
        description="Number of date windows planned for InstaAlert import (may grow during pre-statement expansion).",
    )
    windows_completed: int = Field(
        default=0,
        description="How many InstaAlert windows have been fully drained.",
    )


def _strip_internal_keys(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def _reconcile_backfill_needs_classification(
    session: Session,
    user_id: str,
    blob: dict[str, Any],
    _unknowns_src: int,
) -> None:
    """Clear a stale ``needs_classification`` status only when the **whole** queue is empty.

    Chunk processing pauses when per-source unknowns **exceed** the configured threshold.
    While gated, ``GET …/progress`` and SSE refresh ``unknowns_pending`` for this source but
    leave ``status`` untouched until every email-sourced classification row is cleared
    across **all** pipeline accounts (matches ``POST /classify`` ``should_resume`` when
    ``ONBOARDING_RESUME_THRESHOLD`` ≤ 0).

    ``_unknowns_src`` is the per-source live count callers already merged into ``blob``;
    reconciliation keys off the global queue only.
    """
    if not blob or str(blob.get("status") or "") != "needs_classification":
        return
    if count_all_classification_unknowns(session, user_id=user_id) > 0:
        return
    stmt = list(blob.get("_pending_statement_ids") or [])
    alerts = list(blob.get("_pending_alert_ids") or [])
    if stmt:
        blob["status"] = "processing_statements"
        blob["current_phase"] = "statements"
    elif alerts:
        blob["status"] = "processing_alerts"
        blob["current_phase"] = "alerts"
    else:
        blob["status"] = "complete"
        blob["current_phase"] = None


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


_BACKFILL_LOCKS: dict[tuple[str, str], str] = {}

# Per-user threading.Event: when unset (after ``clear()``), POST /classify holds the gate so the
# onboarding backfill worker waits before ``commit`` — avoids SQLite "database is locked" when the
# SSE stream and classify both write concurrently. Default is **set** (open); classify clears while
# committing then sets again in ``finally``.
_CLASSIFY_GATES: dict[str, threading.Event] = {}


def _get_classify_gate(user_id: str) -> threading.Event:
    """Return the classify/backfill coordination Event for ``user_id`` (creates if missing)."""
    if user_id not in _CLASSIFY_GATES:
        gate = threading.Event()
        gate.set()
        _CLASSIFY_GATES[user_id] = gate
    return _CLASSIFY_GATES[user_id]


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

    req_id = uuid.uuid4().hex[:12]
    lock_key = (current_user, source_key)
    held_by = _BACKFILL_LOCKS.get(lock_key)
    if held_by is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Another import is already running for this account. "
                "Give it a minute to finish, then try again."
            ),
        )

    _BACKFILL_LOCKS[lock_key] = req_id
    try:
        return _run_backfill_locked(session, current_user, source_key, body, req_id)
    finally:
        _BACKFILL_LOCKS.pop(lock_key, None)


def _run_backfill_locked(
    session: Session,
    current_user: str,
    source_key: str,
    body: BackfillAdvanceBody,
    req_id: str,
) -> dict[str, Any]:
    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    existing = dict(all_bf.get(source_key) or {})

    flow = EmailImportFlowLog(request_id=req_id, user_id=current_user, source_key=source_key)
    detail_parts = [
        f"chunk_size={body.chunk_size}",
        f"resume_after_classification={body.resume_after_classification}",
        f"resume_after_password={body.resume_after_password}",
        f"resume_from_pause={body.resume_from_pause}",
    ]
    if body.after is not None:
        detail_parts.append(f"after={body.after.isoformat()}")
    if body.before is not None:
        detail_parts.append(f"before={body.before.isoformat()}")
    flow.write("http_request_begin", "; ".join(detail_parts))

    try:
        client = _gmail_client_connected()
    except HTTPException as e:
        flow.write("gmail_connect_failed", _http_exception_detail(e))
        raise
    flow.write("gmail_connected", "Gmail client authenticated successfully")

    def _flush_backfill_progress_live(snapshot: dict[str, Any]) -> None:
        """Let GET /progress reflect mid-request work (e.g. long InstaAlert Gmail listing)."""
        _merge_and_save_backfill(session, current_user, source_key, snapshot)
        session.commit()

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
            resume_after_password=body.resume_after_password,
            resume_from_pause=body.resume_from_pause,
            import_flow_log=flow,
            progress_commit_hook=_flush_backfill_progress_live,
        )
    except ValueError as e:
        flow.write("validation_error", str(e))
        raise HTTPException(status_code=400, detail=str(e)) from e

    merged_progress = result.progress
    _merge_and_save_backfill(session, current_user, source_key, merged_progress)
    session.commit()

    public = _strip_internal_keys(merged_progress)
    flow.write(
        "http_request_end",
        f"status={public.get('status')!r} emails_found={public.get('emails_found')!r} "
        f"emails_processed={public.get('emails_processed')!r} error={public.get('error_message')!r}",
    )
    return public


@router.get("/backfill/{source}/progress", response_model=BackfillProgressResponse)
def onboarding_backfill_progress(
    source: str,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> BackfillProgressResponse:
    """Poll backfill progress; unknowns_pending is recomputed from the DB on each GET.

    **Deprecated for live UX:** Prefer :func:`onboarding_backfill_stream` (SSE) so the
    dashboard can show per-email progress without discrete chunk polling.
    """
    source_key = source.strip()
    row = _get_or_create_state(session, current_user)
    all_bf = _parse_json_object(row.backfill_progress_json, {})
    blob = dict(all_bf.get(source_key) or {})

    unknowns_live = count_classification_unknowns(
        session, user_id=current_user, source_key=source_key
    )
    if blob:
        blob["unknowns_pending"] = unknowns_live
        _reconcile_backfill_needs_classification(session, current_user, blob, unknowns_live)
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
        current_phase=(str(blob["current_phase"]) if blob.get("current_phase") else None),
        password_parser_key=(
            str(blob["password_parser_key"]) if blob.get("password_parser_key") else None
        ),
        password_failure_message_id=(
            str(blob["password_failure_message_id"]) if blob.get("password_failure_message_id") else None
        ),
        current_window_label=(
            str(blob["current_window_label"]) if blob.get("current_window_label") else None
        ),
        windows_total=int(blob.get("windows_total") or 0),
        windows_completed=int(blob.get("windows_completed") or 0),
    )


_BACKFILL_STREAM_DONE = object()


@router.get("/backfill/{source}/stream")
def onboarding_backfill_stream(
    source: str,
    *,
    resume_after_classification: bool = Query(default=False),
    resume_after_password: bool = Query(default=False),
    resume_from_pause: bool = Query(default=False),
    after: datetime.date | None = Query(default=None),
    before: datetime.date | None = Query(default=None),
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Stream backfill progress as **Server-Sent Events** (``text/event-stream``).

    Runs the same :func:`run_onboarding_backfill` pipeline as ``POST /backfill/{source}``,
    but with a large internal chunk size so the connection stays open while Gmail messages
    are processed one-by-one. Emits:

    * ``{"type": "progress", ...}`` — after each message (counters move smoothly).
    * ``{"type": "status", "progress": {...}}`` — after each orchestrator step + DB reconcile.
    * ``{"type": "gate", "progress": {...}}`` — pause gates (password / classification / paused / error).
    * ``{"type": "complete", "progress": {...}}`` — this source finished successfully.

    **Resume:** pass the same boolean query flags as ``POST`` body (e.g.
    ``?resume_after_password=true``) after fixing secrets so a **new** stream continues
    without holding the HTTP lock during user input (avoids 409 on concurrent ``POST``).

    The request-scoped ``session`` may close when this handler returns; the generator uses
    ``session.get_bind()`` and opens its own :class:`~sqlmodel.Session` per step (same pattern
    as ``POST /discover``).
    """
    source_key = source.strip()
    if not source_key:
        raise HTTPException(status_code=400, detail="source must not be empty")

    req_id = uuid.uuid4().hex[:12]
    lock_key = (current_user, source_key)
    held_by = _BACKFILL_LOCKS.get(lock_key)
    if held_by is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Another import is already running for this account. "
                "Give it a minute to finish, then start the stream again."
            ),
        )

    write_bind = session.get_bind()
    event_queue: queue.Queue[object] = queue.Queue()

    def worker() -> None:
        _BACKFILL_LOCKS[lock_key] = req_id
        # Copy query flags into locals so the retry loop can clear them without ``nonlocal`` /
        # UnboundLocalError surprises on the first ``run_onboarding_backfill`` call.
        ra_cls = resume_after_classification
        ra_pwd = resume_after_password
        rf_pause = resume_from_pause
        ad = after
        bd = before
        try:
            try:
                client = _gmail_client_connected()
            except HTTPException as e:
                event_queue.put(
                    {"type": "error", "detail": _http_exception_detail(e), "terminal": True}
                )
                return

            flow = EmailImportFlowLog(request_id=req_id, user_id=current_user, source_key=source_key)
            flow.write("sse_stream_begin", f"resume_cls={ra_cls} resume_pwd={ra_pwd} resume_pause={rf_pause}")

            while True:
                with SQLiteSerializingSession(write_bind) as stream_session:
                    row = _get_or_create_state(stream_session, current_user)
                    all_bf = _parse_json_object(row.backfill_progress_json, {})
                    existing = dict(all_bf.get(source_key) or {})

                    def _flush_backfill_progress_live(snapshot: dict[str, Any]) -> None:
                        _get_classify_gate(current_user).wait(timeout=30)
                        _merge_and_save_backfill(stream_session, current_user, source_key, snapshot)
                        stream_session.commit()
                        event_queue.put(
                            {
                                "type": "status",
                                "progress": _strip_internal_keys(snapshot),
                            }
                        )

                    def _emit_email_progress(slice_pub: dict[str, Any]) -> None:
                        evt = {"type": "progress", **slice_pub}
                        event_queue.put(evt)

                    try:
                        result = run_onboarding_backfill(
                            session=stream_session,
                            user_id=current_user,
                            source_key=source_key,
                            gmail_client=client,
                            existing_progress=existing,
                            chunk_size=STREAM_DRAIN_CHUNK_SIZE,
                            after=ad,
                            before=bd,
                            resume_after_classification=ra_cls,
                            resume_after_password=ra_pwd,
                            resume_from_pause=rf_pause,
                            import_flow_log=flow,
                            progress_commit_hook=_flush_backfill_progress_live,
                            emit_event=_emit_email_progress,
                        )
                    except ValueError as e:
                        event_queue.put({"type": "error", "detail": str(e), "terminal": True})
                        return

                    merged = dict(result.progress)
                    unknowns_live = count_classification_unknowns(
                        stream_session, user_id=current_user, source_key=source_key
                    )
                    merged["unknowns_pending"] = unknowns_live
                    _reconcile_backfill_needs_classification(
                        stream_session, current_user, merged, unknowns_live
                    )
                    _merge_and_save_backfill(stream_session, current_user, source_key, merged)
                    _get_classify_gate(current_user).wait(timeout=30)
                    stream_session.commit()

                    public = _strip_internal_keys(merged)
                    public["unknowns_pending"] = unknowns_live
                    event_queue.put({"type": "status", "progress": public})

                    st = str(merged.get("status") or "")
                    if st in ("needs_password", "needs_classification", "paused", "error"):
                        event_queue.put({"type": "gate", "progress": public})
                        flow.write("sse_stream_end", f"gate status={st!r}")
                        return
                    if st == "complete":
                        event_queue.put({"type": "complete", "progress": public})
                        flow.write("sse_stream_end", "complete")
                        return
                    if st in ("processing_statements", "processing_alerts", "processing"):
                        ra_cls = False
                        ra_pwd = False
                        rf_pause = False
                        continue
                    flow.write("sse_stream_end", f"unexpected_status={st!r}")
                    event_queue.put({"type": "error", "detail": f"Unexpected status {st!r}.", "terminal": True})
                    return
        finally:
            _BACKFILL_LOCKS.pop(lock_key, None)
            event_queue.put(_BACKFILL_STREAM_DONE)

    threading.Thread(target=worker, daemon=True).start()

    def generate() -> Any:
        while True:
            item = event_queue.get()
            if item is _BACKFILL_STREAM_DONE:
                break
            assert isinstance(item, dict)
            yield _sse_data_line(item)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


class PreclassificationRawResponse(BaseModel):
    """Raw form fields last saved from ``POST /preclassification`` (wizard resume)."""

    first_name: str = ""
    last_name: str = ""
    extra_aliases: list[str] = Field(default_factory=list)
    account_hints: list[str] = Field(default_factory=list)
    family_names: list[str] = Field(default_factory=list)
    friend_names: list[str] = Field(default_factory=list)


@router.get("/preclassification", response_model=PreclassificationRawResponse)
def get_preclassification_saved(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> PreclassificationRawResponse:
    """Return the last saved pre-classification inputs (empty until the user saves once)."""
    row = _get_or_create_state(session, current_user)
    session.commit()
    raw = _parse_json_object(row.preclassification_raw_json, {})
    if not isinstance(raw, dict):
        raw = {}
    fn = raw.get("first_name")
    ln = raw.get("last_name")
    ea = raw.get("extra_aliases")
    ah = raw.get("account_hints")
    fam = raw.get("family_names")
    frn = raw.get("friend_names")
    return PreclassificationRawResponse(
        first_name=str(fn) if fn is not None else "",
        last_name=str(ln) if ln is not None else "",
        extra_aliases=[str(x) for x in ea] if isinstance(ea, list) else [],
        account_hints=[str(x) for x in ah] if isinstance(ah, list) else [],
        family_names=[str(x) for x in fam] if isinstance(fam, list) else [],
        friend_names=[str(x) for x in frn] if isinstance(frn, list) else [],
    )


@router.get("/preclassification/preview")
def preclassification_preview(
    first_name: str = Query(..., min_length=1, description="Given / first name(s)"),
    last_name: str = Query("", max_length=128, description="Surname(s) — may be empty"),
    extra_aliases: list[str] = Query(
        default_factory=list,
        description="Optional nicknames — repeat query key, e.g. ``?extra_aliases=A&extra_aliases=B``.",
    ),
) -> dict[str, Any]:
    """Preview ``self_name`` + ``self_aliases`` without writing to the database."""
    display, aliases = build_self_aliases_from_names(
        first_name, last_name, extra_aliases=extra_aliases
    )
    return {"self_name": display, "self_aliases": aliases}


class PreclassificationSaveBody(BaseModel):
    """Collect Layer-2 identity before parsing (Layer-1 starter pack is automatic at user init)."""

    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(default="", max_length=128)
    extra_aliases: list[str] = Field(
        default_factory=list,
        description="Optional nicknames / bank spellings merged into self_aliases.",
    )
    account_hints: list[str] = Field(
        default_factory=list,
        description=(
            "Account or card number fragments and full UPI IDs for self-transfer matching "
            "(saved to account_hints_json; used by rules classifier substring match)."
        ),
    )
    family_names: list[str] = Field(
        default_factory=list,
        description="Optional names of family members (one person per string); saved as UserContact FAMILY.",
    )
    friend_names: list[str] = Field(
        default_factory=list,
        description="Optional friend names (one person per string); saved as UserContact FRIEND.",
    )


def _replace_onboarding_contacts(
    session: Session,
    user_id: str,
    *,
    family_names: list[str],
    friend_names: list[str],
) -> None:
    """Remove prior wizard-seeded FAMILY/FRIEND rows, then insert current names."""
    for row in session.exec(select(UserContact).where(UserContact.user_id == user_id)).all():
        if row.contact_source == "ONBOARDING" and row.relationship in ("FAMILY", "FRIEND"):
            session.delete(row)

    seen_display: set[str] = set()

    def _add(lines: list[str], relationship: str) -> None:
        for raw in lines:
            display, aliases = display_and_aliases_for_contact_line(raw)
            if not display:
                continue
            key = display.casefold()
            if key in seen_display:
                continue
            seen_display.add(key)
            session.add(
                UserContact(
                    user_id=user_id,
                    display_name=display,
                    aliases_json=json.dumps(aliases),
                    relationship=relationship,
                    contact_source="ONBOARDING",
                )
            )

    _add(family_names, "FAMILY")
    _add(friend_names, "FRIEND")


@router.post("/preclassification")
def preclassification_save(
    body: PreclassificationSaveBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist ``self_name``, ``self_aliases_json``, and ``account_hints_json`` from the body."""
    merge_starter_pack_for_user(session, current_user)
    display, aliases = build_self_aliases_from_names(
        body.first_name,
        body.last_name,
        extra_aliases=body.extra_aliases,
    )
    # One list: account/card fragments and UPI handles (min 4 chars used by rules; we store as given).
    account_hints: list[str] = []
    seen_h: set[str] = set()
    for h in body.account_hints:
        t = h.strip()
        if t and t not in seen_h:
            seen_h.add(t)
            account_hints.append(t)
    row = get_or_create_user_classification_settings(session, current_user)
    row.self_name = display
    row.self_aliases_json = json.dumps(aliases)
    row.account_hints_json = json.dumps(account_hints)
    row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(row)

    # Parsed one-per-person lines (stable order, deduped by display in _replace_onboarding_contacts).
    family_parsed: list[str] = []
    friend_parsed: list[str] = []
    seen_f: set[str] = set()
    seen_r: set[str] = set()
    for raw in body.family_names:
        d, _a = display_and_aliases_for_contact_line(raw)
        if not d:
            continue
        k = d.casefold()
        if k not in seen_f:
            seen_f.add(k)
            family_parsed.append(d)
    for raw in body.friend_names:
        d, _a = display_and_aliases_for_contact_line(raw)
        if not d:
            continue
        k = d.casefold()
        if k not in seen_r:
            seen_r.add(k)
            friend_parsed.append(d)

    _replace_onboarding_contacts(
        session,
        current_user,
        family_names=family_parsed,
        friend_names=friend_parsed,
    )

    state_row = _get_or_create_state(session, current_user)
    state_row.preclassification_raw_json = json.dumps(
        {
            "first_name": body.first_name,
            "last_name": body.last_name,
            "extra_aliases": list(body.extra_aliases),
            "account_hints": account_hints,
            "family_names": family_parsed,
            "friend_names": friend_parsed,
        }
    )
    state_row.updated_at = datetime.datetime.now(datetime.UTC)
    session.add(state_row)
    session.commit()
    return {
        "ok": True,
        "self_name": display,
        "self_aliases": aliases,
        "account_hints": account_hints,
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
    """Hints for the wizard: threshold + which classifier keys **this user saved** in ``UserSecrets``.

    Deliberately ignores server ``OPENAI_API_KEY`` / etc. in the environment so the Smart labels
    step reflects paste/remove actions only. Runtime code still uses env + secrets via
    :func:`api.services.classifier_runtime.user_has_classifier_api_key`.
    """
    ho, ha, hg = user_stored_classifier_api_key_presence(session, current_user)
    return {
        "llm_model": pipeline_cfg.LLM_MODEL,
        "has_any_api_key": ho or ha or hg,
        "has_openai_api_key": ho,
        "has_anthropic_api_key": ha,
        "has_google_api_key": hg,
        "unknown_threshold": effective_onboarding_unknown_threshold(session, current_user),
    }


# ── Phase 3b: inline classification batch ───────────────────────────────────────


def _upsert_friend_contact(
    session: Session,
    user_id: str,
    display_name: str,
    *,
    extra_aliases: list[str] | None = None,
) -> None:
    """Create or extend a FRIEND :class:`UserContact` (idempotent on ``display_name``).

    ``extra_aliases`` holds other counterparty strings for the same person (e.g. the LLM
    label on this row before the user renamed it). Those strings are merged into
    ``aliases_json`` so later imports that still use the old narration-derived label match
    :func:`~scraper.onboarding_orchestrator.count_classification_unknowns` “already
    confirmed” logic and do not re-queue sensitive LLM rows.
    """
    norm = display_name.strip()
    if not norm:
        return
    merged_upper: list[str] = []
    seen: set[str] = set()
    for raw in [norm, *(extra_aliases or [])]:
        u = " ".join((raw or "").split()).strip().upper()
        if len(u) < 2 or u in seen:
            continue
        seen.add(u)
        merged_upper.append(u)

    existing = session.exec(
        select(UserContact)
        .where(UserContact.user_id == user_id)
        .where(UserContact.relationship == "FRIEND")
        .where(UserContact.display_name == norm)
    ).first()
    if existing:
        prior = json.loads(existing.aliases_json or "[]")
        if not isinstance(prior, list):
            prior = []
        for a in merged_upper:
            if a not in prior:
                prior.append(a)
        existing.aliases_json = json.dumps(prior)
        return
    session.add(
        UserContact(
            user_id=user_id,
            display_name=norm,
            aliases_json=json.dumps(merged_upper),
            relationship="FRIEND",
            contact_source="ONBOARDING",
        )
    )


def _upsert_self_contact(session: Session, user_id: str, display_name: str) -> None:
    """Add an alias to the existing SELF UserContact, or create one."""
    norm = display_name.strip()
    if not norm:
        return
    self_row = session.exec(
        select(UserContact)
        .where(UserContact.user_id == user_id)
        .where(UserContact.relationship == "SELF")
    ).first()
    alias_probe = norm.upper()
    if self_row:
        aliases: list[str] = json.loads(self_row.aliases_json or "[]")
        if alias_probe not in aliases:
            aliases.append(alias_probe)
            self_row.aliases_json = json.dumps(aliases)
            self_row.updated_at = datetime.datetime.now(datetime.UTC)
            session.add(self_row)
    else:
        session.add(
            UserContact(
                user_id=user_id,
                display_name=norm,
                aliases_json=json.dumps([alias_probe]),
                relationship="SELF",
                contact_source="ONBOARDING",
            )
        )


def _txn_brief(t: Transaction) -> dict[str, Any]:
    return {
        "id": t.id,
        "source_statement": t.source_statement,
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
    source: str | None = Query(
        default=None,
        description="Pipeline source_key (e.g. hdfc_savings). Omit to list unknowns across all email sources.",
    ),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0, le=500_000),
) -> dict[str, Any]:
    """Return a page of unknown email-sourced transactions (flat ``transactions`` list).

    When ``source`` is omitted, rows from every ``source_statement`` are merged and ordered
    by ``txn_date`` / ``id`` (oldest first so newly imported rows land at the bottom). ``groups`` is kept empty for backward compatibility.
    """
    source_key = source.strip() if source and source.strip() else None
    if source_key:
        pending_total = count_classification_unknowns(
            session, user_id=current_user, source_key=source_key
        )
        rows = list_classification_unknown_transactions(
            session,
            user_id=current_user,
            source_key=source_key,
            limit=limit,
            offset=offset,
        )
    else:
        pending_total = count_all_classification_unknowns(session, user_id=current_user)
        rows = list_all_classification_unknown_transactions(
            session, user_id=current_user, limit=limit, offset=offset
        )

    return {
        "source": source_key,
        "offset": offset,
        "limit": limit,
        "total_transactions": len(rows),
        "pending_total": pending_total,
        "transactions": [_txn_brief(x) for x in rows],
        "groups": [],
        "unknown_threshold": effective_onboarding_unknown_threshold(session, current_user),
        "resume_threshold": effective_onboarding_resume_threshold(session, current_user),
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
    """When ``source`` is omitted, each item's transaction must be email-sourced; its ``source_statement`` is used."""

    source: str | None = Field(
        default=None,
        description="Optional pipeline source_key; if set, every item must belong to this source.",
    )
    items: list[OnboardingClassifyItem]


@router.post("/classify")
def onboarding_classify(
    body: OnboardingClassifyBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply batch corrections, learn merchant rules, and seed UserContacts from category."""
    source_key = body.source.strip() if body.source and body.source.strip() else None
    updated = 0
    rules = 0
    contacts_created = 0
    keywords_for_propagation: list[str] = []

    # Pause backfill worker commits while we touch SQLite (see ``_CLASSIFY_GATES``). Always
    # ``set()`` in ``finally`` so a failed request cannot wedge the stream forever.
    gate = _get_classify_gate(current_user)
    gate.clear()
    try:
        # ``no_autoflush``: avoid flushing dirty Transaction rows before
        # ``upsert_user_merchant_correction_rule`` runs a SELECT (that used to trigger autoflush
        # and SQLite lock fights with the SSE worker).
        with session.no_autoflush:
            for item in body.items:
                txn = session.get(Transaction, item.txn_id)
                if not txn or txn.user_id != current_user:
                    raise HTTPException(
                        status_code=404,
                        detail="We couldn't find that transaction — it may have been removed. Try refreshing?",
                    )
                if txn.source_type != "email":
                    raise HTTPException(
                        status_code=400,
                        detail="That line isn't from email alerts — only email-sourced rows can be updated here.",
                    )
                if source_key is not None and txn.source_statement != source_key:
                    raise HTTPException(
                        status_code=400,
                        detail="That transaction belongs to a different import source than the one you're fixing.",
                    )
                # Keep the pre-edit label so we can store it on UserContact aliases when the user
                # renames a friend — the next Gmail chunk may still classify that person with the
                # LLM / parser string, which must match our “already confirmed” checks.
                prior_counterparty = (txn.counterparty or "").strip()
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
                if txn.spend_category is None and txn.direction == "OUTFLOW":
                    canon = transaction_to_canonical(txn)
                    apply_spend_category_heuristics(canon)
                    txn.spend_category = canon.spend_category.value if canon.spend_category else None
                session.add(txn)
                updated += 1

                cat_upper = item.counterparty_category.strip()
                cp_label = item.counterparty.strip()

                if cat_upper == "Friends and Family":
                    _upsert_friend_contact(
                        session,
                        current_user,
                        cp_label,
                        extra_aliases=[prior_counterparty] if prior_counterparty else None,
                    )
                    contacts_created += 1
                elif cat_upper == "Self Transfer":
                    _upsert_self_contact(session, current_user, cp_label)
                    contacts_created += 1

                if item.apply_to_future:
                    kw_src = (item.merchant_rule_keyword or item.counterparty).strip().upper()
                    if len(kw_src) >= 2:
                        upsert_user_merchant_correction_rule(
                            session,
                            current_user,
                            keyword=kw_src,
                            display_name=cp_label,
                            counterparty_category=cat_upper,
                        )
                        rules += 1
                        keywords_for_propagation.append(kw_src)

        session.commit()

        auto_propagated = propagate_merchant_keyword_hits(
            session,
            current_user,
            keywords=keywords_for_propagation,
            exclude_txn_ids={it.txn_id for it in body.items},
        )
        if auto_propagated:
            session.commit()
    finally:
        gate.set()

    remaining = count_all_classification_unknowns(session, user_id=current_user)
    resume_thresh = effective_onboarding_resume_threshold(session, current_user)

    return {
        "status": "ok",
        "updated": updated,
        "rules_upserted": rules,
        "contacts_created": contacts_created,
        "remaining_unknowns": remaining,
        "resume_threshold": resume_thresh,
        "should_resume": onboarding_should_resume_after_classify(remaining, resume_thresh),
        "auto_propagated": auto_propagated,
    }


@router.post("/portfolio-derive")
def onboarding_portfolio_derive(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Link orphan investment rows, derive ICICI Direct MF/equity holdings from ledger history, ingest.

    Idempotent — safe to call after broker Gmail backfill or when the user lands on the
    portfolio summary step.
    """
    return run_onboarding_portfolio_derivation(session, current_user)


@router.get("/portfolio-snapshot")
def onboarding_portfolio_snapshot(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Broker-slice holdings counts and top rows for the onboarding portfolio summary UI."""
    return portfolio_snapshot_summary(session, current_user)


@router.get("/holdings-coverage")
def onboarding_holdings_coverage(
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, bool]:
    """True when the user already has at least one ``Holding`` row (portfolio layer).

    Used by the Coverage onboarding step to decide whether to show the manual
    portfolio upload fallback when Gmail never yielded broker/fund emails.
    """
    cnt = session.exec(
        select(func.count()).select_from(Holding).where(Holding.user_id == current_user)
    ).one()
    return {"has_holding_data": cnt > 0}


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
    # Background Gmail polling was withheld until setup completed — activate it now.
    resume_scheduler()
    return {"ok": True, "current_step": row.current_step}
