"""
Scraper control endpoints — /api/scraper/*

These endpoints let the Phase 3 UI (and you from the terminal / Swagger) control
the Gmail email scraper without ever touching a config file.

Endpoints:
  GET   /status        — scheduler status + last run summary
  POST  /trigger       — run one scrape cycle right now (waits for result)
  POST  /start         — start or resume the scheduler
  POST  /stop          — pause the scheduler
  PATCH /config        — change the polling interval
  GET   /emails        — list processed emails (paginated, filterable)
  POST  /oauth/init    — kick off Gmail OAuth2 flow (opens browser)
  GET   /oauth/status  — is Gmail authenticated?
  GET   /coverage      — which accounts have real-time email coverage
"""

from __future__ import annotations

import datetime
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlmodel import Session, col, select

from api.auth import get_current_user
from api.database import SQLiteSerializingSession, get_engine, get_session
from api.models import ProcessedEmail
from scraper.config import GMAIL_TOKEN_PATH
from scraper.config_loader import all_sender_emails, get_bank_senders_config
from scraper.orchestrator import run_historical_backfill
from scraper.scheduler import (
    get_status,
    note_gmail_reconnected,
    reschedule,
    resume_scheduler,
    stop_scheduler,
    trigger_now,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request / response schemas ───────────────────────────────────────────────

class ScraperConfigUpdate(BaseModel):
    """Body for PATCH /config."""
    interval_minutes: int


class BackfillRequest(BaseModel):
    """Body for POST /backfill — historical Gmail sweep (DESKTOP_PREREQS item 4)."""

    after: datetime.date
    before: datetime.date
    sender_emails: list[str] | None = None
    gmail_query: str | None = None
    max_messages: int | None = None
    dry_run: bool = False


class ProcessedEmailOut(BaseModel):
    """Response shape for a single processed email row."""
    id: int
    gmail_message_id: str
    sender: str
    subject: str
    received_at: str
    txn_count: int
    status: str
    error_message: str | None
    processed_at: str


class ProcessedEmailsResponse(BaseModel):
    """Paginated list of processed emails."""
    items: list[ProcessedEmailOut]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─── GET /status ──────────────────────────────────────────────────────────────

@router.get("/status")
def scraper_status():
    """Return the current scheduler status and last run summary.

    Fields:
      - is_running:            True if the scheduler is actively polling.
      - is_gmail_authenticated: True if gmail_token.json exists (OAuth done).
      - is_scraping:           True if a scrape cycle is running right now.
      - interval_minutes:      Current polling interval.
      - last_run_at:           ISO-8601 timestamp of last completed cycle.
      - next_run_at:           ISO-8601 timestamp of next scheduled cycle.
      - last_emails_processed: Emails that produced transactions in the last run.
      - last_emails_skipped:   Non-transaction emails skipped in the last run.
      - last_emails_failed:    Emails that errored in the last run.
      - last_txns_created:     New DB rows inserted in the last run.
      - last_error:            First error message from the last run (or null).
      - gmail_reauth_required: True if Google rejected the saved Gmail login —
                               call POST /api/scraper/oauth/init to reconnect.
      - price_last_run_at:     When the daily price job last finished (attempt).
      - price_last_success_at: When it last completed without error (or null).
      - price_last_error:      Last price-job error message (or null).
      - price_next_run_at:     Next scheduled 18:30 IST price refresh (or null).
      - is_price_job_running:  True while NSE/AMFI refresh is executing.
      - weekly_market_*:       Same pattern for the Sunday 19:15 IST job (prices +
                               ``nse_equity_reference`` + ``enrich_holdings``).
    """
    return get_status()


# ─── POST /trigger ─────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_scrape():
    """Run one full scrape cycle immediately and return the result.

    This call blocks until the cycle finishes (usually a few seconds).
    Uses run_in_threadpool so the FastAPI event loop is not blocked.

    Returns the same fields as ScrapeResult:
      emails_found, emails_processed, emails_skipped, emails_failed,
      txns_created, errors (list of error strings).

    Raises 503 if Gmail is not authenticated yet.
    """
    try:
        result = await run_in_threadpool(trigger_now)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "emails_found":     result.emails_found,
        "emails_processed": result.emails_processed,
        "emails_skipped":   result.emails_skipped,
        "emails_failed":    result.emails_failed,
        "txns_created":     result.txns_created,
        "errors":           result.errors,
    }


