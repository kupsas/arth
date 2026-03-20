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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, col, select

from api.database import get_engine, get_session
from api.models import PipelineRun

logger = logging.getLogger(__name__)

router = APIRouter()

# Valid source keys that the API accepts (+ "all" to run everything)
_VALID_SOURCES = {"hdfc_savings", "hdfc_cc_1905", "hdfc_cc_5778", "icici_savings", "all"}


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
):
    """Start a pipeline run in a background thread.

    Returns immediately with the run ID(s) so the client can poll for status.
    """
    if body.source_key not in _VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source_key: {body.source_key!r}. "
                   f"Valid options: {sorted(_VALID_SOURCES)}",
        )

    from pipeline.config import SOURCE_CONFIGS

    # Determine which sources to run
    if body.source_key == "all":
        source_keys = list(SOURCE_CONFIGS.keys())
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
        args=(run_ids, source_keys, body.llm_model),
        daemon=True,
    )
    thread.start()

    return PipelineRunResponse(
        run_ids=run_ids,
        message=f"Pipeline started for {body.source_key} ({len(source_keys)} source(s)). "
                f"Poll GET /api/pipeline/runs/{{id}} for status.",
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
        raise HTTPException(status_code=404, detail=f"Pipeline run {run_id} not found")
    return _run_to_detail(run)


# ───────────────────────────────────────────────────────────────────────────
# Background worker
# ───────────────────────────────────────────────────────────────────────────

def _run_pipeline_background(
    run_ids: list[int],
    source_keys: list[str],
    llm_model: str,
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

    if llm_model:
        config.LLM_MODEL = llm_model

    engine = get_engine()

    for run_id, source_key in zip(run_ids, source_keys):
        with Session(engine) as session:
            run = session.get(PipelineRun, run_id)
            if not run:
                continue

            try:
                source_cfg = config.SOURCE_CONFIGS[source_key]
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
                classify_rules(canonical)
                classify_llm(canonical)

                # Stage 5: Write to DB with dedup
                new_count = 0
                date_min = None
                date_max = None

                for txn in canonical:
                    content_hash = compute_content_hash(txn)
                    existing = session.exec(
                        select(Transaction).where(Transaction.content_hash == content_hash)
                    ).first()
                    if existing is not None:
                        continue

                    db_txn = Transaction(
                        content_hash=content_hash,
                        txn_date=txn.txn_date,
                        account_id=txn.account_id,
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

class UploadResponse(BaseModel):
    """Returned immediately when an upload is accepted."""
    run_id: int
    source_key: str
    message: str


@router.post("/upload", response_model=UploadResponse)
async def upload_statement(
    file: UploadFile = File(...),
    source_key: str | None = Query(None, description="Force a specific parser key"),
    llm_model: str = Query("auto"),
    *,
    session: Session = Depends(get_session),
) -> UploadResponse:
    """Upload a bank statement file and automatically run the pipeline on it.

    Auto-detection logic (when source_key is not provided):
      - Filename contains "1905"       → hdfc_cc_1905
      - Filename contains "5778"       → hdfc_cc_5778
      - Filename contains "HDFC" + no CC pattern → hdfc_savings
      - Filename contains "ICICI"      → icici_savings
      - Extension is .txt              → hdfc_savings (most common txt format)
    Pass source_key explicitly to override auto-detection.

    The file is saved temporarily to data/uploads/, the pipeline runs in a
    background thread, and the temporary file is cleaned up after processing.
    """
    filename = file.filename or "upload.txt"

    detected_source = source_key or _detect_source_key(filename)
    if detected_source not in _VALID_SOURCES or detected_source == "all":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not auto-detect source from filename {filename!r}. "
                f"Please specify source_key. Valid options: "
                f"{sorted(s for s in _VALID_SOURCES if s != 'all')}"
            ),
        )

    # Save the uploaded file to a temp location
    from pipeline.config import DATA_DIR
    uploads_dir = DATA_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix or ".txt"
    with tempfile.NamedTemporaryFile(
        dir=uploads_dir, suffix=suffix, delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        shutil.copyfileobj(file.file, tmp)

    logger.info("Upload received: %s → %s (source=%s)", filename, tmp_path.name, detected_source)

    # Create a PipelineRun placeholder so we can return the ID immediately
    run = PipelineRun(
        source_key=detected_source,
        llm_model=llm_model,
        status="running",
    )
    session.add(run)
    session.flush()
    run_id = run.id
    session.commit()

    # Kick off background processing
    thread = threading.Thread(
        target=_run_upload_background,
        args=(run_id, detected_source, tmp_path, llm_model),
        daemon=True,
    )
    thread.start()

    return UploadResponse(
        run_id=run_id,
        source_key=detected_source,
        message=(
            f"Upload accepted for {detected_source}. "
            f"Poll GET /api/pipeline/runs/{run_id} for status."
        ),
    )


def _detect_source_key(filename: str) -> str:
    """Infer the parser source_key from the uploaded filename.

    Returns "unknown" if no pattern matches so the caller can raise a 400.
    """
    name_upper = filename.upper()
    if "1905" in name_upper:
        return "hdfc_cc_1905"
    if "5778" in name_upper:
        return "hdfc_cc_5778"
    # Check for HDFC (not credit card) — savings account statement
    if "HDFC" in name_upper:
        return "hdfc_savings"
    if "ICICI" in name_upper:
        return "icici_savings"
    # .txt is almost always HDFC savings in this setup
    if filename.lower().endswith(".txt"):
        return "hdfc_savings"
    return "unknown"


def _run_upload_background(
    run_id: int,
    source_key: str,
    input_file: Path,
    llm_model: str,
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
            source_cfg = config.SOURCE_CONFIGS[source_key]
            parser_cls = PARSER_REGISTRY[source_key]
            parser = parser_cls()

            parsed = parser.parse(input_file)
            canonical = transform(
                parsed,
                account_id=source_cfg["account_id"],
                currency=source_cfg.get("currency", "INR"),
                source_statement=input_file.name,
            )
            classify_rules(canonical)
            classify_llm(canonical)

            new_count = 0
            date_min = None
            date_max = None

            for txn in canonical:
                content_hash = compute_content_hash(txn)
                existing = session.exec(
                    _select(Transaction).where(Transaction.content_hash == content_hash)
                ).first()
                if existing is not None:
                    continue

                db_txn = Transaction(
                    content_hash=content_hash,
                    txn_date=txn.txn_date,
                    account_id=txn.account_id,
                    source_statement=input_file.name,
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
            run.status = "failed"
            run.error_message = traceback.format_exc()
            run.completed_at = datetime.datetime.now(datetime.UTC)
            session.commit()
        finally:
            # Always clean up the temp file
            input_file.unlink(missing_ok=True)
            logger.info("Upload temp file cleaned up: %s", input_file.name)
