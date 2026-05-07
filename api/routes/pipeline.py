"""
Pipeline trigger, status, and statement upload endpoints.

POST /api/pipeline/run       — kick off a pipeline run in a background thread
GET  /api/pipeline/runs      — list past runs (paginated)
GET  /api/pipeline/runs/{id} — single run detail (for polling status)
POST /api/pipeline/upload    — upload a statement file and auto-run the pipeline
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import SQLiteSerializingSession, get_engine, get_session
from api.models import PipelineRun

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Upload-run SSE progress (in-memory; one writer per background import thread) ──
_upload_progress_lock = threading.Lock()
# run_id -> { phase, user_id, ... } — cleared when the upload thread finishes.
_upload_progress: dict[int, dict[str, Any]] = {}


def _upload_progress_set(run_id: int, payload: dict[str, Any]) -> None:
    with _upload_progress_lock:
        cur = _upload_progress.setdefault(run_id, {})
        cur.update(payload)


def _upload_progress_snapshot(run_id: int) -> dict[str, Any] | None:
    with _upload_progress_lock:
        row = _upload_progress.get(run_id)
        return dict(row) if row else None


def _upload_progress_clear(run_id: int) -> None:
    with _upload_progress_lock:
        _upload_progress.pop(run_id, None)


# ───────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ───────────────────────────────────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    source_key: str = "all"
    llm_model: str = "auto"


class PipelineRunResponse(BaseModel):
    """Returned immediately when a run is triggered."""
    run_ids: list[int]
    message: str


class PipelineRunDetail(BaseModel):
    id: int
    source_key: str
    llm_model: str
    txn_count: int
    new_count: int
    status: str
    txn_date_min: str | None
    txn_date_max: str | None
    started_at: str
    completed_at: str | None
    error_message: str | None
    # Classification review queue size for this upload (statement imports only).
    unknowns_count: int | None = None


# ───────────────────────────────────────────────────────────────────────────
# POST /run  — trigger a pipeline run in the background
# ───────────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=PipelineRunResponse)
def trigger_pipeline_run(
    body: PipelineRunRequest,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Start a pipeline run in a background thread.

    Returns immediately with the run ID(s) so the client can poll for status.
    """
    from pipeline import config

    source_configs = config.get_source_configs(current_user, session)
    valid_keys = set(source_configs.keys())
    allowed = valid_keys | {"all"}
    if body.source_key not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(
                "That bank source isn't set up for your account yet. "
                f"Configured sources: {', '.join(sorted(allowed))}."
            ),
        )
    if body.source_key == "all" and not valid_keys:
        raise HTTPException(
            status_code=400,
            detail="No bank sources are set up for your account yet. Add one under Settings.",
        )

    # Determine which sources to run
    if body.source_key == "all":
        source_keys = sorted(valid_keys)
    else:
        source_keys = [body.source_key]

    # Create placeholder PipelineRun rows so we can return IDs immediately
    run_ids = []
    for sk in source_keys:
        run = PipelineRun(
            source_key=sk,
            llm_model=body.llm_model,
            status="running",
        )
        session.add(run)
        session.flush()
        run_ids.append(run.id)

    session.commit()

    # Kick off the actual pipeline work in a background thread
    thread = threading.Thread(
        target=_run_pipeline_background,
        args=(run_ids, source_keys, body.llm_model, current_user),
        daemon=True,
    )
    thread.start()

    return PipelineRunResponse(
        run_ids=run_ids,
        message=f"Import started for {body.source_key} ({len(source_keys)} source(s)). "
        "You can leave this page — we'll keep working in the background.",
    )


# ───────────────────────────────────────────────────────────────────────────
# GET /runs  — list past pipeline runs
# ───────────────────────────────────────────────────────────────────────────