# ─── POST /backfill ────────────────────────────────────────────────────────────


@router.post("/backfill")
async def scraper_backfill(
    body: BackfillRequest,
    current_user: str = Depends(get_current_user),
):
    """Historical Gmail import between two dates (exclusive ``before``).

    Reuses the same parse → DB path as live scraping; ``processed_emails`` prevents
    duplicates. Can be long-running — uses a thread pool like POST /trigger.
    """
    if not GMAIL_TOKEN_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Gmail isn't connected yet. Finish setup and use Connect Gmail first.",
        )

    if body.gmail_query and body.sender_emails:
        raise HTTPException(
            status_code=400,
            detail="Use either gmail_query or sender_emails, not both.",
        )

    def _run() -> dict:
        with SQLiteSerializingSession(get_engine()) as s:
            result = run_historical_backfill(
                session=s,
                after=body.after,
                before=body.before,
                user_id=current_user,
                sender_emails=body.sender_emails,
                gmail_query=body.gmail_query,
                max_messages=body.max_messages,
                dry_run=body.dry_run,
            )
            return {
                "emails_found": result.emails_found,
                "emails_processed": result.emails_processed,
                "emails_skipped": result.emails_skipped,
                "emails_failed": result.emails_failed,
                "txns_created": result.txns_created,
                "errors": result.errors,
            }

    try:
        return await run_in_threadpool(_run)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─── POST /start ───────────────────────────────────────────────────────────────

@router.post("/start")
def start_scraper():
    """Start or resume the email scraper scheduler.

    - If the scheduler was paused via POST /stop, this resumes it.
    - If the scheduler was never started (e.g. Gmail wasn't authenticated at
      boot time), this starts a fresh scheduler now.
    - Returns 503 if Gmail is not authenticated.
    """
    if not GMAIL_TOKEN_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Gmail isn't connected yet. Use Connect Gmail in setup before starting the email reader.",
        )
    resume_scheduler()
    return {"status": "started", **get_status()}


# ─── POST /stop ────────────────────────────────────────────────────────────────

@router.post("/stop")
def stop_scraper():
    """Pause the scheduler.  No emails will be fetched until POST /start is called.

    A currently-running scrape cycle will finish before the scheduler pauses.
    """
    stop_scheduler()
    return {"status": "stopped", **get_status()}


# ─── PATCH /config ─────────────────────────────────────────────────────────────

@router.patch("/config")
def update_scraper_config(body: ScraperConfigUpdate):
    """Update the email polling interval.

    The change takes effect immediately on the running scheduler.
    If the scheduler isn't running, the new interval is stored and used
    on the next start.

    Body: { "interval_minutes": 30 }
    Minimum: 1 minute.
    """
    try:
        reschedule(body.interval_minutes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"interval_minutes": body.interval_minutes, **get_status()}


# ─── GET /emails ───────────────────────────────────────────────────────────────

