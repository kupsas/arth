"""
Shared logging configuration for the Arth pipeline, API, and scraper.

Call ``setup_logging()`` once at application startup (e.g. in api/main.py's
lifespan, or at the top of pipeline/run.py's main()).  Every other module
just does ``logger = logging.getLogger(__name__)`` — no further config needed.

**Convention:** importable library code (``pipeline/``, ``api/``, ``scraper/``)
should log, not ``print``.  One-off CLI tools under ``scripts/`` and human-facing
CLI output (e.g. ``pipeline/validator.py`` reports) may still use ``print`` for
stdout UX.

Output behaviour:
  - INFO and above → stdout (colourless, human-readable), unless you set
    ``ARTH_LOG_LEVEL`` in the environment (see below).
  - DEBUG and above → data/logs/arth.log  (rotating, 10MB × 5 backups)

**ARTH_LOG_LEVEL** (optional): Controls how chatty the terminal is. The log file
always keeps DEBUG and above so detailed traces stay on disk. Valid values match
Python's logging names: DEBUG, INFO, WARNING, ERROR, CRITICAL (also WARN / FATAL
as aliases). Invalid values fall back to INFO with a runtime warning.

Format:  2026-03-19 14:30:01 [INFO ] pipeline.run: Stage 1: parsing hdfc_savings
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import warnings
from pathlib import Path

# Single shared formatter so stdout and file look identical (makes copy-pasting
# a log line from the terminal into the file — or vice versa — unambiguous).
_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Where on-disk logs land.  Relative to the repo root, not this file's location.
_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
_LOG_FILE = _LOG_DIR / "arth.log"

# Maps env text → numeric level.  Keep in sync with what we document in .env.example.
_ARTH_LOG_LEVEL_NAMES: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}

# Third-party libraries tend to be chatty at DEBUG level.  We silence them at
# WARNING so our own pipeline messages aren't drowned out in the log file.
_NOISY_LIBS = [
    "httpx",
    "httpcore",
    "urllib3",
    "google",
    "google_auth_oauthlib",
    "googleapiclient.discovery_cache",
    "oauthlib",
    "requests_oauthlib",
    "anthropic",
    "openai",
    "apscheduler",
    "multipart",
    # pdfminer.six (used by pdfplumber) logs every PDF operator at DEBUG.
    "pdfminer",
    # LiteLLM dumps full curl commands, raw JSON responses, and cost math at DEBUG.
    "LiteLLM",
    "litellm",
]


def get_log_file_path() -> Path:
    """Return the absolute path to the rotating application log file.

    Use this from diagnostics/download features so every caller agrees on the
    same location as :func:`setup_logging`.  The file may not exist until the
    first log line is written; the parent ``data/logs`` directory is created
    during ``setup_logging()``.
    """
    return _LOG_FILE


def _stdout_level_from_env() -> int:
    """Read ``ARTH_LOG_LEVEL`` and return the numeric level for the StreamHandler.

    If the variable is missing, empty, or not one of the known names, we default
    to INFO — logging must stay usable even with typos in ``.env``.
    """
    raw = os.environ.get("ARTH_LOG_LEVEL", "INFO").strip()
    if not raw:
        return logging.INFO

    key = raw.upper()
    if key in _ARTH_LOG_LEVEL_NAMES:
        return _ARTH_LOG_LEVEL_NAMES[key]

    allowed = ", ".join(sorted(k for k in _ARTH_LOG_LEVEL_NAMES if k not in ("WARN", "FATAL")))
    warnings.warn(
        f"ARTH_LOG_LEVEL={raw!r} is not recognized; using INFO. "
        f"Valid values: {allowed}.",
        UserWarning,
        stacklevel=3,
    )
    return logging.INFO


class _SecretRedactionFilter(logging.Filter):
    """Best-effort redaction of secret-like values in log messages.

    We keep this lightweight and pattern-based so it can run on every log line.
    It is not a substitute for avoiding secret logging at the source, but it
    protects us against accidental leaks from third-party debug logs.
    """

    _REDACTIONS: list[tuple[re.Pattern[str], str]] = [
        # JSON key/value style tokens in request/response logs
        (re.compile(r'("access_token"\s*:\s*")[^"]+(")', re.IGNORECASE), r"\1***REDACTED***\2"),
        (re.compile(r'("refresh_token"\s*:\s*")[^"]+(")', re.IGNORECASE), r"\1***REDACTED***\2"),
        (re.compile(r'("client_secret"\s*:\s*")[^"]+(")', re.IGNORECASE), r"\1***REDACTED***\2"),
        # Query/body style OAuth fields
        (re.compile(r"(code=)[^&\s]+", re.IGNORECASE), r"\1***REDACTED***"),
        (re.compile(r"(code_verifier=)[^&\s]+", re.IGNORECASE), r"\1***REDACTED***"),
        # Authorization headers
        (re.compile(r"(Authorization['\"]?\s*:\s*['\"]?Basic\s+)[A-Za-z0-9+/=]+", re.IGNORECASE), r"\1***REDACTED***"),
        (re.compile(r"(Authorization['\"]?\s*:\s*['\"]?Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1***REDACTED***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in self._REDACTIONS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
        return True


class _FinancialDataRedactionFilter(logging.Filter):
    """Extra scrubbing for finance-shaped data (belt-and-suspenders).

    Prefer fixing call sites so sensitive values never enter ``logger.*`` — see
    the logging plan's Phase 1b.  This filter catches accidents: long digit runs
    that look like bank accounts, card-like groups, and Indian IFSC codes when
    they appear as standalone tokens in a log line.
    """

    _REDACTIONS: list[tuple[re.Pattern[str], str]] = [
        # Indian IFSC: four letters, literal 0, six alphanumeric (branch id).
        # Case-insensitive; redact the whole token (bank + branch).
        (re.compile(r"\b[A-Za-z]{4}0[A-Za-z0-9]{6}\b"), "***REDACTED***"),
        # Common card layout: four groups of four digits, optional space/dash between groups.
        (re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"), "***REDACTED***"),
        # Long runs of digits typical of bank account numbers (9–18 inclusive).
        # Runs after card-shaped patterns so spaced card numbers match explicitly first.
        (re.compile(r"\b\d{9,18}\b"), "***REDACTED***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in self._REDACTIONS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
        return True


def _all_redaction_filters() -> tuple[logging.Filter, ...]:
    """Order matters: strip secrets first, then finance-shaped patterns."""
    return (_SecretRedactionFilter(), _FinancialDataRedactionFilter())


def setup_logging(*, log_level: int | None = None) -> None:
    """Configure the root logger with a stdout handler and a rotating file handler.

    Idempotent: calling this more than once (e.g. in tests) is safe — it checks
    whether handlers are already attached before adding new ones.

    Args:
        log_level: Minimum level for **stdout** only.  When ``None`` (default),
                   the level is taken from ``ARTH_LOG_LEVEL`` in the environment,
                   or INFO if unset.  Pass an explicit level from tests or CLIs
                   to override the env.  The file handler always records DEBUG+.
    """
    root = logging.getLogger()

    # Guard: don't add duplicate handlers if called a second time.
    if root.handlers:
        return

    # Resolve stdout verbosity: explicit kwargs beat environment.
    resolved_stream_level = log_level if log_level is not None else _stdout_level_from_env()

    # The root logger must be set to the lowest level we care about anywhere;
    # individual handlers then filter further.  DEBUG lets the file handler
    # capture everything even when stdout is set to INFO.
    root.setLevel(logging.DEBUG)

    # ── Stdout handler (verbosity from env or kwargs) ─────────────────────
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(resolved_stream_level)
    stream_handler.setFormatter(_FORMATTER)
    for f in _all_redaction_filters():
        stream_handler.addFilter(f)
    root.addHandler(stream_handler)

    # ── Rotating file handler (DEBUG+) ──────────────────────────────────────
    # maxBytes=10MB, backupCount=5 → at most ~60MB of rotated logs on disk.
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
    for f in _all_redaction_filters():
        file_handler.addFilter(f)
    root.addHandler(file_handler)

    # ── Silence noisy third-party libs ───────────────────────────────────
    for lib in _NOISY_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)

    # IMF SDMX library logs HTTP URLs at INFO and benign XML reader notes at WARNING;
    # keep only real failures on the terminal (ERROR+).
    logging.getLogger("sdmx").setLevel(logging.ERROR)

    logging.getLogger(__name__).debug("Logging initialised — file: %s", _LOG_FILE)
