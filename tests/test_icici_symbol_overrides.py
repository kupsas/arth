"""Tests for optional ICICI ↔ NSE symbol overrides JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.holding_parsers.icici_direct_equity import ICICI_SHORT_TO_NSE
from pipeline.icici_symbol_overrides import (
    invalidate_overrides_cache,
    load_overrides,
    merge_with_disk,
    save_overrides,
)


@pytest.fixture
def overrides_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point overrides at an isolated JSON file and clear the read cache."""
    p = tmp_path / "icici_nse_symbol_overrides.json"
    monkeypatch.setenv("ARTH_ICICI_SYMBOL_OVERRIDES", str(p))
    invalidate_overrides_cache()
    yield p
    invalidate_overrides_cache()


def test_merge_with_disk_combines_static_and_file(overrides_tmp_path: Path) -> None:
    save_overrides(
        {
            "icici_short_to_nse": {"ZZFILE": "ZZNSE"},
            "isin_to_nse": {},
        }
    )
    base = {"A": "AA", "ZZFILE": "OLD"}
    merged = merge_with_disk(base, "icici_short_to_nse")
    assert merged["A"] == "AA"
    assert merged["ZZFILE"] == "ZZNSE"


def test_load_overrides_empty_when_missing_file(overrides_tmp_path: Path) -> None:
    assert not overrides_tmp_path.is_file()
    assert load_overrides() == {
        "icici_short_to_nse": {},
        "isin_to_nse": {},
    }


def test_merge_icici_short_to_nse_includes_static_builtins(overrides_tmp_path: Path) -> None:
    merged = merge_with_disk(ICICI_SHORT_TO_NSE, "icici_short_to_nse")
    assert len(merged) >= len(ICICI_SHORT_TO_NSE)


def test_save_roundtrip(overrides_tmp_path: Path) -> None:
    save_overrides(
        {"icici_short_to_nse": {"X": "Y"}, "isin_to_nse": {"INE1": "SYM"}}
    )
    data = json.loads(overrides_tmp_path.read_text(encoding="utf-8"))
    assert data["icici_short_to_nse"]["X"] == "Y"
    assert data["isin_to_nse"]["INE1"] == "SYM"
