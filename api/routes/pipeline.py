"""
Pipeline trigger, status, and statement upload endpoints.

POST /api/pipeline/run       — kick off a pipeline run in a background thread
GET  /api/pipeline/runs      — list past runs (paginated)
GET  /api/pipeline/runs/{id} — single run detail (for polling status)
POST /api/pipeline/upload    — upload a statement file and auto-run the pipeline
"""

from __future__ import annotations

import datetime
import logging
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import get_engine, get_session
from api.models import PipelineRun

logger = logging.getLogger(__name__)

router = APIRouter()


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
    return [_run_to_detail(r) for r in runs]


# ───────────────────────────────────────────────────────────────────────────
# GET /runs/{id}  — single run detail
# ───────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}", response_model=PipelineRunDetail)
def get_pipeline_run(run_id: int, *, session: Session = Depends(get_session)):
    """Get details of a single pipeline run (useful for polling status)."""
    run = session.get(PipelineRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Couldn't find that import run.")
    return _run_to_detail(run)


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
    from pipeline.db_writer import compute_content_hash

    from api.models import Transaction
    from api.services.account_user_map import user_id_for_account
    from api.services.user_classification import pipeline_config_for_account_owner

    if llm_model:
        config.LLM_MODEL = llm_model

    engine = get_engine()

    for run_id, source_key in zip(run_ids, source_keys):
        with Session(engine) as session:
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

                # Stage 5: Write to DB with dedup
                new_count = 0
                date_min = None
                date_max = None

                for txn in canonical:
                    content_hash = compute_content_hash(txn)
                    row_uid = user_id_for_account(txn.account_id)
                    existing = session.exec(
                        select(Transaction).where(
                            Transaction.content_hash == content_hash,
                            Transaction.account_id == txn.account_id,
                            Transaction.user_id == row_uid,
                        )
                    ).first()
                    if existing is not None:
                        continue

                    db_txn = Transaction(
                        content_hash=content_hash,
                        txn_date=txn.txn_date,
                        account_id=txn.account_id,
                        user_id=row_uid,
                        source_statement=txn.source_statement,
                        direction=txn.direction.value,
                        amount=float(txn.amount),
                        currency=txn.currency,
                        txn_type=txn.txn_type.value if txn.txn_type else None,
                        channel=txn.channel.value if txn.channel else None,
                        upi_type=txn.upi_type.value if txn.upi_type else None,
                        counterparty=txn.counterparty,
                        counterparty_category=(
                            txn.counterparty_category.value if txn.counterparty_category else None
                        ),
                        spend_category=(
                            txn.spend_category.value if txn.spend_category else None
                        ),
                        classification_source=(
                            txn.classification_source.value
                            if txn.classification_source
                            else None
                        ),
                        raw_description=txn.raw_description,
                        ref_number=txn.ref_number,
                        closing_balance=float(txn.closing_balance) if txn.closing_balance else None,
                        value_date=txn.value_date,
                        notes=txn.notes,
                        is_reviewed=True,
                        pipeline_run_id=run.id,
                    )
                    session.add(db_txn)
                    new_count += 1

                    if date_min is None or txn.txn_date < date_min:
                        date_min = txn.txn_date
                    if date_max is None or txn.txn_date > date_max:
                        date_max = txn.txn_date

                run.txn_count = len(canonical)
                run.new_count = new_count
                run.txn_date_min = date_min
                run.txn_date_max = date_max
                run.status = "completed"
                run.completed_at = datetime.datetime.now(datetime.UTC)
                session.commit()

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

def _run_to_detail(run: PipelineRun) -> PipelineRunDetail:
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
    ]
    message: str
    run_id: int | None = None
    source_key: str | None = None
    contact_prompt: bool = False
    password_invalid: bool = False
    type_options: list[UploadOption] | None = None
    account_options: list[UploadOption] | None = None


