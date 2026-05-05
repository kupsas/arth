"""
Email scraper + daily price refresh — APScheduler integrated with FastAPI.

Responsibilities:
  - Run ``scrape_new_emails()`` on a configurable interval (default: 15 min)
    when ``gmail_token.json`` exists **and** at least one user has completed first-run
    setup (``setup_completed_at``). Skipped when ``APP_ENV=onboarding_test``.
  - Run **daily price refresh** at **18:30 Asia/Kolkata** (after Indian cash
    market close): NSE/AMFI/yfinance via ``refresh_all_prices`` (Phase A.4.1).
    This job is **always** scheduled so portfolio marks work without Gmail.
  - Run **weekly market cache refresh** Sunday **19:15 Asia/Kolkata** (after that
    day's daily price job): ``refresh_all_prices`` → ``refresh_nse_equity_reference``
    → ``enrich_holdings`` (see :func:`api.services.weekly_market_refresh.run_weekly_market_data_refresh`).
    Only runs while the API process is up — no separate Mac cron required.
  - After prices commit, run **holding liquidity refresh** (Sub-Plan C): updates
    stored ``earliest_liquidity_date`` for all users so T+2 sleeves track the calendar.
  - Provide start / stop / trigger / reschedule / status controls used by
    the /api/scraper/* endpoints (Step 8)
  - If Gmail hasn't been authenticated yet, the **email** job is omitted;
    the price job still runs. After OAuth, ``resume_scheduler()`` adds the
    email poll job if missing.
  - Keep a shared GmailClient across polling cycles so token refresh is
    seamless and we don't re-authenticate on every run

Threading model:
  - APScheduler's BackgroundScheduler runs jobs in a separate daemon thread.
  - FastAPI's event loop is never blocked by the scrape job.
  - trigger_now() also runs in the calling thread (FastAPI route calls it
    via run_in_threadpool so the event loop stays unblocked).
  - A threading.Lock protects the mutable status state that both the
    scheduler thread and the API handler thread may read/write.
  - A second flag (_is_scraping) prevents two concurrent scrape runs
    (e.g. a scheduled poll firing while a manual trigger is in progress).
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import select

from api.database import SQLiteSerializingSession, get_engine
from api.models import AppUser
from api.services.inflation_service import sync_imf_cpi_history
from pipeline.config import APP_ENV, LLM_MODEL  # noqa: F401 — LLM_MODEL for context; not used directly here
from scraper.config import GMAIL_TOKEN_PATH, POLL_INTERVAL_MINUTES
from scraper.gmail_client import GmailClient, GmailReauthRequiredError
from api.services.liquidity_service import refresh_all_users_liquidity_dates
from api.services.price_feed import (
    backfill_nse_portfolio_gaps,
    refresh_all_prices,
)
from api.services.weekly_market_refresh import run_weekly_market_data_refresh
from scraper.orchestrator import ScrapeResult, scrape_new_emails

logger = logging.getLogger(__name__)

# India session — NSE equity cash close; bhavcopy is typically ready by then.
_DAILY_PRICE_TZ = ZoneInfo("Asia/Kolkata")
# If the machine sleeps past 18:30 IST, APScheduler still runs the job within this
# window (default grace is tiny, so a late wake used to skip the whole day).
_DAILY_PRICE_MISFIRE_GRACE_SEC = 6 * 60 * 60


# ─── Module-level state ────────────────────────────────────────────────────────
# All mutable state lives here rather than in a class so the FastAPI app and
# the scheduler job share the same objects without passing references around.

_scheduler: BackgroundScheduler | None = None
_gmail_client: GmailClient | None = None

# Status fields — written by the scheduler thread, read by the API handler.
# Protected by _status_lock.
_status_lock = threading.Lock()
_last_run_at: datetime.datetime | None = None
_last_result: ScrapeResult | None = None
_last_error: str | None = None
_interval_minutes: int = POLL_INTERVAL_MINUTES

# Prevents two scrape runs from overlapping (scheduled poll + manual trigger).
_is_scraping = False
_scraping_lock = threading.Lock()

# Daily price job — last success / error for /api/scraper/status (Phase A.4).
_price_status_lock = threading.Lock()
_price_last_run_at: datetime.datetime | None = None
_price_last_success_at: datetime.datetime | None = None
_price_last_error: str | None = None
_is_price_job_running = False

# Weekly NSE reference + holdings enrichment (after Sunday evening price job).
_weekly_market_status_lock = threading.Lock()
_weekly_market_last_run_at: datetime.datetime | None = None
_weekly_market_last_success_at: datetime.datetime | None = None
_weekly_market_last_error: str | None = None
_is_weekly_market_job_running = False

# Set True when Google returns invalid_grant / missing token in the scheduler
# path — API can show a banner; cleared after a successful email scrape cycle.
_gmail_reauth_required: bool = False


# ─── Core job function ────────────────────────────────────────────────────────


def note_gmail_reconnected() -> None:
    """Clear the ``gmail_reauth_required`` API flag after a successful OAuth flow."""
    global _gmail_reauth_required
    with _status_lock:
        _gmail_reauth_required = False


def _remove_email_scraper_job() -> None:
    """Stop interval polling until Gmail is re-authorized (avoids log spam).

    After the user completes POST /api/scraper/oauth/init, ``resume_scheduler()``
    calls ``_ensure_email_scrape_job()`` and polling resumes.
    """
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return
    if _scheduler.get_job("email_scraper") is None:
        return
    _scheduler.remove_job("email_scraper")
    logger.warning(
        "Email scraper job removed until Gmail is reconnected "
        "(POST /api/scraper/oauth/init)."
    )

def _run_scrape_job() -> ScrapeResult:
    """Execute one full scrape cycle.

    Called by:
      - APScheduler (background thread, every _interval_minutes)
      - trigger_now() (calling thread, usually a FastAPI route handler thread)

    Opens its own DB session and closes it when done — each cycle is a
    clean unit of work with no session leakage between runs.
    """
    global _gmail_client, _last_run_at, _last_result, _last_error, _is_scraping
    global _gmail_reauth_required

    # ── Concurrency guard ─────────────────────────────────────────────────────
    with _scraping_lock:
        if _is_scraping:
            logger.info("Another email check is still running — this trigger was skipped.")
            # Return the in-progress status rather than erroring out
            return _last_result or ScrapeResult()
        _is_scraping = True

    try:
        logger.debug("Scrape cycle started")

        # ── Ensure authenticated Gmail client ─────────────────────────────────
        # Reuse the existing client if it's already authenticated.
        # GmailClient.authenticate() handles token refresh automatically on
        # subsequent calls (no browser re-prompt needed).
        if _gmail_client is None or not _gmail_client.is_authenticated:
            _gmail_client = GmailClient()
            # Background thread must not open a browser — revoked refresh tokens
            # raise GmailReauthRequiredError instead (handled below).
            _gmail_client.authenticate(allow_interactive_oauth=False)

        # ── Run the scrape ────────────────────────────────────────────────────
        with SQLiteSerializingSession(get_engine()) as session:
            result = scrape_new_emails(session=session, client=_gmail_client)

        # ── Update shared status ──────────────────────────────────────────────
        with _status_lock:
            _last_run_at = datetime.datetime.now(datetime.timezone.utc)
            _last_result = result
            _last_error = result.errors[0] if result.errors else None
            _gmail_reauth_required = False

        logger.info(
            "Email check finished — %d transaction(s) added · %d email(s) checked.",
            result.txns_created,
            result.emails_processed,
        )
        logger.debug(
            "Scrape detail — processed=%d skipped=%d failed=%d txns=%d",
            result.emails_processed,
            result.emails_skipped,
            result.emails_failed,
            result.txns_created,
        )
        return result

    except GmailReauthRequiredError as exc:
        # Refresh token dead — token file may already be deleted by GmailClient.
        _gmail_client = None
        error_msg = str(exc)
        logger.critical(
            "Gmail scraper stopped until you reconnect: %s",
            error_msg,
        )
        _remove_email_scraper_job()
        failed = ScrapeResult(errors=[error_msg])
        with _status_lock:
            _last_run_at = datetime.datetime.now(datetime.timezone.utc)
            _last_error = error_msg
            _last_result = failed
            _gmail_reauth_required = True
        return failed

    except Exception as exc:
        error_msg = str(exc)
        logger.exception("Scrape cycle raised an unhandled exception: %s", error_msg)
        with _status_lock:
            _last_run_at = datetime.datetime.now(datetime.timezone.utc)
            _last_error = error_msg
        # Return a failed result so callers get a structured response
        failed = ScrapeResult(errors=[error_msg])
        with _status_lock:
            _last_result = failed
        return failed

    finally:
        with _scraping_lock:
            _is_scraping = False


def _run_daily_price_job() -> None:
    """Scheduled + idempotent path: fill NSE history gaps, then refresh all marks.

    Runs in the APScheduler worker thread. Opens its own DB session and commits.
    """
    global _price_last_run_at, _price_last_success_at, _price_last_error, _is_price_job_running

    with _price_status_lock:
        if _is_price_job_running:
            logger.info("Daily price update skipped — the previous run is still finishing.")
            return
        _is_price_job_running = True

    try:
        logger.debug("Daily price job started")
        with SQLiteSerializingSession(get_engine()) as session:
            backfill_summary = backfill_nse_portfolio_gaps(session)
            refresh_summary = refresh_all_prices(session)
            session.commit()

        # Sub-Plan C — equity/MF T+2 dates track calendar days; cheap, no network.
        try:
            with SQLiteSerializingSession(get_engine()) as liq_session:
                liq_result = refresh_all_users_liquidity_dates(liq_session)
                liq_session.commit()
            logger.debug(
                "Liquidity dates — users=%d updated=%d unchanged=%d",
                liq_result.user_count,
                liq_result.total_updated,
                liq_result.total_unchanged,
            )
        except Exception:
            logger.exception("Daily liquidity refresh failed — will retry on next schedule")

        now = datetime.datetime.now(datetime.timezone.utc)
        with _price_status_lock:
            _price_last_run_at = now
            _price_last_success_at = now
            _price_last_error = None
        _details = backfill_summary.get("details")
        n_bf = len(_details) if isinstance(_details, list) else 0
        holdings_n = refresh_summary.get("holdings_updated")
        as_of = refresh_summary.get("as_of")
        logger.info(
            "Prices updated — %s holding(s) refreshed%s.",
            holdings_n,
            f" (as of {as_of})" if as_of else "",
        )
        logger.debug(
            "Daily price detail — backfill_symbols=%d price_rows=%s holdings=%s as_of=%s",
            n_bf,
            refresh_summary.get("price_rows_upserted"),
            holdings_n,
            as_of,
        )
    except Exception as exc:
        err = str(exc)
        logger.exception("Daily price job failed: %s", err)
        now = datetime.datetime.now(datetime.timezone.utc)
        with _price_status_lock:
            _price_last_run_at = now
            _price_last_error = err
    finally:
        with _price_status_lock:
            _is_price_job_running = False


def _run_weekly_market_data_job() -> None:
    """Sunday evening: prices → ``nse_equity_reference`` → ``enrich_holdings`` (all users).

    Scheduled after the daily 18:30 IST price job so bhav/NAV from the last session
    is already in ``prices`` before we rebuild the reference table and copy caps.
    """
    global _weekly_market_last_run_at, _weekly_market_last_success_at, _weekly_market_last_error, _is_weekly_market_job_running

    with _weekly_market_status_lock:
        if _is_weekly_market_job_running:
            logger.info("Weekly markets refresh skipped — the previous run is still finishing.")
            return
        _is_weekly_market_job_running = True

    try:
        logger.debug("Weekly market data job started")
        summary = run_weekly_market_data_refresh(user_id=None)
        now = datetime.datetime.now(datetime.timezone.utc)
        with _weekly_market_status_lock:
            _weekly_market_last_run_at = now
            _weekly_market_last_success_at = now
            _weekly_market_last_error = None
        nse_n = (summary.get("nse_equity_reference") or {}).get("symbols_total")
        cap_n = (summary.get("enrich_holdings") or {}).get("equities_cap_updated")
        logger.info("Weekly markets refresh done — reference data and holdings extras updated.")
        logger.debug(
            "Weekly market detail — nse_ref_symbols_total=%s enrich_cap_updates=%s",
            nse_n,
            cap_n,
        )
    except Exception as exc:
        err = str(exc)
        logger.exception("Weekly market data job failed: %s", err)
        now = datetime.datetime.now(datetime.timezone.utc)
        with _weekly_market_status_lock:
            _weekly_market_last_run_at = now
            _weekly_market_last_error = err
    finally:
        with _weekly_market_status_lock:
            _is_weekly_market_job_running = False


def _run_inflation_sync_job() -> None:
    """Refresh IMF India CPI monthly YoY rows in ``inflation_rates`` (weekly / manual)."""
    if os.getenv("INFLATION_DISABLE_IMF", "").strip().lower() in ("1", "true", "yes"):
        logger.debug("INFLATION_DISABLE_IMF — skipping scheduled inflation sync")
        return
    try:
        with SQLiteSerializingSession(get_engine()) as session:
            summary = sync_imf_cpi_history(session)
        if summary.get("ok"):
            # User-facing line is logged inside :func:`sync_imf_cpi_history`.
            logger.debug(
                "Weekly inflation job finished — months_written=%s latest_period=%s",
                summary.get("months_written"),
                summary.get("latest_period"),
            )
        else:
            logger.warning("Scheduled inflation sync incomplete: %s", summary)
    except Exception:
        logger.exception("Scheduled inflation sync failed")


def _email_scrape_job_skip_reason() -> str | None:
    """If non-None, do not register the background Gmail poll job.

    Polling is deferred until at least one user has ``setup_completed_at`` so a
    fresh onboarding DB (or wizard in progress) is not filled by the scheduler.
    ``APP_ENV=onboarding_test`` always skips polling for QA against a separate DB.
    """
    if (APP_ENV or "").strip().lower() == "onboarding_test":
        return "APP_ENV=onboarding_test (email poll disabled for onboarding QA)"
    if not GMAIL_TOKEN_PATH.exists():
        return f"Gmail token not found at {GMAIL_TOKEN_PATH}"
    try:
        with SQLiteSerializingSession(get_engine()) as session:
            users = session.exec(select(AppUser)).all()
            if not any(u.setup_completed_at is not None for u in users):
                return "first-run setup not completed yet (wizard or /api/setup/complete)"
    except Exception:
        logger.exception(
            "Could not read app_users.setup_completed_at — omitting email scraper job"
        )
        return "could not verify setup completion"
    return None


def _ensure_email_scrape_job() -> None:
    """Register Gmail polling if token exists, setup is done, and the job is not yet present."""
    global _scheduler
    if _scheduler is None or not _scheduler.running:
        return
    skip = _email_scrape_job_skip_reason()
    if skip is not None:
        logger.debug("Email scraper not registered: %s", skip)
        return
    if _scheduler.get_job("email_scraper") is not None:
        return
    _scheduler.add_job(
        _run_scrape_job,
        trigger="interval",
        minutes=_interval_minutes,
        id="email_scraper",
        replace_existing=True,
        next_run_time=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=10),
    )
    logger.info("Gmail connected — email checks will run on schedule.")


# ─── Public scheduler controls ────────────────────────────────────────────────

def start_scheduler(interval_minutes: int = POLL_INTERVAL_MINUTES) -> None:
    """Start the background APScheduler (daily prices always; email poll if Gmail token exists).

    Safe to call multiple times — subsequent calls when the scheduler is
    already running are silently ignored.
    """
    global _scheduler, _interval_minutes
    _interval_minutes = interval_minutes

    if _scheduler is not None and _scheduler.running:
        logger.debug("Scheduler is already running — start_scheduler() is a no-op")
        return

    _scheduler = BackgroundScheduler(timezone=datetime.timezone.utc)
    # Phase A.4.1 — 18:30 IST after market close (bhavcopy + AMFI NAV typically available).
    _scheduler.add_job(
        _run_daily_price_job,
        trigger=CronTrigger(hour=18, minute=30, timezone=_DAILY_PRICE_TZ),
        id="daily_prices",
        replace_existing=True,
        misfire_grace_time=_DAILY_PRICE_MISFIRE_GRACE_SEC,
        coalesce=True,
    )
    # Sub-Plan F — keep CPI_GENERAL monthly history fresh without hitting IMF on every request.
    _scheduler.add_job(
        _run_inflation_sync_job,
        trigger=CronTrigger(
            day_of_week="sun", hour=7, minute=0, timezone=_DAILY_PRICE_TZ
        ),
        id="weekly_inflation",
        replace_existing=True,
        misfire_grace_time=_DAILY_PRICE_MISFIRE_GRACE_SEC,
        coalesce=True,
    )
    # After Sunday 18:30 IST daily prices — refresh NSE reference + holdings enrichment.
    _scheduler.add_job(
        _run_weekly_market_data_job,
        trigger=CronTrigger(
            day_of_week="sun", hour=19, minute=15, timezone=_DAILY_PRICE_TZ
        ),
        id="weekly_market_data",
        replace_existing=True,
        misfire_grace_time=_DAILY_PRICE_MISFIRE_GRACE_SEC,
        coalesce=True,
    )

    email_skip = _email_scrape_job_skip_reason()
    if email_skip is None:
        _scheduler.add_job(
            _run_scrape_job,
            trigger="interval",
            minutes=interval_minutes,
            id="email_scraper",
            replace_existing=True,
            next_run_time=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=10),
        )
    else:
        logger.debug(
            "Email scraper job not scheduled yet — %s (daily and weekly jobs still run).",
            email_skip,
        )

    _scheduler.start()
    logger.info(
        "Background scheduler is running — daily price updates, weekly inflation and markets refresh%s.",
        ", plus email import" if email_skip is None else " (email import when you're ready)",
    )
    logger.debug(
        "Scheduler config — email poll every %d min, email=%s",
        interval_minutes,
        "on" if email_skip is None else f"off ({email_skip})",
    )


def stop_scheduler() -> None:
    """Pause the scheduler (keeps the job registered, just stops it firing).

    The scheduler can be resumed with start_scheduler() without re-adding the job.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.pause()
        logger.info("Background tasks paused.")
    else:
        logger.debug("stop_scheduler() called but scheduler was not running")


