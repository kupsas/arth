"""Phase A.6 — Fernet helpers (round-trip, wrong key, empty values)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken

import api.services.encryption as encryption


@pytest.fixture
def isolated_fernet(monkeypatch):
    """Fresh process key so tests do not share a poisoned global Fernet instance."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", key)
    encryption._fernet = None  # noqa: SLF001 — test seam
    yield
    encryption._fernet = None  # noqa: SLF001


def test_encrypt_decrypt_round_trip(isolated_fernet) -> None:
    msg = "PRAN-123456789012"
    token = encryption.encrypt_field(msg)
    assert token != msg
    assert encryption.decrypt_field(token) == msg


def test_empty_string_stays_empty(isolated_fernet) -> None:
    assert encryption.encrypt_field("") == ""
    assert encryption.decrypt_field("") == ""


def test_decrypt_with_wrong_key_raises(isolated_fernet, monkeypatch) -> None:
    token = encryption.encrypt_field("secret")
    other = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", other)
    encryption._fernet = None  # noqa: SLF001
    with pytest.raises(InvalidToken):
        encryption.decrypt_field(token)


def test_get_fernet_returns_singleton_within_key(isolated_fernet) -> None:
    a = encryption.get_fernet()
    b = encryption.get_fernet()
    assert a is b
