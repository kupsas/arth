"""
PDF helpers for statement emails — decrypt bank password-protected PDFs.

Banks typically email statements as PDFs encrypted with a static password
(date of birth, PAN fragment, etc.). We read those secrets from environment
variables (see ``.env.example``) and decrypt to a temporary file so pdfplumber
or other tools can open them.

Uses ``pikepdf`` because it handles owner/user PDF passwords more reliably than
passing passwords through pdfplumber alone.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pikepdf

# Password resolution for statement PDFs is centralized in ``scraper/pdf_passwords.py``
# (env-key chains + UserSecrets). This module only decrypts bytes given a password string.


def decrypt_pdf(pdf_bytes: bytes, password: str) -> Path:
    """Decrypt ``pdf_bytes`` if needed and write the result to a temp ``.pdf`` file.

    Unencrypted PDFs are copied through unchanged. Encrypted PDFs are opened with
    ``password``; if opening without a password fails with ``PasswordError``, we
    retry with the supplied password.

    Args:
        pdf_bytes: Raw PDF file bytes (as attached in email).
        password: User password for the PDF. Use ``""`` only for known-unencrypted files.

    Returns:
        Path to a newly created temporary ``.pdf`` file. The caller should delete
        it when finished (e.g. ``path.unlink(missing_ok=True)``).

    Raises:
        pikepdf.PasswordError: if the PDF is encrypted and the password is wrong.
        OSError: if the temp file cannot be written.
    """
    fd, name = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    out_path = Path(name)

    bio = io.BytesIO(pdf_bytes)
    try:
        try:
            pdf = pikepdf.open(bio)
        except pikepdf.PasswordError:
            bio.seek(0)
            pdf = pikepdf.open(bio, password=password)
        try:
            pdf.save(out_path)
        finally:
            pdf.close()
    except Exception:
        out_path.unlink(missing_ok=True)
        raise

    return out_path