def resume_scheduler() -> None:
    """Resume a paused scheduler, or start a fresh one if needed.

    Called after OAuth completes (token file now exists) to activate a
    scheduler that was deferred at startup.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.resume()
        _ensure_email_scrape_job()
        logger.info("Background scheduler resumed.")
    else:
        # Scheduler was never started (e.g. shutdown); normal start path
        start_scheduler(_interval_minutes)


def shutdown_scheduler() -> None:
    """Cleanly shut down the scheduler.  Called from FastAPI lifespan on exit."""
    global _scheduler
    if _scheduler is not None:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("Background scheduler stopped.")
        _scheduler = None


def trigger_now() -> ScrapeResult:
    """Run one scrape cycle immediately, blocking until it completes.

    This is a synchronous function — call it from a FastAPI route via
    ``fastapi.concurrency.run_in_threadpool(trigger_now)`` so the event
    loop is not blocked.

    Returns:
        ScrapeResult with counts for the just-completed cycle.

    Raises:
        RuntimeError if Gmail has not been authenticated yet.
    """
    if not GMAIL_TOKEN_PATH.exists():
        raise RuntimeError(
            "Gmail is not authenticated. Complete OAuth first via /api/scraper/oauth/init."
        )
    return _run_scrape_job()


def reschedule(interval_minutes: int) -> None:
    """Change the polling interval while the scheduler is running.

    Args:
        interval_minutes: New interval in minutes (minimum 1).

    Raises:
        ValueError if interval_minutes < 1.
        RuntimeError if the scheduler isn't currently running.
    """
    global _interval_minutes

    if interval_minutes < 1:
        raise ValueError(f"interval_minutes must be ≥ 1, got {interval_minutes}")

    _interval_minutes = interval_minutes

    if _scheduler is None or not _scheduler.running:
        # Scheduler isn't active — just update the stored interval so the
        # next start_scheduler() call uses the new value.
        logger.info(
            "Email check interval saved — next run will use %d minute(s).",
            interval_minutes,
        )
        return

    _scheduler.reschedule_job(
        "email_scraper",
        trigger="interval",
        minutes=interval_minutes,
    )
    logger.info("Email check frequency updated to every %d minute(s).", interval_minutes)


def get_status() -> dict:
    """Return the current scheduler status as a plain dict for API serialisation.

    Fields:
        is_running:           True if the scheduler is active and polling.
        is_gmail_authenticated: True if gmail_token.json exists (OAuth done).
        is_scraping:          True if a scrape cycle is currently in progress.
        interval_minutes:     Current polling interval.
        last_run_at:          ISO-8601 UTC timestamp of last completed run (or null).
        next_run_at:          ISO-8601 UTC timestamp of next scheduled run (or null).
        last_emails_processed: From the last ScrapeResult (or null).
        last_emails_skipped:   From the last ScrapeResult (or null).
        last_emails_failed:    From the last ScrapeResult (or null).
        last_txns_created:     From the last ScrapeResult (or null).
        last_error:            First error message from the last run (or null).
        gmail_reauth_required: True if Google rejected the refresh token — user
                               must POST /api/scraper/oauth/init (browser on server).
    """
    with _status_lock:
        scheduler_running = _scheduler is not None and _scheduler.running

        next_run_at = None
        price_next_run_at = None
        weekly_market_next_run_at = None
        if _scheduler is not None and _scheduler.running:
            job = _scheduler.get_job("email_scraper")
            if job and job.next_run_time:
                next_run_at = job.next_run_time.isoformat()
            pj = _scheduler.get_job("daily_prices")
            if pj and pj.next_run_time:
                price_next_run_at = pj.next_run_time.isoformat()
            wmj = _scheduler.get_job("weekly_market_data")
            weekly_market_next_run_at = (
                wmj.next_run_time.isoformat() if wmj and wmj.next_run_time else None
            )

        last_run = _last_result

        with _price_status_lock:
            price_last_run = _price_last_run_at.isoformat() if _price_last_run_at else None
            price_last_ok = (
                _price_last_success_at.isoformat() if _price_last_success_at else None
            )
            price_err = _price_last_error
            price_busy = _is_price_job_running

        with _weekly_market_status_lock:
            wm_last_run = (
                _weekly_market_last_run_at.isoformat() if _weekly_market_last_run_at else None
            )
            wm_last_ok = (
                _weekly_market_last_success_at.isoformat()
                if _weekly_market_last_success_at
                else None
            )
            wm_err = _weekly_market_last_error
            wm_busy = _is_weekly_market_job_running

        reauth = _gmail_reauth_required

        return {
            "is_running":             scheduler_running,
            "is_gmail_authenticated": GMAIL_TOKEN_PATH.exists(),
            "gmail_reauth_required":  reauth,
            "is_scraping":            _is_scraping,
            "interval_minutes":       _interval_minutes,
            "last_run_at":            _last_run_at.isoformat() if _last_run_at else None,
            "next_run_at":            next_run_at,
            "last_emails_processed":  last_run.emails_processed if last_run else None,
            "last_emails_skipped":    last_run.emails_skipped if last_run else None,
            "last_emails_failed":     last_run.emails_failed if last_run else None,
            "last_txns_created":      last_run.txns_created if last_run else None,
            "last_error":             _last_error,
            # Daily price job (Phase A.4)
            "price_last_run_at":      price_last_run,
            "price_last_success_at":  price_last_ok,
            "price_last_error":       price_err,
            "price_next_run_at":      price_next_run_at,
            "is_price_job_running":   price_busy,
            # Weekly: prices + nse_equity_reference + enrich_holdings (Sun 19:15 IST)
            "weekly_market_last_run_at":     wm_last_run,
            "weekly_market_last_success_at": wm_last_ok,
            "weekly_market_last_error":      wm_err,
            "weekly_market_next_run_at":     weekly_market_next_run_at,
            "is_weekly_market_job_running":  wm_busy,
        }
