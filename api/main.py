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

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from api.auth import get_current_user
from api.database import get_engine, init_db
from api.routes import metrics, pipeline, transactions
from api.services.price_feed import run_startup_price_sync
from api.routes.auth import router as auth_router
from api.routes.goals import router as goals_router
from api.routes.holdings import router as holdings_router
from api.routes.investment_transactions import router as investment_transactions_router
from api.routes.liabilities import router as liabilities_router
from api.routes.prices import router as prices_router
from api.routes.settings import router as settings_router
from api.routes.recurring import router as recurring_router
from api.routes.scraper import router as scraper_router
from pipeline.logging_config import setup_logging
from scraper.scheduler import shutdown_scheduler, start_scheduler
from sqlmodel import Session

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the Arth API server.

    Startup:
      1. Configure structured logging (stdout INFO + rotating file DEBUG).
      2. Ensure all DB tables exist (init_db is idempotent — safe to call every boot).
      3. Phase A.4.2 — If there are market-priced holdings, backfill stale NSE ``prices``
         rows then refresh marks (runs in a worker thread so bind stays responsive).
      4. Start APScheduler: daily price job at 18:30 IST always; Gmail poll only
         if ``gmail_token.json`` exists (or after OAuth adds the email job).

    Shutdown:
      5. Clean up the APScheduler background thread so the process exits cleanly.
    """
    setup_logging()
    logger.info("Arth API starting up...")
    init_db()

    def _sync_startup_prices() -> None:
        try:
            with Session(get_engine()) as session:
                run_startup_price_sync(session)
                session.commit()
        except Exception:
            logger.exception("Startup price backfill/refresh failed — will retry at next scheduled run")

    await asyncio.to_thread(_sync_startup_prices)
    start_scheduler()
    logger.info("Arth API ready")
    yield
    logger.info("Arth API shutting down...")
    shutdown_scheduler()


app = FastAPI(
    title="Arth API",
    description="Personal finance transaction pipeline & query API",
    version="0.4.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the Next.js dashboard and Swagger UI.
# allow_credentials=True is required for cookies to be sent cross-port.
#
# For Cloudflare Tunnel (or any non-localhost dashboard URL), add origins via
# .env: CORS_EXTRA_ORIGINS=https://your-app.trycloudflare.com,https://other...
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

# ---------------------------------------------------------------------------
# Auth routes — public (no session required for login/logout)
# ---------------------------------------------------------------------------
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])

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
app.include_router(recurring_router,    prefix="/api/recurring",     tags=["Recurring"],     dependencies=_auth)
app.include_router(goals_router,        prefix="/api/goals",         tags=["Goals"],         dependencies=_auth)
app.include_router(settings_router,    prefix="/api/settings",      tags=["Settings"],      dependencies=_auth)
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
