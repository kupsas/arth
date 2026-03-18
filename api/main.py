"""
FastAPI application for Arth — personal finance transaction API.

Run with:
    uvicorn api.main:app --reload --port 8000

Swagger docs at http://localhost:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.database import init_db
from api.routes import pipeline, transactions
from api.routes.scraper import router as scraper_router
from scraper.scheduler import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the Arth API server.

    Startup:
      1. Ensure all DB tables exist (init_db is idempotent — safe to call every boot).
      2. Start the email scraper scheduler.  If Gmail hasn't been authenticated
         yet (no token file), start_scheduler() is a no-op and the scheduler
         stays inactive until the user completes OAuth via /api/scraper/oauth/init.

    Shutdown:
      3. Clean up the APScheduler background thread so the process exits cleanly.
    """
    init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="Arth API",
    description="Personal finance transaction pipeline & query API",
    version="0.2.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the future Next.js dashboard and Swagger UI
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Next.js dev server (Phase 3)
        "http://localhost:8000",   # Swagger UI served by FastAPI itself
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(transactions.router, prefix="/api/transactions", tags=["Transactions"])
app.include_router(pipeline.router,      prefix="/api/pipeline",      tags=["Pipeline"])
app.include_router(scraper_router,       prefix="/api/scraper",        tags=["Scraper"])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check():
    """Simple liveness probe — returns 200 if the server is up."""
    return {"status": "ok"}
