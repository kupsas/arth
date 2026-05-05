"""
Decrypt password-protected statement PDFs on manual upload.

Email ingest uses the same password chains via :mod:`scraper.pdf_passwords`; uploads
set :func:`~scraper.secrets_context.statement_secrets_context` so those resolvers see
the logged-in user's ``UserSecrets``.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pikepdf
from sqlmodel import Session

from scraper.pdf_passwords import (
    resolve_hdfc_cc_pdf_password_candidates,
    resolve_hdfc_combined_pdf_password_candidates,
    resolve_icici_statement_pdf_password_candidates,
    resolve_nse_pdf_password_candidates,
)
from scraper.pdf_utils import decrypt_pdf_with_password_candidates
from scraper.secrets_context import statement_secrets_context

logger = logging.getLogger(__name__)


class NeedsPdfPassword(Exception):
    """PDF is encrypted and no configured or supplied password worked."""


class WrongPdfPassword(Exception):
    """User supplied ``pdf_password`` but it did not unlock the PDF."""


def _pdf_bytes_need_password(raw: bytes) -> bool:
    """Return True when *raw* is an encrypted PDF."""
    bio = io.BytesIO(raw)
    try:
        pdf = pikepdf.open(bio)
        pdf.close()
        return False
    except pikepdf.PasswordError:
        return True
    except Exception:
        # Corrupt / non-PDF — let downstream parsers report errors.
        return False


def _merged_password_candidates(session: Session, user_id: str) -> list[str]:
    """Ordered unique list of env + onboarding-derived PDF passwords."""
    ordered: list[str] = []
    seen: set[str] = set()
    with statement_secrets_context(session, user_id):
        for fn in (
            resolve_icici_statement_pdf_password_candidates,
            resolve_hdfc_combined_pdf_password_candidates,
            resolve_hdfc_cc_pdf_password_candidates,
            resolve_nse_pdf_password_candidates,
        ):
            for p in fn():
                s = (p or "").strip()
                if s and s not in seen:
                    seen.add(s)
                    ordered.append(s)
    return ordered


def prepare_upload_pdf_path(
    saved_path: Path,
    *,
    session: Session,
    user_id: str,
    pdf_password: str | None,
) -> tuple[Path, list[Path]]:
    """Return a path pdfplumber can open, plus extra temp files to delete after processing.

    - Unencrypted PDF → ``(saved_path, [])``.
    - Encrypted → decrypt to a new temp file; ``cleanup`` includes ``saved_path`` (encrypted
      blob) so callers remove it once the decrypted copy exists.

    Raises:
        NeedsPdfPassword: encrypted, auto candidates exhausted, no user password.
        WrongPdfPassword: ``pdf_password`` was sent but nothing unlocked the file.
    """
    if saved_path.suffix.lower() != ".pdf":
        return saved_path, []

    raw = saved_path.read_bytes()
    if not _pdf_bytes_need_password(raw):
        return saved_path, []

    user_pw = (pdf_password or "").strip()
    candidates: list[str] = []
    if user_pw:
        candidates.append(user_pw)
    candidates.extend(_merged_password_candidates(session, user_id))

    if not candidates:
        raise NeedsPdfPassword()

    try:
        decrypted_path, _used = decrypt_pdf_with_password_candidates(raw, candidates)
    except pikepdf.PasswordError:
        if user_pw:
            raise WrongPdfPassword() from None
        raise NeedsPdfPassword() from None

    logger.info(
        "Upload PDF decrypted for processing (user_pw=%s)", "yes" if user_pw else "no"
    )
    # Encrypted upload temp replaced by decrypted temp — remove the password-protected blob.
    saved_path.unlink(missing_ok=True)
    return decrypted_path, []


__all__ = [
    "NeedsPdfPassword",
    "WrongPdfPassword",
    "prepare_upload_pdf_path",
]
