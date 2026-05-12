"""
Holding / investment ingest (Phase A.2.7).

Parse → validate → encrypt PII → upsert ``Holding`` / insert deduped
``InvestmentTransaction`` / insert ``Liability``.

Workflow (from plan):
  1. ``APP_ENV=test`` → ``data/arth_test.db`` first
  2. Run ingest + ``scripts/validate_investment_crossref.py``
  3. Backup ``data/arth_main.db`` before first prod ingest
"""

from __future__ import annotations

import argparse
import datetime
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from api.database import get_engine, init_db
from api.services.email_import_flow_log import EmailImportFlowLog
from api.services.holding_enrichment import enrich_single_equity_classification
from api.services.holdings_sync import ensure_holding_for_transaction, sync_holding_from_transactions
from api.models import Holding, HoldingValueSnapshot, InvestmentTransaction, Liability
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY, parse_bike_loan_txt, parse_term_insurance_pdf
from parsers.holdings.base import ParsedHolding, ParsedInvestmentTxn, ParsedLiability
from parsers.holdings.nps import NPS_CANONICAL_HOLDING_NAME, PLATFORM as NPS_CRA_PLATFORM
from pipeline.investment_txn_linking import (
    find_holding_id_for_parsed_txn,
    link_unlinked_investment_transactions,
    parse_mf_txn_notes,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod
from pipeline.isin_nse_resolver import is_curated_ignored_security

logger = logging.getLogger(__name__)

_VALID_ASSET = {e.value for e in AssetClass}
_VALID_VALUATION = {e.value for e in ValuationMethod}
_VALID_LIQ = {e.value for e in LiquidityClass}
_VALID_INV_TXN = {e.value for e in InvestmentTxnType}


def _default_user_id() -> str:
    uid = os.environ.get("ARTH_USER_ID", "").strip()
    if not uid:
        raise RuntimeError(
            "ARTH_USER_ID environment variable must be set (your Arth username, "
            "same string as dashboard login)."
        )
    return uid


def validate_parsed_holding(ph: ParsedHolding) -> list[str]:
    """Return human-readable issues (empty list => OK)."""
    errs: list[str] = []
    if not ph.name or not ph.name.strip():
        errs.append("holding name is empty")
    if ph.asset_class not in _VALID_ASSET:
        errs.append(f"invalid asset_class {ph.asset_class!r}")
    if ph.valuation_method not in _VALID_VALUATION:
        errs.append(f"invalid valuation_method {ph.valuation_method!r}")
    if ph.liquidity_class not in _VALID_LIQ:
        errs.append(f"invalid liquidity_class {ph.liquidity_class!r}")
    return errs


def validate_parsed_inv_txn(t: ParsedInvestmentTxn) -> list[str]:
    errs: list[str] = []
    if t.txn_type not in _VALID_INV_TXN:
        errs.append(f"invalid txn_type {t.txn_type!r}")
    if t.quantity <= 0:
        errs.append("quantity must be > 0")
    if t.total_amount < 0:
        errs.append("total_amount cannot be negative")
    return errs


def find_existing_holding(session: Session, user_id: str, ph: ParsedHolding) -> Holding | None:
    """Match on (user, platform, symbol) or (user, platform, name) + optional folio."""
    if ph.symbol:
        row = session.exec(
            select(Holding).where(
                Holding.user_id == user_id,
                Holding.account_platform == ph.account_platform,
                Holding.symbol == ph.symbol,
            )
        ).first()
        if row:
            return row

    # One consolidated NPS row per PRAN — upgrades legacy E/C/G rows on re-import.
    if (
        ph.asset_class == AssetClass.NPS.value
        and ph.account_platform == NPS_CRA_PLATFORM
        and ph.folio_number
        and str(ph.folio_number).strip()
    ):
        pran = str(ph.folio_number).strip()
        stmt = select(Holding).where(
            Holding.user_id == user_id,
            Holding.account_platform == ph.account_platform,
            Holding.asset_class == AssetClass.NPS.value,
            Holding.is_active == True,  # noqa: E712
        )
        cands = list(session.exec(stmt).all())

        def _pran_match(h: Holding) -> bool:
            fn = str(h.folio_number_encrypted).strip() if h.folio_number_encrypted else ""
            ai = str(h.account_identifier_encrypted).strip() if h.account_identifier_encrypted else ""
            return fn == pran or ai == pran

        hits = [h for h in cands if _pran_match(h)]
        if hits:
            pref = [h for h in hits if h.name == NPS_CANONICAL_HOLDING_NAME]
            return pref[0] if pref else hits[0]

    stmt = select(Holding).where(
        Holding.user_id == user_id,
        Holding.account_platform == ph.account_platform,
        Holding.name == ph.name,
    )
    candidates = list(session.exec(stmt).all())
    if not candidates:
        return None
    if ph.folio_number:
        for h in candidates:
            fn = h.folio_number_encrypted
            if fn and ph.folio_number and fn == ph.folio_number:
                return h
    return candidates[0]


def _apply_parsed_holding_to_row(h: Holding, ph: ParsedHolding, user_id: str) -> None:
    h.user_id = user_id
    h.symbol = ph.symbol
    h.name = ph.name
    h.quantity = ph.quantity
    h.asset_class = ph.asset_class
    h.account_platform = ph.account_platform
    h.valuation_method = ph.valuation_method
    h.current_value = ph.current_value
    h.liquidity_class = ph.liquidity_class
    h.average_cost_per_unit = ph.average_cost_per_unit
    h.current_price_per_unit = ph.current_price_per_unit
    h.principal_amount = ph.principal_amount
    h.interest_rate = ph.interest_rate
    h.maturity_date = ph.maturity_date
    h.compounding_frequency = ph.compounding_frequency
    h.face_value = ph.face_value
    h.coupon_rate = ph.coupon_rate
    h.coupon_frequency = ph.coupon_frequency
    h.fund_type = ph.fund_type
    h.is_active = ph.is_active
    if ph.notes:
        h.notes = ph.notes
    if ph.folio_number:
        h.folio_number_encrypted = ph.folio_number
    if ph.metadata.get("pran"):
        h.account_identifier_encrypted = str(ph.metadata["pran"])
    elif ph.isin and ph.asset_class == AssetClass.EQUITY.value:
        extra = f"ISIN {ph.isin}"
        h.notes = f"{h.notes or ''}\n{extra}".strip()


def _upsert_holding_value_snapshot(
    session: Session,
    *,
    holding_id: int,
    snapshot_date: str | None,
    value: float | None,
    source_file: str | None,
) -> None:
    if not snapshot_date or value is None:
        return
    try:
        d = datetime.date.fromisoformat(snapshot_date[:10])
    except ValueError:
        return
    row = session.exec(
        select(HoldingValueSnapshot).where(
            HoldingValueSnapshot.holding_id == holding_id,
            HoldingValueSnapshot.snapshot_date == d,
        )
    ).first()
    if row is None:
        row = HoldingValueSnapshot(
            holding_id=holding_id,
            snapshot_date=d,
            value=float(value),
            source="statement",
            notes=source_file,
        )
    else:
        row.value = float(value)
        row.notes = source_file
    session.add(row)


# --- Investment dedupe (email vs CSV / statement upload) -----------------
# ``price_per_unit`` is intentionally *not* part of the key — parsers round differently
# for the same trade (e.g. 313.58 vs 313.584106) which must still count as one row.
_INV_QTY_REL_TOL = 1e-5
_INV_QTY_ABS_TOL = 1e-4
_INV_AMT_REL_TOL = 1e-4
_INV_AMT_ABS_TOL = 1.0  # rupees — covers tiny NAV / fee drift between mail vs statement

# Strip boilerplate so "ELSS … Growth" vs "ELSS … Regular Plan - Growth" still match.
_MF_DEDUPE_NOISE_TOKENS = frozenset(
    {
        "regular",
        "direct",
        "plan",
        "growth",
        "idcw",
        "dividend",
        "mutual",
        "fund",
        "ltd",
        "limited",
        "managers",
        "mf",
    }
)


def _inv_norm_symbol(sym: str | None) -> str | None:
    s = (sym or "").strip()
    return s.upper() if s else None


def _inv_qty_total_match(parsed_qty: float, parsed_amt: float, row_qty: float, row_amt: float) -> bool:
    return math.isclose(
        float(parsed_qty),
        float(row_qty),
        rel_tol=_INV_QTY_REL_TOL,
        abs_tol=_INV_QTY_ABS_TOL,
    ) and math.isclose(
        float(parsed_amt),
        float(row_amt),
        rel_tol=_INV_AMT_REL_TOL,
        abs_tol=_INV_AMT_ABS_TOL,
    )


def _folio_from_parsed_txn(t: ParsedInvestmentTxn) -> str | None:
    """Folio from CSV metadata or ``Folio …`` line in name/notes (same idea as stored MF notes)."""
    meta = t.metadata or {}
    raw = meta.get("folio")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    blob = "\n".join(x for x in (t.name, t.notes) if x and str(x).strip())
    folio, _hint = parse_mf_txn_notes(blob)
    return folio.strip() if folio and str(folio).strip() else None


def _mf_scheme_token_bag(text: str) -> tuple[str, ...]:
    """Sorted unique tokens after lowercasing and dropping common scheme boilerplate."""
    s = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    toks = sorted(
        {
            w
            for w in s.split()
            if len(w) > 1 and w not in _MF_DEDUPE_NOISE_TOKENS
        }
    )
    return tuple(toks)


def _parsed_mf_identity_blob(t: ParsedInvestmentTxn) -> str:
    """Combined name + notes for MF fingerprinting (folio line stripped downstream)."""
    return "\n".join(x for x in (t.name, t.notes) if x and str(x).strip())


def _mf_folio_or_fingerprint_match(t: ParsedInvestmentTxn, r: InvestmentTransaction) -> bool:
    """Same MF line from Gmail vs CSV: folio when both sides have it; else scheme token-bag (+ folio substring check)."""
    fp = _folio_from_parsed_txn(t)
    fr, db_hint = parse_mf_txn_notes(r.notes)
    fr = fr.strip() if fr and str(fr).strip() else None

    if fp and fr:
        return fp == fr

    _, p_hint = parse_mf_txn_notes(_parsed_mf_identity_blob(t))
    p_key = (p_hint or _parsed_mf_identity_blob(t)).strip()
    d_key = (db_hint or (r.notes or "")).strip()
    if _mf_scheme_token_bag(p_key) != _mf_scheme_token_bag(d_key):
        return False
    if fp and not fr:
        return fp in re.sub(r"\s+", "", d_key)
    if fr and not fp:
        return fr in re.sub(r"\s+", "", p_key)
    return True


def _is_mf_duplicate_style(t: ParsedInvestmentTxn, parsed_sym: str | None, db_sym: str | None) -> bool:
    plat = (t.account_platform or "").upper()
    if " MF" in plat or plat.endswith("MF") or "MUTUAL" in plat:
        return True
    if "PPF" in plat:
        return True
    if parsed_sym and parsed_sym.isdigit():
        return True
    if db_sym and db_sym.isdigit():
        return True
    return False


def _parsed_isin_upper(t: ParsedInvestmentTxn) -> str | None:
    """ISIN from parser metadata (ICICI equity statement PDF legs carry this when NSE symbol is blank)."""
    raw = (t.metadata or {}).get("isin")
    if raw is None or not str(raw).strip():
        return None
    s = str(raw).strip().upper()
    # Indian ISINs: equities ``INE*``, ETFs / funds ``INF*``, etc. (ISO 6166, 12 chars).
    if len(s) == 12 and s.startswith("IN") and s[2].isalpha() and s[2].isascii():
        return s
    return None


def _is_icici_direct_equity_blank_symbol(t: ParsedInvestmentTxn, ps: str | None, rs: str | None) -> bool:
    """ICICI Direct equity rows where the NSE ticker never resolved — overlap across statement PDFs."""
    if ps or rs:
        return False
    return (t.account_platform or "").strip().upper() == "ICICI DIRECT"


def _equity_icici_blank_symbol_identity_match(t: ParsedInvestmentTxn, r: InvestmentTransaction) -> bool:
    """Match statement orphans: ISIN line in DB notes, else same company-name token bag as MF logic."""
    p_isin = _parsed_isin_upper(t)
    db_blob = (r.notes or "").strip().upper()
    if p_isin and p_isin in db_blob.replace(" ", ""):
        return True
    p_blob = "\n".join(x for x in (t.name, t.notes) if x and str(x).strip())
    d_blob = (r.notes or "").strip()
    bag_p = _mf_scheme_token_bag(p_blob)
    bag_d = _mf_scheme_token_bag(d_blob)
    if len(bag_p) < 2 or len(bag_d) < 2:
        return False
    return bag_p == bag_d


def _investment_txn_row_matches_parsed(t: ParsedInvestmentTxn, r: InvestmentTransaction) -> bool:
    """True when ``r`` is the same economic event as parsed ``t`` (skip insert, log duplicate)."""
    if not _inv_qty_total_match(t.quantity, t.total_amount, r.quantity, r.total_amount):
        return False

    ps = _inv_norm_symbol(t.symbol)
    rs = _inv_norm_symbol(r.symbol)

    if ps and rs and ps == rs:
        return True

    if _is_mf_duplicate_style(t, ps, rs):
        if ps and rs and ps != rs:
            return False
        return _mf_folio_or_fingerprint_match(t, r)

    if _is_icici_direct_equity_blank_symbol(t, ps, rs):
        return _equity_icici_blank_symbol_identity_match(t, r)

    return False


def investment_txn_exists(session: Session, t: ParsedInvestmentTxn) -> bool:
    """Return True if an equivalent ledger row already exists (any ``source_type``).

    Dedupe key (aggressive, email vs statement upload):
      * Same ``txn_date``, ``account_platform``, ``txn_type``
      * Quantity + ``total_amount`` close (tolerances — not ``price_per_unit``)
      * **Equity / symbol rows:** same normalized ``symbol``
      * **ICICI Direct equity, blank ticker:** same **ISIN** (from parsed metadata vs ``notes``) or
        same **name token fingerprint** (overlapping ICICI PDFs often repeat the same line with no
        NSE symbol resolved)
      * **MF-style (platform name, AMFI digit symbol, or blank symbol):** same folio when both
        sides have it; otherwise same **scheme fingerprint** (token bag after stripping boilerplate)

    When this returns True, :func:`ingest_investment_transactions` skips the insert — no merge /
    reconciliation of the existing row.
    """
    stmt = select(InvestmentTransaction).where(
        InvestmentTransaction.txn_date == t.txn_date,
        InvestmentTransaction.account_platform == t.account_platform,
        InvestmentTransaction.txn_type == t.txn_type,
    )
    rows = list(session.exec(stmt).all())
    return any(_investment_txn_row_matches_parsed(t, r) for r in rows)


def ingest_holdings(
    session: Session,
    holdings: list[ParsedHolding],
    *,
    user_id: str,
    dry_run: bool = False,
) -> dict[str, int]:
    inserted = 0
    updated = 0
    errors = 0
    for ph in holdings:
        bad = validate_parsed_holding(ph)
        if bad:
            logger.warning("Skip holding %r: %s", ph.name, bad)
            errors += 1
            continue
        existing = find_existing_holding(session, user_id, ph)
        if dry_run:
            inserted += 0 if existing else 1
            updated += 1 if existing else 0
            continue
        is_new_holding = existing is None
        if existing:
            _apply_parsed_holding_to_row(existing, ph, user_id)
            session.add(existing)
            updated += 1
            target_row = existing
        else:
            h = Holding(
                symbol=ph.symbol,
                name=ph.name,
                quantity=ph.quantity,
                asset_class=ph.asset_class,
                account_platform=ph.account_platform,
                valuation_method=ph.valuation_method,
                current_value=ph.current_value,
                liquidity_class=ph.liquidity_class,
                user_id=user_id,
                is_active=ph.is_active,
                notes=ph.notes,
                average_cost_per_unit=ph.average_cost_per_unit,
                current_price_per_unit=ph.current_price_per_unit,
                principal_amount=ph.principal_amount,
                interest_rate=ph.interest_rate,
                maturity_date=ph.maturity_date,
                compounding_frequency=ph.compounding_frequency,
                face_value=ph.face_value,
                coupon_rate=ph.coupon_rate,
                coupon_frequency=ph.coupon_frequency,
                fund_type=ph.fund_type,
            )
            if ph.folio_number:
                h.folio_number_encrypted = ph.folio_number
            if ph.metadata.get("pran"):
                h.account_identifier_encrypted = str(ph.metadata["pran"])
            elif ph.isin and ph.asset_class == AssetClass.EQUITY.value:
                h.notes = f"{ph.notes or ''}\nISIN {ph.isin}".strip()
            session.add(h)
            inserted += 1
            target_row = h
        # Commit so the next row in this loop can SELECT what we just inserted,
        # and so the write transaction is closed before any NSE enrichment calls.
        if not dry_run:
            session.commit()
            if target_row.id is not None:
                _upsert_holding_value_snapshot(
                    session,
                    holding_id=target_row.id,
                    snapshot_date=str(ph.metadata.get("value_as_of_date") or "").strip() or None,
                    value=(
                        float(ph.metadata["snapshot_value"])
                        if ph.metadata.get("snapshot_value") is not None
                        else None
                    ),
                    source_file=str(ph.metadata.get("source_file") or "").strip() or None,
                )
                if is_new_holding:
                    enrich_single_equity_classification(session, target_row)
    if not dry_run:
        session.commit()
    return {"inserted": inserted, "updated": updated, "errors": errors}


def ingest_investment_transactions(
    session: Session,
    txns: list[ParsedInvestmentTxn],
    *,
    user_id: str | None = None,
    dry_run: bool = False,
    source_type: str | None = None,
    gmail_message_id: str | None = None,
    import_flow_log: EmailImportFlowLog | None = None,
    email_presumes_reviewed: bool = False,
) -> dict[str, Any]:
    """Insert deduped ledger rows. When ``user_id`` is set, resolve ``holding_id`` (MF + equity).

    Duplicate detection is **aggressive** (same economic line from Gmail vs CSV upload, or two
    overlapping ICICI equity statement PDFs): same date/platform/type, quantity + total within
    tolerances, symbol or MF folio / scheme fingerprint or **ICICI blank-symbol equity** identity
    — **not** exact ``price_per_unit`` equality. When a duplicate exists, the row is **skipped**
    (no merge / reconciliation of the existing row).

    When ``source_type=\"email\"`` (Gmail scraper / statement PDF attachment path), new rows
    default to ``is_reviewed=False`` so they surface on the investment review queue — same rule as
    :func:`pipeline.db_writer.write_to_db` for bank transactions. Pass ``email_presumes_reviewed=True``
    for historical Gmail sweeps or onboarding mail import so rows enter pre-reviewed.

    When ``import_flow_log`` is set (onboarding email import HTTP path), append diagnostics to
    ``data/logs/email-import.log`` alongside bank-transaction events from :mod:`scraper.orchestrator`.
    """
    inserted = 0
    skipped = 0
    errors = 0
    linked_inline = 0
    uid = user_id.strip() if user_id and str(user_id).strip() else None
    inserted_rows: list[InvestmentTransaction] = []
    # Cap per-message samples so one huge statement PDF cannot flood the log file.
    _max_sample = 8
    _n_validate_logged = 0
    _n_dup_logged = 0
    _n_ignored_logged = 0

    if import_flow_log:
        import_flow_log.write(
            "inv_ingest_start",
            f"n={len(txns)} user_id={uid or '-'} source_type={source_type or '-'} gmail_id={gmail_message_id or '-'} dry_run={dry_run}",
        )

    for t in txns:
        bad = validate_parsed_inv_txn(t)
        if bad:
            logger.warning("Skip inv txn %s %s: %s", t.txn_date, t.txn_type, bad)
            errors += 1
            if import_flow_log and _n_validate_logged < _max_sample:
                import_flow_log.write(
                    "inv_skip_validate",
                    f"date={t.txn_date} type={t.txn_type} symbol={t.symbol!r} reason={bad}",
                )
                _n_validate_logged += 1
            continue
        meta_pre = t.metadata or {}
        isin_chk = (meta_pre.get("isin") or "").strip().upper()
        sym_chk = (t.symbol or "").strip().upper()
        if is_curated_ignored_security(symbol=t.symbol, isin=meta_pre.get("isin")):
            skipped += 1
            logger.info(
                "Skip inv txn (ignored security): date=%s type=%s symbol=%r isin=%r",
                t.txn_date,
                t.txn_type,
                t.symbol,
                isin_chk or None,
            )
            if import_flow_log and _n_ignored_logged < _max_sample:
                import_flow_log.write(
                    "inv_skip_ignored_security",
                    f"date={t.txn_date} type={t.txn_type} symbol={sym_chk!r} isin={isin_chk!r}",
                )
                _n_ignored_logged += 1
            continue
        if investment_txn_exists(session, t):
            skipped += 1
            if import_flow_log and _n_dup_logged < _max_sample:
                import_flow_log.write(
                    "inv_skip_duplicate",
                    f"date={t.txn_date} type={t.txn_type} symbol={t.symbol!r} amt={t.total_amount}",
                )
                _n_dup_logged += 1
            continue
        if dry_run:
            inserted += 1
            continue
        notes_parts = []
        if t.name:
            notes_parts.append(t.name)
        if t.notes:
            notes_parts.append(t.notes)
        meta = t.metadata or {}
        isin_meta = (meta.get("isin") or "").strip().upper()
        if len(isin_meta) == 12 and isin_meta.startswith("IN"):
            joined = "\n".join(notes_parts).upper()
            if isin_meta not in joined.replace(" ", ""):
                notes_parts.append(f"ISIN {isin_meta}")
        hid: int | None = None
        if uid:
            hid = find_holding_id_for_parsed_txn(session, uid, t)
            if hid is not None:
                linked_inline += 1
        # File/CLI imports omit source_type → reviewed. Email path: live scrape leaves rows
        # unreviewed unless ``email_presumes_reviewed`` (historical / onboarding mail).
        if source_type == "email":
            is_reviewed_default = bool(email_presumes_reviewed)
        else:
            is_reviewed_default = True
        it = InvestmentTransaction(
            txn_date=t.txn_date,
            symbol=t.symbol,
            txn_type=t.txn_type,
            quantity=t.quantity,
            price_per_unit=t.price_per_unit,
            total_amount=t.total_amount,
            account_platform=t.account_platform,
            holding_id=hid,
            notes="\n".join(notes_parts) if notes_parts else None,
            is_reviewed=is_reviewed_default,
            source_type=source_type,
            gmail_message_id=gmail_message_id,
        )
        session.add(it)
        inserted += 1
        # Commit immediately to get it.id and close the write transaction before
        # the post-loop linking and holding-sync work below.
        session.commit()
        inserted_rows.append(it)

    orphan_backfill = {"examined": 0, "linked": 0, "still_orphan": 0, "ambiguous": 0}
    if uid and not dry_run:
        orphan_backfill = link_unlinked_investment_transactions(
            session, user_ids=[uid]
        )

    holdings_synced = 0
    if uid and not dry_run and inserted_rows:
        for it in inserted_rows:
            if it.holding_id is None:
                ensure_holding_for_transaction(session, it, user_id=uid)
        to_sync: set[int] = set()
        for it in inserted_rows:
            if it.holding_id is not None:
                to_sync.add(it.holding_id)
        for hid in sorted(to_sync):
            sync_holding_from_transactions(session, hid)
            holdings_synced += 1

    if not dry_run:
        session.commit()

    if import_flow_log:
        import_flow_log.write(
            "inv_ingest_done",
            f"inserted={inserted} skipped_dup={skipped} errors={errors} linked_inline={linked_inline} "
            f"orphans_linked={orphan_backfill.get('linked', 0)} orphans_examined={orphan_backfill.get('examined', 0)} "
            f"orphans_ambiguous={orphan_backfill.get('ambiguous', 0)} holdings_synced={holdings_synced}",
        )

    return {
        "inserted": inserted,
        "skipped_duplicate": skipped,
        "errors": errors,
        "linked_inline": linked_inline,
        "orphans_linked": int(orphan_backfill.get("linked", 0)),
        "orphans_examined": int(orphan_backfill.get("examined", 0)),
        "orphans_ambiguous": int(orphan_backfill.get("ambiguous", 0)),
        "holdings_synced": holdings_synced,
    }


def ingest_liabilities(
    session: Session,
    rows: list[ParsedLiability],
    *,
    user_id: str,
    dry_run: bool = False,
) -> dict[str, int]:
    n = 0
    for pl in rows:
        if dry_run:
            n += 1
            continue
        li = Liability(
            name=pl.name,
            liability_type=pl.liability_type,
            principal_outstanding=pl.principal_outstanding,
            interest_rate=pl.interest_rate,
            emi_amount=pl.emi_amount,
            tenure_remaining_months=pl.tenure_remaining_months,
            emi_start_date=pl.emi_start_date,
            emi_end_date=pl.emi_end_date,
            user_id=user_id,
            notes=pl.notes,
        )
        session.add(li)
        n += 1
    if not dry_run:
        session.commit()
    return {"inserted": n}


def run_parser_source(source_key: str, input_path: Path) -> tuple[list[ParsedHolding], list[ParsedInvestmentTxn]]:
    if source_key not in HOLDING_PARSER_REGISTRY:
        raise SystemExit(f"Unknown source {source_key!r}. Choose from: {sorted(HOLDING_PARSER_REGISTRY)}")
    cls = HOLDING_PARSER_REGISTRY[source_key]
    parser = cls()
    return parser.parse_path(input_path)


def main(argv: list[str] | None = None) -> None:
    from pipeline.logging_config import setup_logging

    setup_logging()
    p = argparse.ArgumentParser(description="Ingest holdings / investment transactions / liabilities.")
    p.add_argument(
        "--source",
        required=True,
        help="icici_direct_mf | icici_ppf | nps | liability_bike | liability_term_insurance",
    )
    p.add_argument("--input", required=True, type=Path, help="File or directory path")
    p.add_argument(
        "--user-id",
        default=None,
        help="Arth username for DB rows; defaults to ARTH_USER_ID (required if unset)",
    )
    p.add_argument("--dry-run", action="store_true", help="Parse + validate only; no DB writes")
    p.add_argument("--skip-txns", action="store_true", help="Holdings / liabilities only")
    p.add_argument("--skip-holdings", action="store_true", help="Investment txns / liabilities only")
    args = p.parse_args(argv)

    user_id = (args.user_id or _default_user_id()).strip()
    init_db()

    source = args.source.strip()
    path: Path = args.input.expanduser().resolve()

    if source == "liability_bike":
        if not path.is_file():
            sys.exit("--input must be a .txt file for liability_bike")
        rows = parse_bike_loan_txt(path)
        with Session(get_engine()) as session:
            stats = ingest_liabilities(session, rows, user_id=user_id, dry_run=args.dry_run)
        logger.info("Liability bike ingest: %s", stats)
        return

    if source == "liability_term_insurance":
        if not path.is_file():
            sys.exit("--input must be a .pdf file for liability_term_insurance")
        rows = parse_term_insurance_pdf(path)
        with Session(get_engine()) as session:
            stats = ingest_liabilities(session, rows, user_id=user_id, dry_run=args.dry_run)
        logger.info("Liability term insurance ingest: %s", stats)
        return

    holdings, txns = run_parser_source(source, path)
    logger.info("Parsed holdings=%d investment_txns=%d", len(holdings), len(txns))

    with Session(get_engine()) as session:
        hstats = {"inserted": 0, "updated": 0, "errors": 0}
        tstats = {"inserted": 0, "skipped_duplicate": 0, "errors": 0}
        if not args.skip_holdings:
            hstats = ingest_holdings(session, holdings, user_id=user_id, dry_run=args.dry_run)
        if not args.skip_txns:
            tstats = ingest_investment_transactions(
                session,
                txns,
                user_id=user_id,
                dry_run=args.dry_run,
            )
    logger.info("Holdings: %s  Investment txns: %s", hstats, tstats)


if __name__ == "__main__":
    main()