@router.get("/runs", response_model=list[PipelineRunDetail])
def list_pipeline_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    *,
    session: Session = Depends(get_session),
):
    """List pipeline runs, most recent first."""
    query = (
        select(PipelineRun)
        .order_by(col(PipelineRun.started_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    runs = session.exec(query).all()
    return [_run_to_detail(r, unknowns_count=None) for r in runs]


# ───────────────────────────────────────────────────────────────────────────
# GET /runs/{id}  — single run detail
# ───────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}", response_model=PipelineRunDetail)
def get_pipeline_run(
    run_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Get details of a single pipeline run (useful for polling status)."""
    from pipeline import config

    run = session.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Couldn't find that import run.")
    user_sources = config.get_source_configs(current_user, session)
    if run.source_key not in user_sources:
        raise HTTPException(status_code=403, detail="That import run isn't linked to your account.")
    unknowns: int | None = None
    if run.status == "completed":
        from scraper.onboarding_orchestrator import count_pipeline_run_classification_unknowns

        unknowns = count_pipeline_run_classification_unknowns(
            session, user_id=current_user, run_id=run_id
        )
    return _run_to_detail(run, unknowns_count=unknowns)


@router.get("/runs/{run_id}/stream")
async def stream_pipeline_run_progress(
    run_id: int,
    *,
    current_user: str = Depends(get_current_user),
):
    """Server-Sent Events for one upload import: parse → dedupe → classify → complete."""
    from pipeline import config

    engine = get_engine()
    with SQLiteSerializingSession(engine) as session:
        run = session.get(PipelineRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Couldn't find that import run.")
        user_sources = config.get_source_configs(current_user, session)
        if run.source_key not in user_sources:
            raise HTTPException(status_code=403, detail="That import run isn't linked to your account.")

    async def gen():
        last_json: str | None = None
        idle_ticks = 0
        while True:
            snap = _upload_progress_snapshot(run_id)
            if snap is None:
                with SQLiteSerializingSession(engine) as s2:
                    run2 = s2.get(PipelineRun, run_id)
                    if run2 and run2.status in ("completed", "failed"):
                        payload: dict[str, Any] = {
                            "phase": "complete" if run2.status == "completed" else "error",
                            "run_status": run2.status,
                            "error_message": run2.error_message,
                        }
                        if run2.status == "completed":
                            from scraper.onboarding_orchestrator import (
                                count_pipeline_run_classification_unknowns,
                            )

                            payload["unknowns_count"] = count_pipeline_run_classification_unknowns(
                                s2, user_id=current_user, run_id=run_id
                            )
                            payload["txn_count"] = run2.txn_count
                            payload["new_count"] = run2.new_count
                        line = json.dumps(payload)
                        if line != last_json:
                            yield f"data: {line}\n\n"
                            last_json = line
                        break
                idle_ticks += 1
                if idle_ticks > 600:
                    yield f"data: {json.dumps({'phase': 'timeout', 'message': 'No progress updates — try refreshing the run.'})}\n\n"
                    break
            else:
                if snap.get("user_id") != current_user:
                    yield f"data: {json.dumps({'phase': 'error', 'message': 'Forbidden'})}\n\n"
                    break
                line = json.dumps(snap)
                if line != last_json:
                    yield f"data: {line}\n\n"
                    last_json = line
                if snap.get("phase") in ("complete", "error"):
                    break
            await asyncio.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _txn_brief_for_run(t: Any) -> dict[str, Any]:
    """Match :func:`api.routes.onboarding._txn_brief` shape for the review queue UI."""
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


@router.get("/runs/{run_id}/unknowns")
def list_pipeline_run_unknowns(
    run_id: int,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0, le=500_000),
) -> dict[str, Any]:
    """Paged unknowns for one statement upload run (same envelope as onboarding unknowns)."""
    from pipeline import config

    from api.services.classifier_runtime import (
        effective_onboarding_resume_threshold,
        effective_onboarding_unknown_threshold,
    )
    from scraper.onboarding_orchestrator import (
        count_pipeline_run_classification_unknowns,
        list_pipeline_run_classification_unknown_transactions,
    )

    run = session.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Couldn't find that import run.")
    user_sources = config.get_source_configs(current_user, session)
    if run.source_key not in user_sources:
        raise HTTPException(status_code=403, detail="That import run isn't linked to your account.")

    pending_total = count_pipeline_run_classification_unknowns(
        session, user_id=current_user, run_id=run_id
    )
    rows = list_pipeline_run_classification_unknown_transactions(
        session, user_id=current_user, run_id=run_id, limit=limit, offset=offset
    )
    resume_thresh = effective_onboarding_resume_threshold(session, current_user)
    return {
        "source": None,
        "pipeline_run_id": run_id,
        "offset": offset,
        "limit": limit,
        "total_transactions": len(rows),
        "pending_total": pending_total,
        "transactions": [_txn_brief_for_run(x) for x in rows],
        "groups": [],
        "unknown_threshold": effective_onboarding_unknown_threshold(session, current_user),
        "resume_threshold": resume_thresh,
    }


class PipelineRunClassifyItem(BaseModel):
    """One user correction for a statement-upload row."""

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


class PipelineRunClassifyBody(BaseModel):
    items: list[PipelineRunClassifyItem]


@router.post("/runs/{run_id}/classify")
def pipeline_run_classify(
    run_id: int,
    body: PipelineRunClassifyBody,
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply corrections to transactions from one upload run (statement rows only)."""
    from pipeline import config

    from api.routes.onboarding import (
        _get_classify_gate,
        _upsert_friend_contact,
        _upsert_self_contact,
    )
    from api.routes.transactions import upsert_user_merchant_correction_rule
    from api.services.classifier_runtime import effective_onboarding_resume_threshold
    from api.services.onboarding_merchant_propagation import (
        propagate_merchant_keyword_hits,
        transaction_to_canonical,
    )
    from pipeline.rules_classifier import apply_spend_category_heuristics
    from scraper.onboarding_orchestrator import count_pipeline_run_classification_unknowns

    from api.models import Transaction

    run = session.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Couldn't find that import run.")
    user_sources = config.get_source_configs(current_user, session)
    if run.source_key not in user_sources:
        raise HTTPException(status_code=403, detail="That import run isn't linked to your account.")

    updated = 0
    rules = 0
    contacts_created = 0
    keywords_for_propagation: list[str] = []
    auto_propagated = 0

    gate = _get_classify_gate(current_user)
    gate.clear()
    try:
        with session.no_autoflush:
            for item in body.items:
                txn = session.get(Transaction, item.txn_id)
                if not txn or txn.user_id != current_user:
                    raise HTTPException(
                        status_code=404,
                        detail="We couldn't find that transaction — it may have been removed. Try refreshing?",
                    )
                if txn.pipeline_run_id != run_id:
                    raise HTTPException(
                        status_code=400,
                        detail="That transaction is not from this statement import.",
                    )
                if txn.source_type != "statement":
                    raise HTTPException(
                        status_code=400,
                        detail="Only statement-upload rows can be updated here.",
                    )
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
                txn.is_reviewed = True
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

    remaining = count_pipeline_run_classification_unknowns(
        session, user_id=current_user, run_id=run_id
    )

    resume_thresh = effective_onboarding_resume_threshold(session, current_user)

    return {
        "status": "ok",
        "updated": updated,
        "rules_upserted": rules,
        "contacts_created": contacts_created,
        "remaining_unknowns": remaining,
        "resume_threshold": resume_thresh,
        "should_resume": False,
        "auto_propagated": auto_propagated,
    }


# ───────────────────────────────────────────────────────────────────────────
# Background worker
# ───────────────────────────────────────────────────────────────────────────

def _run_pipeline_background(
    run_ids: list[int],
    source_keys: list[str],
    llm_model: str,
    user_id: str,
) -> None:
    """Execute the pipeline in a background thread.

    This runs outside the request lifecycle, so we create our own DB session.
    We update the pre-created PipelineRun rows with results or errors.
    """
    from pipeline import config
    from pipeline.llm_classifier import classify_llm
    from pipeline.parsers import PARSER_REGISTRY
    from pipeline.rules_classifier import classify_rules
    from pipeline.transformer import transform
    from pipeline.db_writer import write_to_db
    from api.services.user_classification import pipeline_config_for_account_owner

    if llm_model:
        config.LLM_MODEL = llm_model

    engine = get_engine()

    for run_id, source_key in zip(run_ids, source_keys):
        with SQLiteSerializingSession(engine) as session:
            run = session.get(PipelineRun, run_id)
            if not run:
                continue

            try:
                source_cfgs = config.get_source_configs(user_id, session)
                source_cfg = source_cfgs[source_key]
                parser_cls = PARSER_REGISTRY[source_key]
                parser = parser_cls()
                input_file = config.DATA_DIR / source_cfg["source_statement"]

                # Stage 1-4: Parse → Transform → Rules → LLM
                parsed = parser.parse(input_file)
                canonical = transform(
                    parsed,
                    account_id=source_cfg["account_id"],
                    currency=source_cfg.get("currency", "INR"),
                    source_statement=source_cfg["source_statement"],
                )
                ucfg = pipeline_config_for_account_owner(session, source_cfg["account_id"])
                classify_rules(canonical, ucfg)
                classify_llm(canonical)

                # Stage 5: write_to_db is the single canonical write path.
                # It handles hash dedup, email↔statement reconciliation, NULL backfill,
                # PipelineRun finalisation, and cache invalidation in one place.
                # We pass the pre-created run row so the ID returned to the API caller
                # stays valid for polling.
                write_to_db(
                    canonical,
                    source_key=source_key,
                    llm_model=config.LLM_MODEL,
                    session=session,
                    source_type="statement",
                    existing_run=run,
                )

            except Exception:
                # Full traceback also stored on the run row for UI polling; logger captures it for arth.log.
                logger.exception(
                    "Background statement import failed (run_id=%s source=%s)",
                    run_id,
                    source_key,
                )
                run.status = "failed"
                run.error_message = traceback.format_exc()
                run.completed_at = datetime.datetime.now(datetime.UTC)
                session.commit()


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _run_to_detail(run: PipelineRun, *, unknowns_count: int | None = None) -> PipelineRunDetail:
    return PipelineRunDetail(
        id=run.id,
        source_key=run.source_key,
        llm_model=run.llm_model,
        txn_count=run.txn_count,
        new_count=run.new_count,
        status=run.status,
        txn_date_min=run.txn_date_min.isoformat() if run.txn_date_min else None,
        txn_date_max=run.txn_date_max.isoformat() if run.txn_date_max else None,
        started_at=run.started_at.isoformat() if run.started_at else "",
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error_message=run.error_message,
        unknowns_count=unknowns_count,
    )


# ───────────────────────────────────────────────────────────────────────────
# POST /upload  — upload a statement file and auto-run the pipeline
# ───────────────────────────────────────────────────────────────────────────

class UploadOption(BaseModel):
    """One row for type picker or account picker UIs."""

    source_type: str | None = None
    source_key: str | None = None
    label: str


class UploadStatementResponse(BaseModel):
    """Structured outcome so the client can branch without guessing HTTP codes."""

    outcome: Literal[
        "success",
        "type_picker",
        "account_picker",
        "no_match",
        "no_source",
        "needs_password",
        "account_mismatch",
        "confirm_account",
        "holdings_success",
    ]
    message: str
    run_id: int | None = None
    source_key: str | None = None
    contact_prompt: bool = False
    password_invalid: bool = False
    type_options: list[UploadOption] | None = None
    account_options: list[UploadOption] | None = None
    # Account validation / confirmation (outcomes account_mismatch | confirm_account)
    detected_hint: str | None = None
    existing_hints: dict[str, str] | None = None
    pending_source_type: str | None = None
    needs_last4_input: bool = False
    # Portfolio ingest via unified upload (same shape as HoldingUploadResponse.import_stats)
    import_stats: dict[str, Any] | None = None


_DETECT_CONF = 0.72


def _portfolio_upload_response_from_unified_upload(
    *,
    active_file: Path,
    source_type: str,
    label: str,
    user_id: str,
    session: Session,
) -> UploadStatementResponse:
    """Run portfolio ingest synchronously for unified ``POST /upload``."""
    from pipeline.holding_upload_ingest import ingest_portfolio_file

    try:
        stats = ingest_portfolio_file(
            path=active_file,
            source_type=source_type,
            user_id=user_id,
            session=session,
        )
        session.commit()
        return UploadStatementResponse(
            outcome="holdings_success",
            message=f"Portfolio imported as {label}.",
            import_stats=stats,
        )
    except ValueError as ve:
        session.rollback()
        return UploadStatementResponse(
            outcome="no_match",
            message=str(ve),
            contact_prompt=True,
        )


def _ensure_new_upload_pipeline_source(session: Session, user_id: str, source_type: str, last4: str) -> str:
    """Create ``UserPipelineSource`` + registry entry for a user-confirmed new account (last four digits).

    Uses the base ``hdfc_savings`` / ``icici_savings`` key when that slot is still free so the
    first account matches historical Gmail sync behaviour; additional accounts use suffixed keys.
    """
    from sqlmodel import select

    from api.models import UserPipelineSource
    from parsers.uploads import (
        register_dynamic_hdfc_cc_key,
        register_dynamic_hdfc_savings_key,
        register_dynamic_icici_savings_key,
    )

    st = (source_type or "").strip().lower()
    uid = (user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=400, detail="Missing user context.")

    if st == "hdfc_savings":
        aid = f"HDFC_SAL_{last4}"
        base = session.exec(
            select(UserPipelineSource).where(
                UserPipelineSource.user_id == uid,
                UserPipelineSource.source_key == "hdfc_savings",
            )
        ).first()
        sk = "hdfc_savings" if base is None else register_dynamic_hdfc_savings_key(last4)
    elif st == "hdfc_cc":
        aid = f"HDFC_CC_{last4}"
        sk = register_dynamic_hdfc_cc_key(last4)
    elif st == "icici_savings":
        aid = f"ICICI_SAV_{last4}"
        base = session.exec(
            select(UserPipelineSource).where(
                UserPipelineSource.user_id == uid,
                UserPipelineSource.source_key == "icici_savings",
            )
        ).first()
        sk = "icici_savings" if base is None else register_dynamic_icici_savings_key(last4)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"We can't add a new account link for this statement type ({source_type!r}) from here yet.",
        )

    row_by_acct = session.exec(
        select(UserPipelineSource).where(
            UserPipelineSource.user_id == uid,
            UserPipelineSource.account_id == aid,
        )
    ).first()
    if row_by_acct is not None:
        return str(row_by_acct.source_key)

    row_by_sk = session.exec(
        select(UserPipelineSource).where(
            UserPipelineSource.user_id == uid,
            UserPipelineSource.source_key == sk,
        )
    ).first()
    if row_by_sk is not None:
        return str(row_by_sk.source_key)

    session.add(
        UserPipelineSource(
            user_id=uid,
            source_key=sk,
            account_id=aid,
            currency="INR",
            statement_folder=None,
        )
    )
    return sk


def _dedupe_detection_by_type(results: list[Any]) -> list[Any]:
    """Keep the strongest confidence per *source_type*."""
    best: dict[str, Any] = {}
    for r in results:
        cur = best.get(r.source_type)
        if cur is None or r.confidence > cur.confidence:
            best[r.source_type] = r
    return list(best.values())


@router.post("/upload", response_model=UploadStatementResponse)
async def upload_statement(
    file: UploadFile = File(...),
    source_key: str | None = Query(None, description="Force a specific pipeline source_key after disambiguation"),
    source_type: str | None = Query(
        None,
        description="After type picker: logical parser id (e.g. hdfc_savings_pdf)",
    ),
    llm_model: str = Query("auto"),
    pdf_password: str | None = Query(
        None,
        description="When the PDF is password-protected: user password (after onboarding/env candidates fail)",
    ),
    mismatch_action: str | None = Query(
        None,
        description="After account_mismatch or confirm_account: 'new_account' (with new_account_last4) or use explicit source_key for 'same'",
    ),
    new_account_last4: str | None = Query(
        None,
        description="Four digits for a new savings/CC account when mismatch_action=new_account",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> UploadStatementResponse:
    """Upload a bank or portfolio statement; sniff file **content** to pick ingest path.

    Flow:
      1. No ``source_key`` → transaction detectors → ``success`` | ``type_picker`` |
         ``account_picker`` | ``no_match`` | ``no_source``.
      2. When transaction detectors find nothing, portfolio detectors run → ``holdings_success`` |
         ``type_picker`` (portfolio types) | ``no_match``.
      3. ``source_type`` that is a portfolio format → ``ingest_portfolio_file`` → ``holdings_success``.
      4. User disambiguates → re-upload with ``source_type``, then ``source_key`` if needed (bank).
    """
    from pipeline import config
    from pipeline.detection import (
        ResolveAccountMismatch,
        ResolveConfirmAccount,
        account_option_label,
        detect_holding_file,
        detect_transaction_file,
        resolve_upload_statement_destination,
    )
    from pipeline.parsers import PARSER_REGISTRY

    from pipeline.config import DATA_DIR
    from pipeline.pdf_upload_unlock import (
        NeedsPdfPassword,
        WrongPdfPassword,
        prepare_upload_pdf_path,
    )
    from scraper.source_builder import (
        sync_user_pipeline_sources_from_scraper_mappings,
        sync_user_pipeline_sources_from_transactions,
    )

    filename = file.filename or "upload.txt"
    # Gmail onboarding only created ``ScraperAccountMapping`` rows; uploads resolve
    # accounts via ``UserPipelineSource``. Bridge any gap before we read valid keys.
    n_map = sync_user_pipeline_sources_from_scraper_mappings(session, current_user)
    n_txn = sync_user_pipeline_sources_from_transactions(session, current_user)
    if n_map or n_txn:
        session.commit()
    user_sources = config.get_source_configs(current_user, session)
    valid_keys = sorted(user_sources.keys())

    uploads_dir = DATA_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix or ".txt"
    with tempfile.NamedTemporaryFile(dir=uploads_dir, suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    tmp_kept = False
    active_file = tmp_path
    try:
        if tmp_path.suffix.lower() == ".pdf":
            try:
                active_file, _ = prepare_upload_pdf_path(
                    tmp_path,
                    session=session,
                    user_id=current_user,
                    pdf_password=pdf_password,
                )
            except NeedsPdfPassword:
                return UploadStatementResponse(
                    outcome="needs_password",
                    message=(
                        "This PDF is password-protected. We couldn't unlock it with your saved "
                        "statement settings — enter the password from the bank (often name + date "
                        "of birth as printed on the statement cover)."
                    ),
                    contact_prompt=False,
                    password_invalid=False,
                )
            except WrongPdfPassword:
                return UploadStatementResponse(
                    outcome="needs_password",
                    message="That password didn't unlock the PDF. Try again.",
                    contact_prompt=False,
                    password_invalid=True,
                )

        # ── User confirmed a brand-new account (re-upload after mismatch / confirm) ──
        ma = (mismatch_action or "").strip().lower()
        if ma == "new_account":
            tail = (new_account_last4 or "").strip()
            if len(tail) != 4 or not tail.isdigit():
                raise HTTPException(
                    status_code=400,
                    detail="Enter exactly four digits for the new account.",
                )
            st_req = (source_type or "").strip()
            if not st_req:
                raise HTTPException(
                    status_code=400,
                    detail="Re-send the statement type (source_type) when you add a new account.",
                )
            sk_new = _ensure_new_upload_pipeline_source(session, current_user, st_req, tail)
            session.commit()
            user_sources = config.get_source_configs(current_user, session)
            if sk_new not in user_sources:
                raise HTTPException(
                    status_code=500,
                    detail="We couldn't save that account link. Try again in a moment?",
                )
            run = PipelineRun(source_key=sk_new, llm_model=llm_model, status="running")
            session.add(run)
            session.flush()
            run_id = run.id
            session.commit()
            threading.Thread(
                target=_run_upload_background,
                args=(run_id, sk_new, active_file, llm_model, current_user),
                daemon=True,
            ).start()
            tmp_kept = True
            logger.info("Upload (new account): %s → %s (%s)", filename, active_file.name, sk_new)
            return UploadStatementResponse(
                outcome="success",
                message=(
                    f"Import started for your new account. "
                    f"You can watch progress under Runs in the app (run #{run_id})."
                ),
                run_id=run_id,
                source_key=sk_new,
            )

        # ── Explicit source_key (user picked account or retry) ─────────────────
        if source_key:
            sk = source_key.strip()
            if sk not in user_sources:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"That bank source isn't set up for your account. "
                        f"Configured: {valid_keys}"
                    ),
                )
            run = PipelineRun(source_key=sk, llm_model=llm_model, status="running")
            session.add(run)
            session.flush()
            run_id = run.id
            session.commit()
            threading.Thread(
                target=_run_upload_background,
                args=(run_id, sk, active_file, llm_model, current_user),
                daemon=True,
            ).start()
            tmp_kept = True
            logger.info("Upload (explicit key): %s → %s (%s)", filename, active_file.name, sk)
            return UploadStatementResponse(
                outcome="success",
                message=(
                    f"Import started for your linked account. "
                    f"You can watch progress under Runs in the app (run #{run_id})."
                ),
                run_id=run_id,
                source_key=sk,
            )

        # ── Explicit portfolio source_type (after unified type picker) ─────────
        st_portfolio = (source_type or "").strip()
        if st_portfolio:
            from pipeline.holding_upload_ingest import VALID_PORTFOLIO_UPLOAD_SOURCE_TYPES

            if st_portfolio in VALID_PORTFOLIO_UPLOAD_SOURCE_TYPES:
                from pipeline.detection import PARSER_LABELS

                plabel = PARSER_LABELS.get(st_portfolio, st_portfolio)
                return _portfolio_upload_response_from_unified_upload(
                    active_file=active_file,
                    source_type=st_portfolio,
                    label=plabel,
                    user_id=current_user,
                    session=session,
                )

        raw_results = detect_transaction_file(active_file)
        strong = [r for r in raw_results if r.confidence >= _DETECT_CONF]
        if not strong and raw_results:
            strong = [max(raw_results, key=lambda r: r.confidence)]

        if source_type:
            st = source_type.strip()
            strong = [r for r in strong if r.source_type == st]
            if not strong:
                return UploadStatementResponse(
                    outcome="no_match",
                    message=(
                        "This file does not look like the statement type you selected. "
                        "Try another option or send us the file so we can add support."
                    ),
                    contact_prompt=True,
                )

        deduped = _dedupe_detection_by_type(strong)
        if len(deduped) > 1:
            return UploadStatementResponse(
                outcome="type_picker",
                message="We detected more than one possible statement format. Which one is this file?",
                type_options=[
                    UploadOption(source_type=r.source_type, label=r.label) for r in deduped
                ],
            )

        if len(deduped) == 0:
            raw_h = detect_holding_file(active_file)
            strong_h = [r for r in raw_h if r.confidence >= _DETECT_CONF]
            if not strong_h and raw_h:
                strong_h = [max(raw_h, key=lambda r: r.confidence)]
            deduped_h = _dedupe_detection_by_type(strong_h)

            if len(deduped_h) == 1:
                chosen_h = deduped_h[0]
                return _portfolio_upload_response_from_unified_upload(
                    active_file=active_file,
                    source_type=chosen_h.source_type,
                    label=chosen_h.label,
                    user_id=current_user,
                    session=session,
                )

            if len(deduped_h) > 1:
                return UploadStatementResponse(
                    outcome="type_picker",
                    message=(
                        "We detected more than one possible format for this file. "
                        "Which one is it?"
                    ),
                    type_options=[
                        UploadOption(source_type=r.source_type, label=r.label) for r in deduped_h
                    ],
                )

            return UploadStatementResponse(
                outcome="no_match",
                message=(
                    "We couldn't recognise this statement format. It may be a type we have not "
                    "seen before — please reach out to us and we'll help. Your data was not changed."
                ),
                contact_prompt=True,
            )

        chosen = deduped[0]
        resolved = resolve_upload_statement_destination(
            source_type=chosen.source_type,
            account_hint=chosen.account_hint,
            user_source_keys=valid_keys,
            user_source_configs=user_sources,
            parser_registry=PARSER_REGISTRY,
            skip_account_validation=False,
        )

        if resolved is None:
            return UploadStatementResponse(
                outcome="no_source",
                message=(
                    f"This looks like {chosen.label}, but that bank isn’t connected here yet. "
                    "Open Settings, add the account, then upload again."
                ),
            )

        if isinstance(resolved, ResolveAccountMismatch):
            hints = resolved.hints_on_file
            on_file = " and ".join(sorted(hints.values())) or "your linked account"
            same_account_opts = [
                UploadOption(
                    source_key=sk,
                    label=f"No — same account, use the one ending {hints[sk]}",
                )
                for sk in resolved.candidate_keys
                if sk in hints
            ]
            return UploadStatementResponse(
                outcome="account_mismatch",
                message=(
                    f"We read account ending {resolved.detected_hint} on this file, but the "
                    f"account we already have on file ends in {on_file}. "
                    "Is this a new account, the same one, or should we cancel?"
                ),
                detected_hint=resolved.detected_hint,
                existing_hints=dict(hints),
                pending_source_type=chosen.source_type,
                needs_last4_input=False,
                account_options=same_account_opts or None,
            )

        if isinstance(resolved, ResolveConfirmAccount):
            hints = resolved.hints_on_file
            tails = sorted({v for v in hints.values()})
            tail_txt = ", ".join(tails) if tails else "your linked account"
            return UploadStatementResponse(
                outcome="confirm_account",
                message=(
                    "We couldn't read the account number from this statement. "
                    f"You already have an account ending in {tail_txt} on file. "
                    "Continue with that account, tell us this is a new account, or cancel?"
                ),
                existing_hints=dict(hints),
                pending_source_type=chosen.source_type,
                needs_last4_input=True,
                account_options=[
                    UploadOption(source_key=sk, label=f"Continue with account ending …{hints[sk]}")
                    for sk in resolved.candidate_keys
                    if sk in hints
                ]
                if len(resolved.candidate_keys) > 1
                else None,
            )

        if isinstance(resolved, list):
            return UploadStatementResponse(
                outcome="account_picker",
                message="We matched the file format. Which account should we import into?",
                account_options=[
                    UploadOption(source_key=rk, label=account_option_label(rk)) for rk in resolved
                ],
            )

        run = PipelineRun(source_key=resolved, llm_model=llm_model, status="running")
        session.add(run)
        session.flush()
        run_id = run.id
        session.commit()
        threading.Thread(
            target=_run_upload_background,
            args=(run_id, resolved, active_file, llm_model, current_user),
            daemon=True,
        ).start()
        tmp_kept = True
        logger.info("Upload (auto): %s → %s (%s)", filename, active_file.name, resolved)
        return UploadStatementResponse(
            outcome="success",
            message=(
                f"Import started for your linked account. "
                f"You can watch progress under Runs in the app (run #{run_id})."
            ),
            run_id=run_id,
            source_key=resolved,
        )
    finally:
        if not tmp_kept:
            active_file.unlink(missing_ok=True)


# ───────────────────────────────────────────────────────────────────────────
# POST /upload/holdings  — portfolio PDF/CSV when Gmail had no holdings
# ───────────────────────────────────────────────────────────────────────────


class HoldingUploadResponse(BaseModel):
    outcome: Literal["success", "type_picker", "no_match", "needs_password"]
    message: str
    contact_prompt: bool = False
    password_invalid: bool = False
    import_stats: dict[str, Any] | None = None
    type_options: list[UploadOption] | None = None


@router.post("/upload/holdings", response_model=HoldingUploadResponse)
async def upload_holdings_statement(
    file: UploadFile = File(...),
    source_type: str | None = Query(
        None,
        description="After type picker: logical holding parser id (e.g. icici_direct_mf_statement_pdf)",
    ),
    pdf_password: str | None = Query(
        None,
        description="Password for encrypted portfolio PDFs (after saved/env candidates)",
    ),
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> HoldingUploadResponse:
    """Upload a portfolio CSV/PDF; content sniff routes to the correct holding ingest."""
    from pipeline.config import DATA_DIR
    from pipeline.detection import detect_holding_file
    from pipeline.holding_upload_ingest import ingest_portfolio_file
    from pipeline.pdf_upload_unlock import (
        NeedsPdfPassword,
        WrongPdfPassword,
        prepare_upload_pdf_path,
    )

    filename = file.filename or "upload.pdf"
    uploads_dir = DATA_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(dir=uploads_dir, suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    active_file = tmp_path
    try:
        if tmp_path.suffix.lower() == ".pdf":
            try:
                active_file, _ = prepare_upload_pdf_path(
                    tmp_path,
                    session=session,
                    user_id=current_user,
                    pdf_password=pdf_password,
                )
            except NeedsPdfPassword:
                return HoldingUploadResponse(
                    outcome="needs_password",
                    message=(
                        "This PDF is password-protected. Enter the password from your broker/bank "
                        "(we already tried your saved statement secrets)."
                    ),
                    password_invalid=False,
                )
            except WrongPdfPassword:
                return HoldingUploadResponse(
                    outcome="needs_password",
                    message="That password didn't unlock the PDF. Try again.",
                    password_invalid=True,
                )

        raw = detect_holding_file(active_file)
        strong = [r for r in raw if r.confidence >= _DETECT_CONF]
        if not strong and raw:
            strong = [max(raw, key=lambda r: r.confidence)]

        if source_type:
            st = source_type.strip()
            strong = [r for r in strong if r.source_type == st]
            if not strong:
                return HoldingUploadResponse(
                    outcome="no_match",
                    message=(
                        "This file doesn't match the portfolio statement type you picked. "
                        "Try another or contact us with a sample."
                    ),
                    contact_prompt=True,
                )

        deduped = _dedupe_detection_by_type(strong)
        if len(deduped) > 1:
            return HoldingUploadResponse(
                outcome="type_picker",
                message="We found multiple possible portfolio formats. Which one is this file?",
                type_options=[
                    UploadOption(source_type=r.source_type, label=r.label) for r in deduped
                ],
            )

        if len(deduped) == 0:
            return HoldingUploadResponse(
                outcome="no_match",
                message=(
                    "We couldn't recognise this portfolio file. Please reach out — "
                    "we can add support. Nothing was imported."
                ),
                contact_prompt=True,
            )

        chosen = deduped[0].source_type
        chosen_label = deduped[0].label
        stats = ingest_portfolio_file(
            path=active_file,
            source_type=chosen,
            user_id=current_user,
            session=session,
        )
        session.commit()
        return HoldingUploadResponse(
            outcome="success",
            message=f"Done — saved as {chosen_label}.",
            import_stats=stats,
        )
    except ValueError as ve:
        session.rollback()
        return HoldingUploadResponse(
            outcome="no_match",
            message=str(ve),
            contact_prompt=True,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        active_file.unlink(missing_ok=True)


def _run_upload_background(
    run_id: int,
    source_key: str,
    input_file: Path,
    llm_model: str,
    user_id: str,
) -> None:
    """Process an uploaded statement file in a background thread.

    Mirrors _run_pipeline_background() but uses the uploaded file path
    instead of the default source file from config.  Cleans up the temp
    file after processing (success or failure).

    Uses :class:`~api.database.SQLiteSerializingSession` so DB commits line up with
    the API's request-scoped sessions — plain :class:`sqlmodel.Session` bypasses the
    process-wide writer lock and can cause ``database is locked`` under SQLite.
    """
    from pipeline import config
    from pipeline.db_writer import compute_content_hash, write_to_db
    from pipeline.llm_classifier import classify_llm
    from pipeline.models import CanonicalTransaction
    from pipeline.parsers import PARSER_REGISTRY
    from pipeline.rules_classifier import classify_rules
    from pipeline.transformer import transform

    from api.models import Transaction
    from api.services.account_user_map import user_id_for_account
    from api.services.user_classification import pipeline_config_for_account_owner
    from scraper.onboarding_orchestrator import count_pipeline_run_classification_unknowns
    from sqlmodel import select as _select

    if llm_model:
        config.LLM_MODEL = llm_model

    engine = get_engine()

    _upload_progress_set(
        run_id,
        {
            "user_id": user_id,
            "phase": "parsing",
            "parsed_count": 0,
            "total_count": 0,
        },
    )

    with SQLiteSerializingSession(engine) as session:
        run = session.get(PipelineRun, run_id)
        if not run:
            input_file.unlink(missing_ok=True)
            _upload_progress_clear(run_id)
            return

        try:
            source_cfgs = config.get_source_configs(user_id, session)
            source_cfg = source_cfgs[source_key]
            # Same label as email/CLI imports — not the upload tempfile basename (tmpx….csv).
            statement_src = source_cfg["source_statement"]
            parser_cls = PARSER_REGISTRY[source_key]
            parser = parser_cls()

            parsed = parser.parse(input_file)
            _upload_progress_set(
                run_id,
                {
                    "phase": "parsing",
                    "parsed_count": len(parsed),
                    "total_count": len(parsed),
                },
            )

            canonical = transform(
                parsed,
                account_id=source_cfg["account_id"],
                currency=source_cfg.get("currency", "INR"),
                source_statement=statement_src,
            )
            ucfg = pipeline_config_for_account_owner(session, source_cfg["account_id"])
            classify_rules(canonical, ucfg)

            # Pre-filter to only hash-new rows before LLM classification.
            # This avoids spending LLM calls on rows that are exact content-hash
            # duplicates already in the DB.  write_to_db will still see all rows
            # and use its own Path A (hash) + Path B (email reconciliation) logic.
            unique_new = 0
            new_canonical: list[CanonicalTransaction] = []
            for txn in canonical:
                content_hash = compute_content_hash(txn)
                row_uid = user_id_for_account(txn.account_id)
                existing = session.exec(
                    _select(Transaction).where(
                        Transaction.content_hash == content_hash,
                        Transaction.account_id == txn.account_id,
                        Transaction.user_id == row_uid,
                    )
                ).first()
                if existing is None:
                    unique_new += 1
                    new_canonical.append(txn)

            _upload_progress_set(
                run_id,
                {
                    "phase": "deduping",
                    "total_count": len(canonical),
                    "unique_count": unique_new,
                },
            )

            llm_state: dict[str, int] = {"done": 0, "total": 0}
            classify_total = len(new_canonical)

            def on_llm_batch(done: int, total: int) -> None:
                llm_state["done"], llm_state["total"] = done, total
                if total > 0:
                    _upload_progress_set(
                        run_id,
                        {
                            "phase": "classifying",
                            "classified_count": done,
                            "total_classify": total,
                            "total_count": classify_total,
                        },
                    )

            if new_canonical:
                classify_llm(new_canonical, on_batch_complete=on_llm_batch)
            if llm_state["total"] == 0:
                _upload_progress_set(
                    run_id,
                    {
                        "phase": "classifying",
                        "classified_count": classify_total,
                        "total_classify": classify_total,
                        "total_count": len(canonical),
                    },
                )

            # Stage 5: write_to_db is the single canonical write path.
            # Passing all of canonical (not just new_canonical) lets write_to_db run
            # Path A (hash dedup + NULL backfill) and Path B (email↔statement
            # reconciliation) on every row — hash-dup rows get backfilled, email rows
            # get merged rather than duplicated. The pre-filter above only controlled
            # which rows were sent to the LLM to avoid unnecessary API spend.
            run_row = session.get(PipelineRun, run_id)
            completed_run = write_to_db(
                canonical,
                source_key=source_key,
                llm_model=config.LLM_MODEL,
                session=session,
                source_type="statement",
                existing_run=run_row,
            )

            unknowns = count_pipeline_run_classification_unknowns(
                session, user_id=user_id, run_id=run_id
            )
            total_classify_final = llm_state["total"] or classify_total
            classified_final = (
                llm_state["done"] if llm_state["total"] else classify_total
            )
            _upload_progress_set(
                run_id,
                {
                    "phase": "complete",
                    "classified_count": classified_final,
                    "total_classify": total_classify_final,
                    "total_count": len(canonical),
                    "unique_count": unique_new,
                    "unknowns_count": unknowns,
                    "new_count": completed_run.new_count,
                    "txn_count": completed_run.txn_count,
                },
            )

        except Exception:
            logger.exception(
                "Background upload import failed (run_id=%s source=%s)",
                run_id,
                source_key,
            )
            run.status = "failed"
            run.error_message = traceback.format_exc()
            run.completed_at = datetime.datetime.now(datetime.UTC)
            session.commit()
            _upload_progress_set(
                run_id,
                {
                    "phase": "error",
                    "error_message": run.error_message or "Import failed",
                },
            )
        finally:
            # Always clean up the temp file
            input_file.unlink(missing_ok=True)
            logger.info("Upload temp file cleaned up: %s", input_file.name)
            # Keep progress briefly for SSE consumers; clear after delay in a daemon thread.
            def _delayed_clear() -> None:
                import time

                time.sleep(120)
                _upload_progress_clear(run_id)

            threading.Thread(target=_delayed_clear, daemon=True).start()
