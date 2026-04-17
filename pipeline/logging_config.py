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
  - INFO and above → stdout (colourless, human-readable)
  - DEBUG and above → data/logs/arth.log  (rotating, 5MB × 3 backups)

Format:  2026-03-19 14:30:01 [INFO ] pipeline.run: Stage 1: parsing hdfc_savings
"""

from __future__ import annotations

import logging
import logging.handlers
import re
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
]


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


def setup_logging(*, log_level: int = logging.INFO) -> None:
    """Configure the root logger with a stdout handler and a rotating file handler.

    Idempotent: calling this more than once (e.g. in tests) is safe — it checks
    whether handlers are already attached before adding new ones.

    Args:
        log_level: The minimum level emitted to stdout.  The file handler always
                   captures DEBUG and above so nothing is lost permanently.
    """
    root = logging.getLogger()

    # Guard: don't add duplicate handlers if called a second time.
    if root.handlers:
        return

    # The root logger must be set to the lowest level we care about anywhere;
    # individual handlers then filter further.  DEBUG lets the file handler
    # capture everything even when stdout is set to INFO.
    root.setLevel(logging.DEBUG)

    # ── Stdout handler (INFO+) ────────────────────────────────────────────
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(_FORMATTER)
    stream_handler.addFilter(_SecretRedactionFilter())
    root.addHandler(stream_handler)

    # ── Rotating file handler (DEBUG+) ────────────────────────────────────
    # maxBytes=5MB, backupCount=3 → keeps at most ~20MB of logs on disk.
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
    file_handler.addFilter(_SecretRedactionFilter())
    root.addHandler(file_handler)

    # ── Silence noisy third-party libs ───────────────────────────────────
    for lib in _NOISY_LIBS:
        logging.getLogger(lib).setLevel(logging.WARNING)

    logging.getLogger(__name__).debug("Logging initialised — file: %s", _LOG_FILE)