_DETECT_CONF = 0.72


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
    *,
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
) -> UploadStatementResponse:
    """Upload a bank statement; sniff file **content** to pick the parser + account.

    Flow:
      1. No ``source_key`` → run detectors → ``success`` | ``type_picker`` |
         ``account_picker`` | ``no_match`` | ``no_source``.
      2. User disambiguates → re-upload with ``source_type``, then ``source_key`` if needed.
    """
    from pipeline import config
    from pipeline.detection import (
        account_option_label,
        detect_transaction_file,
        resolve_transaction_source_key,
    )
    from pipeline.parsers import PARSER_REGISTRY

    from pipeline.config import DATA_DIR
    from pipeline.pdf_upload_unlock import (
        NeedsPdfPassword,
        WrongPdfPassword,
        prepare_upload_pdf_path,
    )

    filename = file.filename or "upload.txt"
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
                message=f"Import started for {sk}. Poll GET /api/pipeline/runs/{run_id} for status.",
                run_id=run_id,
                source_key=sk,
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
            return UploadStatementResponse(
                outcome="no_match",
                message=(
                    "We couldn't recognise this statement format. It may be a type we have not "
                    "seen before — please reach out to us and we'll help. Your data was not changed."
                ),
                contact_prompt=True,
            )

        chosen = deduped[0]
        resolved = resolve_transaction_source_key(
            source_type=chosen.source_type,
            account_hint=chosen.account_hint,
            user_source_keys=valid_keys,
            parser_registry=PARSER_REGISTRY,
        )

        if resolved is None:
            return UploadStatementResponse(
                outcome="no_source",
                message=(
                    f"This looks like {chosen.label}, but you have no matching bank account "
                    "connected yet. Add the account under pipeline sources, then try again."
                ),
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
                f"Import started for {resolved}. Poll GET /api/pipeline/runs/{run_id} for status."
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
        stats = ingest_portfolio_file(
            path=active_file,
            source_type=chosen,
            user_id=current_user,
            session=session,
        )
        session.commit()
        return HoldingUploadResponse(
            outcome="success",
            message=f"Imported using {chosen}.",
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
    """
    from pipeline import config
    from pipeline.llm_classifier import classify_llm
    from pipeline.parsers import PARSER_REGISTRY
    from pipeline.rules_classifier import classify_rules
    from pipeline.transformer import transform
    from pipeline.db_writer import compute_content_hash

    from api.models import Transaction
    from api.services.account_user_map import user_id_for_account
    from api.services.user_classification import pipeline_config_for_account_owner
    from sqlmodel import select as _select

    if llm_model:
        config.LLM_MODEL = llm_model

    engine = get_engine()

    with Session(engine) as session:
        run = session.get(PipelineRun, run_id)
        if not run:
            input_file.unlink(missing_ok=True)
            return

        try:
            source_cfgs = config.get_source_configs(user_id, session)
            source_cfg = source_cfgs[source_key]
            # Same label as email/CLI imports — not the upload tempfile basename (tmpx….csv).
            statement_src = source_cfg["source_statement"]
            parser_cls = PARSER_REGISTRY[source_key]
            parser = parser_cls()

            parsed = parser.parse(input_file)
            canonical = transform(
                parsed,
                account_id=source_cfg["account_id"],
                currency=source_cfg.get("currency", "INR"),
                source_statement=statement_src,
            )
            ucfg = pipeline_config_for_account_owner(session, source_cfg["account_id"])
            classify_rules(canonical, ucfg)
            classify_llm(canonical)

            new_count = 0
            date_min = None
            date_max = None

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
                if existing is not None:
                    continue

                db_txn = Transaction(
                    content_hash=content_hash,
                    txn_date=txn.txn_date,
                    account_id=txn.account_id,
                    user_id=row_uid,
                    source_statement=statement_src,
                    direction=txn.direction.value,
                    amount=float(txn.amount),
                    currency=txn.currency,
                    txn_type=txn.txn_type.value if txn.txn_type else None,
                    channel=txn.channel.value if txn.channel else None,
                    upi_type=txn.upi_type.value if txn.upi_type else None,
                    counterparty=txn.counterparty,
                    counterparty_category=(
                        txn.counterparty_category.value if txn.counterparty_category else None
                    ),
                    spend_category=(
                        txn.spend_category.value if txn.spend_category else None
                    ),
                    classification_source=(
                        txn.classification_source.value
                        if txn.classification_source
                        else None
                    ),
                    raw_description=txn.raw_description,
                    ref_number=txn.ref_number,
                    closing_balance=float(txn.closing_balance) if txn.closing_balance else None,
                    value_date=txn.value_date,
                    notes=txn.notes,
                    is_reviewed=True,
                    pipeline_run_id=run_id,
                )
                session.add(db_txn)
                new_count += 1

                if date_min is None or txn.txn_date < date_min:
                    date_min = txn.txn_date
                if date_max is None or txn.txn_date > date_max:
                    date_max = txn.txn_date

            run.txn_count = len(canonical)
            run.new_count = new_count
            run.txn_date_min = date_min
            run.txn_date_max = date_max
            run.status = "completed"
            run.completed_at = datetime.datetime.now(datetime.UTC)
            session.commit()

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
        finally:
            # Always clean up the temp file
            input_file.unlink(missing_ok=True)
            logger.info("Upload temp file cleaned up: %s", input_file.name)
