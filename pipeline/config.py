"""
Central configuration for the raw-to-canonical pipeline.

Reads .env for secrets; everything else is plain Python constants so you can
see (and grep) every knob in one place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from sqlmodel import Session

load_dotenv()

# ---------------------------------------------------------------------------
# Environment — controls which DB file is used (prod vs test vs onboarding)
# pytest overrides this via in-memory SQLite, so it doesn't use either file.
# ---------------------------------------------------------------------------
APP_ENV: str = os.getenv("APP_ENV", "prod")


def resolve_db_path(
    repo_root: Path,
    app_env: str,
    arth_db_name: str | None,
    arth_db_path: str | None,
) -> Path:
    """Pick the SQLite file used by the API and CLI.

    Precedence (highest first):
    1. ``ARTH_DB_PATH`` — absolute or ``~`` path to the database file (any location).
    2. ``ARTH_DB_NAME`` — **basename only** (slashes stripped); file lives under
       ``<repo_root>/data/`` — e.g. ``ARTH_DB_NAME=arth_onboarding.db``.
    3. ``APP_ENV=test`` → ``data/arth_test.db``.
    4. ``APP_ENV=onboarding`` or ``onboarding_test`` → ``data/arth_onboarding.db``
       (dedicated onboarding SQLite; same file for both env names).
    5. Otherwise → ``data/arth_main.db`` (default production database).

    Parameters ``arth_db_name`` / ``arth_db_path`` are the raw env string or ``None``
    when unset, so unit tests can call this without mutating the environment.
    """
    data_dir = repo_root / "data"
    if arth_db_path:
        return Path(arth_db_path).expanduser().resolve()
    if arth_db_name:
        # Only the basename is honoured so ARTH_DB_NAME cannot escape ``data/``.
        safe = Path(arth_db_name.strip()).name
        if not safe:
            raise ValueError("ARTH_DB_NAME must not be empty after stripping")
        return (data_dir / safe).resolve()
    if app_env == "test":
        return (data_dir / "arth_test.db").resolve()
    if app_env in ("onboarding", "onboarding_test"):
        return (data_dir / "arth_onboarding.db").resolve()
    return (data_dir / "arth_main.db").resolve()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "personal-data"
OUTPUT_DIR = REPO_ROOT / "data" / "output"
# Env overrides for onboarding QA (see ``resolve_db_path``).
_ARTH_DB_NAME_ENV: str | None = os.getenv("ARTH_DB_NAME", "").strip() or None
_ARTH_DB_PATH_ENV: str | None = os.getenv("ARTH_DB_PATH", "").strip() or None
DB_PATH: Path = resolve_db_path(REPO_ROOT, APP_ENV, _ARTH_DB_NAME_ENV, _ARTH_DB_PATH_ENV)

# Source files — add new statements here as they arrive
GSHEET_BENCHMARK_FILE = DATA_DIR / "GSheet_Transactions_modifiedForLLMTraining.csv"

# ---------------------------------------------------------------------------
# Source configs  (parser_key -> metadata used by transformer / classifier)
# Per-user rows live in SQLite ``user_pipeline_sources``; use
# :func:`get_source_configs` with a ``Session`` and ``ARTH_USER_ID`` (CLI) or
# the logged-in username (API). This dict stays empty so imports remain valid.
# To add a new source for a user: insert a row (see ``scripts/migrate_sashank_config_to_db.py``)
# and register a parser in ``parsers/__init__.py``.
# ---------------------------------------------------------------------------
SOURCE_CONFIGS: dict[str, dict] = {}


def get_source_configs(user_id: str, session: Session) -> dict[str, dict]:
    """Load file-pipeline source metadata for *user_id* from ``UserPipelineSource``.

    Returns the same shape historically stored in ``SOURCE_CONFIGS``:
    ``{ source_key: { "account_id", "currency", "source_statement" } }`` where
    ``source_statement`` is the folder name under :data:`DATA_DIR` (DB column
    ``statement_folder``).
    """
    from sqlmodel import select

    from api.models import UserPipelineSource

    rows = session.exec(
        select(UserPipelineSource).where(UserPipelineSource.user_id == user_id)
    ).all()
    out: dict[str, dict] = {}
    for r in rows:
        folder = (r.statement_folder or "").strip() or r.source_key
        out[r.source_key] = {
            "account_id": r.account_id,
            "currency": r.currency or "INR",
            "source_statement": folder,
        }
    return out

# ---------------------------------------------------------------------------
# LLM model selection
# ---------------------------------------------------------------------------

# Set to a specific model key to force a single model, or "auto" to use the
# fallback chain below.  "none" disables LLM classification entirely.
LLM_MODEL: str = os.getenv("LLM_MODEL", "auto")

# Ordered fallback chain: try each model in sequence until one succeeds.
# Chosen based on the March 2026 benchmark (see docs/evaluations/llm-benchmark-2026-03/).
# Spans 3 providers so a provider-wide outage doesn't block the pipeline.
LLM_FALLBACK_CHAIN: list[str] = [
    "gemini-3.1-flash-lite",   # primary — best value (81% accuracy, $0.0025/20 txns)
    "gemini-2.5-flash",        # backup 1 — same provider, slightly costlier
    "claude-haiku-4-5",        # backup 2 — different provider (Anthropic)
    "gpt-5-mini",              # backup 3 — different provider (OpenAI)
]

# Mapping from friendly name -> (provider, exact_model_id)
LLM_MODEL_MAP: dict[str, tuple[str, str]] = {
    "claude-haiku-4-5":      ("anthropic", "claude-haiku-4-5"),
    "claude-sonnet-4-6":     ("anthropic", "claude-sonnet-4-6"),
    "claude-opus-4-7":       ("anthropic", "claude-opus-4-7"),
    "claude-opus-4-6":       ("anthropic", "claude-opus-4-6"),
    "gpt-5-mini":            ("openai",    "gpt-5-mini-2025-08-07"),
    "gpt-5-nano":            ("openai",    "gpt-5-nano-2025-08-07"),
    "gpt-5.4":               ("openai",    "gpt-5.4"),
    "gpt-5.4-mini":          ("openai",    "gpt-5.4-mini"),
    "gpt-5.4-nano":          ("openai",    "gpt-5.4-nano"),
    "gpt-5.4-pro":           ("openai",    "gpt-5.4-pro"),
    "gemini-3.1-flash-lite": ("google",    "gemini-3.1-flash-lite"),
    "gemini-3-flash":        ("google",    "gemini-3-flash-preview"),
    "gemini-2.5-flash":      ("google",    "gemini-2.5-flash"),
    "gemini-2.5-flash-lite": ("google",    "gemini-2.5-flash-lite"),
}

# Pricing per 1M tokens (USD).  Used by the benchmark to compute cost.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":      {"input": 1.00,  "output": 5.00},
    "claude-sonnet-4-6":     {"input": 3.00,  "output": 15.00},
    "claude-opus-4-7":       {"input": 5.00,  "output": 25.00},
    "claude-opus-4-6":       {"input": 5.00,  "output": 25.00},
    "gpt-5-mini":            {"input": 0.25,  "output": 2.00},
    "gpt-5-nano":            {"input": 0.05,  "output": 0.40},
    "gpt-5.4":               {"input": 2.50,  "output": 15.00},
    "gpt-5.4-mini":          {"input": 0.75,  "output": 4.50},
    "gpt-5.4-nano":          {"input": 0.20,  "output": 1.25},
    "gpt-5.4-pro":           {"input": 30.00, "output": 270.00},
    "gemini-3.1-flash-lite": {"input": 0.25,  "output": 1.50},
    "gemini-3-flash":        {"input": 0.50,  "output": 3.00},
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
}

# API keys — transaction **classification** pipeline only (separate from conversational agent).
# Prefer *_FOR_CLASSIFIER so usage is trackable per product; fall back to legacy names for CI/scripts.
# When ``ARTH_DEMO_MODE`` is set, ``GOOGLE_API_KEY_DEMO_CLASSIFIER`` overrides (demo deployments only).
# During Gmail ingest or statement-upload background jobs,
# :func:`api.services.classifier_runtime.user_classifier_runtime` may temporarily overlay
# these module attributes with per-user values from encrypted ``UserSecrets``
# (see ``POST /api/onboarding/api-key``).
OPENAI_API_KEY: str = (
    os.getenv("OPENAI_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
)
ANTHROPIC_API_KEY: str = (
    os.getenv("ANTHROPIC_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("ANTHROPIC_API_KEY", "").strip()
)

# Public demo: bill/track Gemini for transaction classification separately from chat (see GOOGLE_API_KEY_DEMO_CHAT in agent.config).
_ARTH_DEMO_MODE = os.getenv("ARTH_DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
_GOOGLE_DEMO_CLASSIFIER = os.getenv("GOOGLE_API_KEY_DEMO_CLASSIFIER", "").strip()
GOOGLE_API_KEY: str = (
    (_GOOGLE_DEMO_CLASSIFIER if _ARTH_DEMO_MODE and _GOOGLE_DEMO_CLASSIFIER else "")
    or os.getenv("GOOGLE_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)

# ---------------------------------------------------------------------------
# LLM classifier tuning
# ---------------------------------------------------------------------------
LLM_BATCH_SIZE: int = 15
LLM_CACHE_DIR: Path = REPO_ROOT / "data" / ".llm_cache"
