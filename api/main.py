"""
FastAPI application for Arth — personal finance transaction API.

Run with:
    uvicorn api.main:app --reload --port 8000

Swagger docs at http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from api.auth import get_current_user
from api.database import SQLiteSerializingSession, get_engine, init_db
from api.routes import metrics, pipeline, transactions, user_config
from api.services.inflation_service import sync_imf_cpi_history
from api.services.price_feed import run_startup_price_sync
from api.routes.auth import router as auth_router
from api.routes.chat_ws import router as chat_router
from api.routes.goals import router as goals_router
from api.routes.life_events import router as life_events_router
from api.routes.holdings import router as holdings_router
from api.routes.investment_transactions import router as investment_transactions_router
from api.routes.liabilities import router as liabilities_router
from api.routes.prices import router as prices_router
from api.routes.settings import router as settings_router
from api.routes.recurring import router as recurring_router
from api.routes.surplus import router as surplus_router
from api.routes.liquidity import router as liquidity_router
from api.routes.goal_suggestions import router as goal_suggestions_router
from api.routes.inflation import router as inflation_router
from api.routes.simulate import router as simulate_router
from api.routes.scraper import router as scraper_router
from api.routes.scraper_config import router as scraper_config_router
from api.routes.onboarding import router as onboarding_router
from api.routes.setup import router as setup_router
from api.errors import ArthError, arth_internal_error
from api.middleware import RequestLoggingMiddleware
from pipeline.logging_config import setup_logging
from scraper.scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)


async def _run_startup_db_maintenance_in_thread() -> None:
    """Run price backfill/refresh, then IMF inflation sync — both off the event loop.

    SQLite allows only **one writer at a time** (WAL helps readers, not concurrent writers).
    We used to start price sync and inflation sync as **two** asyncio tasks; both opened a
    session immediately and could flush INSERTs at once → ``database is locked``.
    Running them **one after another** keeps startup non-blocking for Uvicorn while
    avoiding that race.
    """

    logger.info(
        "Startup DB maintenance: beginning price sync (NSE/AMFI/yfinance), "
        "then inflation (IMF CPI) — sequential to avoid SQLite writer contention"
    )

    def _sync_startup_prices() -> None:
        try:
            with SQLiteSerializingSession(get_engine()) as session:
                run_startup_price_sync(session)
                session.commit()
        except Exception:
            logger.exception("Startup price backfill/refresh failed — will retry at next scheduled run")

    def _sync_inflation() -> None:
        try:
            if os.getenv("INFLATION_DISABLE_IMF", "").strip().lower() in (
                "1",
                "true",
                "yes",
            ):
                logger.info("INFLATION_DISABLE_IMF — skipping startup inflation sync")
                return
            with SQLiteSerializingSession(get_engine()) as session:
                sync_imf_cpi_history(session)
        except Exception:
            logger.exception(
                "Startup inflation sync failed — will retry on weekly job or refresh"
            )

    try:
        await asyncio.to_thread(_sync_startup_prices)
        await asyncio.to_thread(_sync_inflation)
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the Arth API server.

    Startup:
      1. Configure structured logging (stdout INFO + rotating file DEBUG).
      2. Ensure all DB tables exist (init_db is idempotent — safe to call every boot).
      3. Start APScheduler: daily price job at 18:30 IST; weekly prices + NSE reference +
         holdings enrich Sun 19:15 IST; Gmail poll only if ``gmail_token.json`` exists
         (or after OAuth adds the email job).
      4. Phase A.4.2 — If there are market-priced holdings, backfill stale NSE ``prices``
         then refresh marks **in the background**. Uvicorn used to await this before
         ``yield``, which left "Waiting for application startup" for minutes when NSE
         or AMFI was slow or unreachable.
      5. Sub-Plan F — IMF India CPI monthly YoY history sync **after** price sync in the
         same background task (weekly job also scheduled in ``scraper.scheduler``).

    Shutdown:
      6. Clean up the APScheduler background thread so the process exits cleanly.
    """
    setup_logging()
    logger.info("Arth API starting up...")
    init_db()
    start_scheduler()
    # Schedule maintenance without awaiting — server becomes ready immediately.
    # Keep a reference on app.state so the task is not GC'd before it runs (asyncio footgun).
    app.state.startup_db_maintenance_task = asyncio.create_task(
        _run_startup_db_maintenance_in_thread()
    )
    logger.info("Arth API ready (startup DB maintenance runs in background)")
    yield
    logger.info("Arth API shutting down...")
    shutdown_scheduler()


app = FastAPI(
    title="Arth API",
    description="Personal finance transaction pipeline & query API",
    version="0.4.0",
    lifespan=lifespan,
)


