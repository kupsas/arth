"""
Fernet symmetric encryption for PII stored in the asset tables (holdings, etc.).

Environment:
  FERNET_KEY — url-safe base64 key from ``Fernet.generate_key()``. If missing on
  first use, a key is generated, appended to ``.env`` in the repo root (when that
  file exists), and a loud warning is logged. **Back up the key**; without it,
  encrypted columns cannot be recovered.

Design:
  * ``account_platform`` stays a plain display label (e.g. "ICICI Direct") so you
    can filter in SQL. Sensitive account identifiers go in
    ``account_identifier_encrypted``.
  * ``EncryptedStr`` is a SQLAlchemy ``TypeDecorator``: Python sees plaintext;
    SQLite stores Fernet ciphertext (base64).
"""

from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

# Repo root: api/services/encryption.py -> parents[2] == Arth/
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env"

_fernet: Fernet | None = None


def _append_fernet_key_to_dotenv(key_b64: str) -> None:
    """Persist a newly generated key so the next process uses the same key."""
    if not _ENV_PATH.exists():
        logger.warning(
            "FERNET_KEY was auto-generated but %s does not exist — key is only "
            "in memory for this process. Create .env and set FERNET_KEY=%s",
            _ENV_PATH,
            key_b64,
        )
        return
    block = (
        "\n# Phase A — Fernet key for encrypted PII on holdings (back this up; loss = unreadable data)\n"
        f"FERNET_KEY={key_b64}\n"
    )
    existing = _ENV_PATH.read_text(encoding="utf-8")
    if "FERNET_KEY=" in existing:
        return
    with _ENV_PATH.open("a", encoding="utf-8") as f:
        f.write(block)
    logger.warning(
        "FERNET_KEY was missing; generated and appended to %s. Back up this key.",
        _ENV_PATH,
    )


def _resolve_fernet_key_bytes() -> bytes:
    """Load ``FERNET_KEY`` from the environment, generating and persisting if absent."""
    load_dotenv(_ENV_PATH)
    raw = (os.environ.get("FERNET_KEY") or "").strip()
    if raw:
        return raw.encode("ascii")
    key = Fernet.generate_key()
    key_str = key.decode("ascii")
    warnings.warn(
        "FERNET_KEY was not set. A new key was generated and will be appended to .env "
        "if possible. Back up FERNET_KEY — losing it makes encrypted fields unreadable.",
        UserWarning,
        stacklevel=2,
    )
    _append_fernet_key_to_dotenv(key_str)
    os.environ["FERNET_KEY"] = key_str
    return key


def get_fernet() -> Fernet:
    """Return a process-wide Fernet instance (lazy-init)."""
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_resolve_fernet_key_bytes())
    return _fernet


def encrypt_field(plaintext: str) -> str:
    """Encrypt a string; returns url-safe base64 ciphertext (Fernet token)."""
    if plaintext == "":
        return ""
    token = get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_field(ciphertext: str) -> str:
    """Decrypt a Fernet token string back to UTF-8 plaintext."""
    if ciphertext == "":
        return ""
    raw = get_fernet().decrypt(ciphertext.encode("ascii"))
    return raw.decode("utf-8")


class EncryptedStr(TypeDecorator):
    """Map Python ``str`` (plaintext) to a VARCHAR of Fernet ciphertext in the DB."""

    impl = String(4096)
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        # Empty string means "no value" — store NULL so we do not persist noise ciphertext.
        if value is None or value == "":
            return None
        return encrypt_field(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None or value == "":
            return value
        try:
            return decrypt_field(value)
        except InvalidToken as e:
            logger.error("EncryptedStr decrypt failed (wrong FERNET_KEY or corrupt data): %s", e)
            raise
