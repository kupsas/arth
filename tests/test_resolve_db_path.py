"""Unit tests for ``pipeline.config.resolve_db_path`` (onboarding / test DB selection)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.config import resolve_db_path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_default_prod_uses_arth_main_db() -> None:
    assert resolve_db_path(REPO_ROOT, "prod", None, None) == (REPO_ROOT / "data" / "arth_main.db").resolve()


def test_app_env_test_uses_arth_test_db() -> None:
    assert resolve_db_path(REPO_ROOT, "test", None, None) == (REPO_ROOT / "data" / "arth_test.db").resolve()


def test_app_env_onboarding_test_uses_arth_onboarding_db() -> None:
    assert resolve_db_path(REPO_ROOT, "onboarding_test", None, None) == (
        REPO_ROOT / "data" / "arth_onboarding.db"
    ).resolve()


def test_app_env_onboarding_uses_arth_onboarding_db() -> None:
    assert resolve_db_path(REPO_ROOT, "onboarding", None, None) == (
        REPO_ROOT / "data" / "arth_onboarding.db"
    ).resolve()


def test_arth_db_name_overrides_app_env() -> None:
    # Even with APP_ENV=test, explicit filename wins (placed under data/).
    assert resolve_db_path(REPO_ROOT, "test", "arth_custom.db", None) == (
        REPO_ROOT / "data" / "arth_custom.db"
    ).resolve()


def test_arth_db_name_uses_basename_only() -> None:
    """Path segments in ARTH_DB_NAME must not escape ``data/``."""
    assert resolve_db_path(REPO_ROOT, "prod", "../../tmp/evil.db", None) == (REPO_ROOT / "data" / "evil.db").resolve()


def test_arth_db_path_wins_over_everything() -> None:
    explicit = REPO_ROOT / "data" / "somewhere_else.db"
    assert resolve_db_path(REPO_ROOT, "test", "ignored.db", str(explicit)) == explicit.resolve()


def test_arth_db_path_expands_user() -> None:
    home = REPO_ROOT / "data"
    path_str = str(home / "from_tilde.db")
    # No tilde in path_str — expanduser is still applied; behaviour is identical to Path.resolve.
    assert resolve_db_path(REPO_ROOT, "prod", None, path_str) == (home / "from_tilde.db").resolve()


def test_arth_db_name_empty_raises() -> None:
    with pytest.raises(ValueError, match="ARTH_DB_NAME"):
        resolve_db_path(REPO_ROOT, "prod", "   ", None)
