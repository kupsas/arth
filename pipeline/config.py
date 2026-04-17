"""
Central configuration for the raw-to-canonical pipeline.

Reads .env for secrets; everything else is plain Python constants so you can
see (and grep) every knob in one place.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Environment — controls which DB file is used (prod vs test)
# pytest overrides this via in-memory SQLite, so it doesn't use either file.
# ---------------------------------------------------------------------------
APP_ENV: str = os.getenv("APP_ENV", "prod")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "personal-data"
OUTPUT_DIR = REPO_ROOT / "data" / "output"
DB_PATH: Path = REPO_ROOT / "data" / ("arth_test.db" if APP_ENV == "test" else "arth.db")

# Source files — add new statements here as they arrive
GSHEET_BENCHMARK_FILE = DATA_DIR / "GSheet_Transactions_modifiedForLLMTraining.csv"

# ---------------------------------------------------------------------------
# Source configs  (parser_key -> metadata used by transformer / classifier)
# To add a new source: add an entry here and a parser in parsers/__init__.py
# ---------------------------------------------------------------------------
SOURCE_CONFIGS: dict[str, dict] = {
    "hdfc_savings": {
        "account_id": "HDFC_SAL_3703",
        "currency": "INR",
        "source_statement": "HDFC_Savings",   # directory of yearly .txt files
    },
    # HDFC credit cards — each key points at a directory of 12 monthly CSVs.
    # The HDFCCreditCardParser.parse() accepts either a file or a directory.
    "hdfc_cc_1905": {
        "account_id": "HDFC_CC_1905",
        "currency": "INR",
        "source_statement": "1905_CC",   # directory of monthly CSVs
    },
    "hdfc_cc_5778": {
        "account_id": "HDFC_CC_5778",
        "currency": "INR",
        "source_statement": "5778_CC",   # directory of monthly CSVs
    },
    # ICICI savings account — directory of yearly PDFs
    "icici_savings": {
        "account_id": "ICICI_SAV_6118",
        "currency": "INR",
        "source_statement": "ICICI_Savings",   # directory of yearly .pdf files
    },
}

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
    "claude-opus-4-6":       ("anthropic", "claude-opus-4-6"),
    "gpt-5-mini":            ("openai",    "gpt-5-mini-2025-08-07"),
    "gpt-5-nano":            ("openai",    "gpt-5-nano-2025-08-07"),
    "gemini-3.1-flash-lite": ("google",    "gemini-3.1-flash-lite-preview"),
    "gemini-3-flash":        ("google",    "gemini-3-flash-preview"),
    "gemini-2.5-flash":      ("google",    "gemini-2.5-flash"),
    "gemini-2.5-flash-lite": ("google",    "gemini-2.5-flash-lite"),
}

# Pricing per 1M tokens (USD).  Used by the benchmark to compute cost.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5":      {"input": 1.00,  "output": 5.00},
    "claude-sonnet-4-6":     {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":       {"input": 5.00,  "output": 25.00},
    "gpt-5-mini":            {"input": 0.25,  "output": 2.00},
    "gpt-5-nano":            {"input": 0.05,  "output": 0.40},
    "gemini-3.1-flash-lite": {"input": 0.25,  "output": 1.50},
    "gemini-3-flash":        {"input": 0.50,  "output": 3.00},
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
}

# API keys — transaction **classification** pipeline only (separate from conversational agent).
# Prefer *_FOR_CLASSIFIER so usage is trackable per product; fall back to legacy names for CI/scripts.
OPENAI_API_KEY: str = (
    os.getenv("OPENAI_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
)
ANTHROPIC_API_KEY: str = (
    os.getenv("ANTHROPIC_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("ANTHROPIC_API_KEY", "").strip()
)
GOOGLE_API_KEY: str = (
    os.getenv("GOOGLE_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)

# ---------------------------------------------------------------------------
# LLM classifier tuning
# ---------------------------------------------------------------------------
LLM_BATCH_SIZE: int = 15
LLM_CACHE_DIR: Path = REPO_ROOT / "data" / ".llm_cache"
