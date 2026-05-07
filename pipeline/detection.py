"""
Content-based statement detection for uploaded files.

Replaces filename-only heuristics: each registered parser can expose ``detect()``
so we ask “does this file look like mine?” before routing to the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

# Human-readable labels for upload UI (transaction + holding logical types).
# Transaction rows: keep ``dashboard/src/lib/statement-upload-type-labels.ts`` in sync when you add keys here.
PARSER_LABELS: dict[str, str] = {
    "hdfc_savings": "HDFC Savings Account Statement (.txt export)",
    "hdfc_savings_pdf": "HDFC Combined Bank Statement (PDF)",
    "hdfc_cc": "HDFC Credit Card Statement (.csv export)",
    "hdfc_cc_pdf": "HDFC Credit Card Statement (PDF)",
    "icici_savings": "ICICI Bank Savings Account Statement (PDF)",
    # Holding / portfolio (PDF modules + registry keys)
    "icici_direct_equity_statement_pdf": "ICICI Direct Equity Transaction Statement (PDF)",
    "icici_direct_mf_statement_pdf": "ICICI Direct Mutual Fund Statement (PDF)",
    "icici_direct_contract_note": "NSE Contract Note / Trade Confirmation (PDF)",
    "icici_ppf_pdf": "ICICI PPF Account Statement (PDF)",
    "icici_direct_equity": "ICICI Direct Equity (CSV export)",
    "icici_direct_mf": "ICICI Direct Mutual Fund (CSV export)",
    "icici_ppf": "ICICI PPF (CSV export)",
}


@dataclass(frozen=True)
class DetectionResult:
    """One parser’s opinion about an uploaded file."""

    source_type: str
    confidence: float  # 0.0–1.0
    account_hint: str | None  # last4, account tail, etc.
    label: str


@dataclass(frozen=True)
class ResolveAccountMismatch:
    """Statement last-4 does not match any configured account for this format."""

    candidate_keys: tuple[str, ...]
    detected_hint: str
    hints_on_file: dict[str, str]


@dataclass(frozen=True)
class ResolveConfirmAccount:
    """We could not read last-4 from the file (or multi-account disambiguation) — ask the user."""

    candidate_keys: tuple[str, ...]
    hints_on_file: dict[str, str]


def last4_from_user_pipeline_account_id(account_id: str) -> str | None:
    """Parse trailing four digits from canonical ``account_id`` strings used in Arth."""
    aid = (account_id or "").strip()
    for prefix in ("HDFC_SAL_", "HDFC_CC_", "ICICI_SAV_"):
        if aid.startswith(prefix):
            tail = aid[len(prefix) :]
            if len(tail) == 4 and tail.isdigit():
                return tail
    return None


def _label_for(source_type: str) -> str:
    return PARSER_LABELS.get(source_type, source_type)


def detect_transaction_file(path: Path) -> list[DetectionResult]:
    """Run ``detect()`` on each unique parser class in :data:`parsers.uploads.PARSER_REGISTRY`."""
    from pipeline.parsers import PARSER_REGISTRY

    results: list[DetectionResult] = []
    seen_classes: set[type[Any]] = set()
    for _sk, cls in PARSER_REGISTRY.items():
        if cls in seen_classes:
            continue
        seen_classes.add(cls)
        res = cls.detect(path)
        if res is not None:
            results.append(res)
    # Stable sort: confidence desc, then label
    results.sort(key=lambda r: (-r.confidence, r.label))
    return results


def detect_holding_file(path: Path) -> list[DetectionResult]:
    """Detect portfolio files: CSV via registry parsers, PDF via dedicated sniffers."""
    from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY

    results: list[DetectionResult] = []
    seen_classes: set[type[Any]] = set()
    for _sk, cls in HOLDING_PARSER_REGISTRY.items():
        if cls in seen_classes:
            continue
        seen_classes.add(cls)
        res = cls.detect(path)
        if res is not None:
            results.append(res)

    # PDF helpers not tied to HOLDING_PARSER_REGISTRY rows (statement PDFs, contract notes).
    from parsers.holdings import icici_direct_contract_note as cn_mod
    from parsers.holdings import icici_direct_equity_statement_pdf as eq_pdf_mod
    from parsers.holdings import icici_direct_mf_statement_pdf as mf_pdf_mod
    from parsers.holdings import icici_ppf_pdf as ppf_pdf_mod

    for fn in (
        eq_pdf_mod.detect_icici_equity_statement_pdf,
        mf_pdf_mod.detect_icici_mf_statement_pdf,
        cn_mod.detect_icici_contract_note_pdf,
        ppf_pdf_mod.detect_icici_ppf_pdf,
    ):
        res = fn(path)
        if res is not None:
            results.append(res)

    results.sort(key=lambda r: (-r.confidence, r.label))
    return results


def parser_class_for_transaction_source_type(source_type: str) -> type[Any] | None:
    """Map logical detection key to :class:`~parsers.uploads.base.BaseParser` subclass."""
    from parsers.uploads.hdfc_cc import HDFCCreditCardParser
    from parsers.uploads.hdfc_cc_pdf import HDFCCreditCardPdfParser
    from parsers.uploads.hdfc_savings import HDFCSavingsParser
    from parsers.uploads.hdfc_savings_pdf import HDFCSavingsPdfParser
    from parsers.uploads.icici_savings import ICICISavingsParser

    logical_map: dict[str, type[Any]] = {
        "hdfc_savings": HDFCSavingsParser,
        "hdfc_savings_pdf": HDFCSavingsPdfParser,
        "hdfc_cc": HDFCCreditCardParser,
        "hdfc_cc_pdf": HDFCCreditCardPdfParser,
        "icici_savings": ICICISavingsParser,
    }
    return logical_map.get(source_type)


def matching_user_source_keys(
    *,
    source_type: str,
    user_source_keys: list[str],
    parser_registry: dict[str, type[Any]],
) -> list[str]:
    """Return user source_keys whose parser class matches *source_type*."""
    target_cls = parser_class_for_transaction_source_type(source_type)
    if target_cls is None:
        return []
    out: list[str] = []
    for sk in user_source_keys:
        if sk in parser_registry and parser_registry[sk] is target_cls:
            out.append(sk)
    return sorted(out)


def resolve_transaction_source_key(
    *,
    source_type: str,
    account_hint: str | None,
    user_source_keys: list[str],
    parser_registry: dict[str, type[Any]],
) -> str | list[str] | None:
    """Legacy resolver — skips account validation (same as ``skip_account_validation=True``)."""
    out = resolve_upload_statement_destination(
        source_type=source_type,
        account_hint=account_hint,
        user_source_keys=user_source_keys,
        user_source_configs={},
        parser_registry=parser_registry,
        skip_account_validation=True,
    )
    return cast(str | list[str] | None, out)


def resolve_upload_statement_destination(
    *,
    source_type: str,
    account_hint: str | None,
    user_source_keys: list[str],
    user_source_configs: Mapping[str, Mapping[str, Any]],
    parser_registry: dict[str, type[Any]],
    skip_account_validation: bool = False,
) -> str | list[str] | ResolveAccountMismatch | ResolveConfirmAccount | None:
    """Pick ``source_key`` for an upload, with optional last-4 validation / confirmation steps.

    Returns:
        - ``str`` — unambiguous single key (validated or user skipped validation).
        - ``list[str]`` — show account picker (multiple keys, no way to auto-pick).
        - :class:`ResolveAccountMismatch` — file hint disagrees with on-file account(s).
        - :class:`ResolveConfirmAccount` — file did not yield a hint; user must confirm target.
        - ``None`` — no configured source for this detected format.
    """
    cands = matching_user_source_keys(
        source_type=source_type,
        user_source_keys=user_source_keys,
        parser_registry=parser_registry,
    )
    if not cands:
        return None

    hints_on_file: dict[str, str] = {}
    for sk in cands:
        cfg = user_source_configs.get(sk) or {}
        aid = str(cfg.get("account_id") or "")
        l4 = last4_from_user_pipeline_account_id(aid)
        if l4:
            hints_on_file[sk] = l4

    if skip_account_validation:
        if source_type == "hdfc_cc" and account_hint:
            hint = account_hint.strip()
            filtered = [sk for sk in cands if hint in sk]
            if len(filtered) == 1:
                return filtered[0]
            if len(filtered) > 1:
                return filtered
        if len(cands) == 1:
            return cands[0]
        return cands

    ah = (account_hint or "").strip() or None

    if ah:
        matching = [sk for sk in cands if hints_on_file.get(sk) == ah]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            return matching
        if hints_on_file:
            return ResolveAccountMismatch(
                candidate_keys=tuple(cands),
                detected_hint=ah,
                hints_on_file={sk: hints_on_file[sk] for sk in cands if sk in hints_on_file},
            )
        if source_type == "hdfc_cc":
            hint = ah
            filtered = [sk for sk in cands if hint in sk]
            if len(filtered) == 1:
                return filtered[0]
            if len(filtered) > 1:
                return filtered
        if len(cands) == 1:
            return cands[0]
        return cands

    if len(cands) == 1:
        sk0 = cands[0]
        if hints_on_file.get(sk0):
            return ResolveConfirmAccount(
                candidate_keys=(sk0,),
                hints_on_file={sk0: hints_on_file[sk0]},
            )
        return sk0

    known_all = all(sk in hints_on_file for sk in cands)
    if known_all and hints_on_file:
        return ResolveConfirmAccount(
            candidate_keys=tuple(cands),
            hints_on_file={sk: hints_on_file[sk] for sk in cands},
        )
    return cands


def account_option_label(source_key: str) -> str:
    """Short label for account picker UI."""
    if "hdfc_cc_" in source_key:
        tail = source_key.replace("hdfc_cc_", "")
        return f"HDFC Credit Card (…{tail})"
    if source_key.startswith("hdfc_savings_"):
        tail = source_key.removeprefix("hdfc_savings_")
        return f"HDFC Savings (…{tail})"
    if source_key.startswith("icici_savings_"):
        tail = source_key.removeprefix("icici_savings_")
        return f"ICICI Savings (…{tail})"
    return source_key.replace("_", " ").title()


__all__ = [
    "DetectionResult",
    "PARSER_LABELS",
    "ResolveAccountMismatch",
    "ResolveConfirmAccount",
    "_label_for",
    "detect_transaction_file",
    "detect_holding_file",
    "last4_from_user_pipeline_account_id",
    "parser_class_for_transaction_source_type",
    "matching_user_source_keys",
    "resolve_transaction_source_key",
    "resolve_upload_statement_destination",
    "account_option_label",
]