@router.get("/emails", response_model=ProcessedEmailsResponse)
def list_processed_emails(
    status: str | None = Query(None, description="Filter by status: processed | skipped | failed"),
    sender: str | None = Query(None, description="Filter by sender address"),
    date_from: datetime.date | None = Query(None, description="Inclusive start date (received_at)"),
    date_to: datetime.date | None = Query(None, description="Inclusive end date (received_at)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    *,
    session: Session = Depends(get_session),
):
    """List emails the scraper has attempted to process.

    Useful for debugging: shows which emails were parsed, which were skipped
    (non-transaction emails), and which failed with an error.
    """
    from sqlmodel import func

    query = select(ProcessedEmail)

    if status:
        query = query.where(ProcessedEmail.status == status)
    if sender:
        query = query.where(ProcessedEmail.sender == sender)
    if date_from:
        query = query.where(
            col(ProcessedEmail.received_at) >= datetime.datetime.combine(
                date_from, datetime.time.min
            )
        )
    if date_to:
        query = query.where(
            col(ProcessedEmail.received_at) <= datetime.datetime.combine(
                date_to, datetime.time.max
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    query = (
        query
        .order_by(col(ProcessedEmail.received_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = session.exec(query).all()
    total_pages = max(1, (total + page_size - 1) // page_size)

    return ProcessedEmailsResponse(
        items=[_email_to_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ─── POST /oauth/init ──────────────────────────────────────────────────────────

@router.post("/oauth/init")
def oauth_init():
    """Start the Gmail OAuth2 flow.

    This opens a browser window on the machine running the API server with
    Google's consent screen.  After you click "Allow", the token is saved to
    data/gmail_token.json and the scheduler is automatically activated.

    Check GET /api/scraper/oauth/status to confirm authentication completed.

    If Gmail is already authenticated, returns a message saying so (no-op).
    """
    if GMAIL_TOKEN_PATH.exists():
        return {
            "status": "already_authenticated",
            "message": "Gmail is already connected on this device. You can continue in the setup steps.",
        }

    def _run_oauth_flow() -> None:
        """Execute the full OAuth flow in a background thread.

        This must run in a thread because InstalledAppFlow.run_local_server()
        starts a temporary HTTP server and blocks until the browser redirect
        callback arrives — it can't run on the FastAPI event loop.
        """
        try:
            from scraper.gmail_client import GmailClient
            logger.info("Starting Gmail OAuth flow (browser will open)...")
            client = GmailClient()
            client.authenticate()
            logger.info("Gmail OAuth completed — token saved, activating scheduler")
            note_gmail_reconnected()
            # Now that the token exists, activate the scheduler
            resume_scheduler()
        except Exception as exc:
            logger.error("Gmail OAuth flow failed: %s", exc)

    thread = threading.Thread(target=_run_oauth_flow, daemon=True, name="gmail-oauth")
    thread.start()

    return {
        "status": "oauth_started",
        "message": (
            "A browser window will open shortly with Google’s sign-in. After you tap "
            "“Allow,” come back to this app — the next step will look for your bank emails."
        ),
    }


# ─── GET /oauth/status ─────────────────────────────────────────────────────────

@router.get("/oauth/status")
def oauth_status():
    """Check whether Gmail has been authenticated.

    Returns:
      - is_authenticated: True if gmail_token.json exists (token was saved).
      - token_path:       Path where the token is stored (for reference).
      - message:          Human-readable explanation.

    Note: this only checks whether the token *file* exists — it does not
    attempt a live token validation.  If the token is corrupted or expired
    beyond refresh, the next scrape cycle will surface the error.
    """
    authenticated = GMAIL_TOKEN_PATH.exists()
    return {
        "is_authenticated": authenticated,
        "token_path": str(GMAIL_TOKEN_PATH),
        "message": (
            "Gmail is connected. Arth can read new bank alert emails in the background when you are set up."
            if authenticated
            else
            "Gmail is not connected yet. In Arth, use “Connect Gmail” to sign in with Google on this device."
        ),
    }


# ─── GET /coverage ─────────────────────────────────────────────────────────────

# Static coverage map — derived from Step 3a real-email discovery.
# This captures what we confirmed actually works vs. what doesn't.
_COVERAGE: list[dict] = [
    # ── HDFC (sample rows — replace account_id values in your fork with real pipeline keys) ──
    {
        "account_id": "HDFC_SAVINGS_SAMPLE",
        "bank": "HDFC",
        "account_type": "Savings (sample)",
        "has_email_coverage": True,
        "email_sender": "alerts@hdfcbank.net",
        "covered_transaction_types": [
            "UPI outbound (payments from savings)",
            "UPI inbound (peer-to-peer credits)",
        ],
        "not_covered": [
            "Net banking transfers outbound — many banks skip email for these",
            "Inbound credits: salary, standing instructions, NEFT/RTGS credits",
        ],
        "notes": "In practice, a large share of day-to-day spend is UPI and shows up in near real time.",
    },
    {
        "account_id": "HDFC_CC_SAMPLE_A",
        "bank": "HDFC",
        "account_type": "Credit card (sample A)",
        "has_email_coverage": True,
        "email_sender": "alerts@hdfcbank.net",
        "covered_transaction_types": [
            "Credit card swipes / online purchases (outbound spending)",
        ],
        "not_covered": [
            "Refunds and cashback credits",
            "EMI auto-debits (statement only)",
            "E-mandate / auto-pay alerts with no amount — cannot be parsed",
        ],
        "notes": "Swipes usually arrive in real time; refunds often need the statement.",
    },
    {
        "account_id": "HDFC_CC_SAMPLE_B",
        "bank": "HDFC",
        "account_type": "Credit card (sample B)",
        "has_email_coverage": True,
        "email_sender": "alerts@hdfcbank.net",
        "covered_transaction_types": [
            "Credit card swipes / online purchases (outbound spending)",
        ],
        "not_covered": [
            "Refunds and cashback credits",
            "EMI auto-debits (statement only)",
        ],
        "notes": "Same pattern as the other sample HDFC card row.",
    },
    # ── ICICI ─────────────────────────────────────────────────────────────────
    {
        "account_id": "ICICI_SAVINGS_SAMPLE",
        "bank": "ICICI",
        "account_type": "Savings (sample)",
        "has_email_coverage": True,
        "email_sender": "customernotification@icici.bank.in",
        "covered_transaction_types": [
            "IMPS outbound via mobile banking (manually initiated transfers)",
            "NEFT outbound via mobile banking (manually initiated transfers)",
        ],
        "not_covered": [
            "All inbound credits (salary, NEFT/RTGS credits, interest)",
            "Broker / demat transactions (often no transactional email — use statements)",
            "Automatic payments and standing instructions",
        ],
        "notes": (
            "Only manual transfers initiated in the banking app may get email alerts; "
            "inbound transactions are often statement-only."
        ),
    },
]


@router.get("/coverage")
def email_coverage(
    session: Session = Depends(get_session),
    current_user: str = Depends(get_current_user),
):
    """Return the email alert coverage map.

    Shows which accounts have real-time email coverage, which transaction types
    are captured, and what remains statement-only.

    This is based on confirmed real-email discovery (Step 3a) — not assumptions.
    ``configured_senders`` reflects DB-backed scraper config when present.
    """
    email_accounts = sum(1 for a in _COVERAGE if a["has_email_coverage"])
    bank = get_bank_senders_config(session, current_user)
    senders = all_sender_emails(bank)
    return {
        "summary": {
            "total_accounts": len(_COVERAGE),
            "accounts_with_email_coverage": email_accounts,
            "configured_senders": senders,
        },
        "accounts": _COVERAGE,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _email_to_out(row: ProcessedEmail) -> ProcessedEmailOut:
    return ProcessedEmailOut(
        id=row.id,
        gmail_message_id=row.gmail_message_id,
        sender=row.sender,
        subject=row.subject,
        received_at=row.received_at.isoformat() if row.received_at else "",
        txn_count=row.txn_count,
        status=row.status,
        error_message=row.error_message,
        processed_at=row.processed_at.isoformat() if row.processed_at else "",
    )