@app.exception_handler(ArthError)
async def arth_error_handler(request: Request, exc: ArthError) -> Response:
    """Log structured client/server errors; response shape matches OpenAPI ``HTTPException``."""
    rid = getattr(request.state, "request_id", None)
    if exc.status_code >= 500:
        logger.error(
            "%s %s %s status=%s request_id=%s",
            exc.error_code,
            request.method,
            request.url.path,
            exc.status_code,
            rid,
        )
    else:
        logger.warning(
            "%s %s %s status=%s request_id=%s",
            exc.error_code,
            request.method,
            request.url.path,
            exc.status_code,
            rid,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> Response:
    """Delegate validation and HTTP errors; log and wrap unknown failures."""
    if isinstance(exc, RequestValidationError):
        return await request_validation_exception_handler(request, exc)
    if isinstance(exc, HTTPException):
        return await http_exception_handler(request, exc)

    rid = getattr(request.state, "request_id", None)
    logger.exception(
        "Unhandled exception %s %s request_id=%s",
        request.method,
        request.url.path,
        rid,
    )
    ie = arth_internal_error()
    return JSONResponse(status_code=500, content={"detail": ie.detail})


# ---------------------------------------------------------------------------
# Middleware — CORS next to the app; request logging outermost (registered last).
# ---------------------------------------------------------------------------
_cors_extra = os.getenv("CORS_EXTRA_ORIGINS", "")
_cors_extra_list = [o.strip() for o in _cors_extra.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Next.js dev server
        "http://localhost:8000",   # Swagger UI served by FastAPI itself
        *_cors_extra_list,
    ],
    allow_credentials=True,       # required for Set-Cookie / Cookie headers
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLoggingMiddleware)

# ---------------------------------------------------------------------------
# Auth routes — public (no session required for login/logout)
# ---------------------------------------------------------------------------
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(setup_router, prefix="/api/setup", tags=["Setup"])
app.include_router(chat_router)

# ---------------------------------------------------------------------------
# Protected routes — all require a valid session cookie.
#
# The dependencies=[Depends(get_current_user)] argument applies the auth check
# to EVERY endpoint on that router, without modifying the router files themselves.
# ---------------------------------------------------------------------------
_auth = [Depends(get_current_user)]

app.include_router(transactions.router, prefix="/api/transactions", tags=["Transactions"], dependencies=_auth)
app.include_router(metrics.router,      prefix="/api/metrics",       tags=["Metrics"],       dependencies=_auth)
app.include_router(pipeline.router,     prefix="/api/pipeline",      tags=["Pipeline"],      dependencies=_auth)
app.include_router(scraper_router,      prefix="/api/scraper",       tags=["Scraper"],       dependencies=_auth)
app.include_router(
    scraper_config_router,
    prefix="/api/scraper-config",
    tags=["Scraper config"],
    dependencies=_auth,
)
app.include_router(recurring_router,    prefix="/api/recurring",     tags=["Recurring"],     dependencies=_auth)
app.include_router(surplus_router,      prefix="/api/surplus",       tags=["Surplus"],       dependencies=_auth)
app.include_router(liquidity_router,    prefix="/api/liquidity",     tags=["Liquidity"],     dependencies=_auth)
app.include_router(goal_suggestions_router, prefix="/api/goal-suggestions", tags=["Goal suggestions"], dependencies=_auth)
app.include_router(inflation_router,   prefix="/api/inflation",     tags=["Inflation"],     dependencies=_auth)
app.include_router(simulate_router,    prefix="/api/simulate",      tags=["Simulation"],    dependencies=_auth)
app.include_router(goals_router,        prefix="/api/goals",         tags=["Goals"],         dependencies=_auth)
app.include_router(life_events_router, prefix="/api/life-events",   tags=["Life events"],   dependencies=_auth)
app.include_router(settings_router,    prefix="/api/settings",      tags=["Settings"],      dependencies=_auth)
app.include_router(
    user_config.router,
    prefix="/api/user",
    tags=["User classification"],
    dependencies=_auth,
)
app.include_router(
    onboarding_router,
    prefix="/api/onboarding",
    tags=["Onboarding"],
    dependencies=_auth,
)
app.include_router(holdings_router,           prefix="/api/holdings",                  tags=["Holdings"],                  dependencies=_auth)
app.include_router(investment_transactions_router, prefix="/api/investment-transactions", tags=["Investment transactions"], dependencies=_auth)
app.include_router(liabilities_router,       prefix="/api/liabilities",               tags=["Liabilities"],               dependencies=_auth)
app.include_router(prices_router,            prefix="/api/prices",                    tags=["Prices"],                    dependencies=_auth)


# ---------------------------------------------------------------------------
# Root — redirect to Swagger UI
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root_redirect():
    """Send humans to Swagger UI; machines can still use /health or /api/... ."""
    return RedirectResponse(url="/docs", status_code=307)


# ---------------------------------------------------------------------------
# Health check — public, no auth (used by launchd, monitoring, etc.)
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check():
    """Simple liveness probe — returns 200 if the server is up."""
    return {"status": "ok"}
