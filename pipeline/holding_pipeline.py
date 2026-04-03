"""
Holding / investment ingest (Phase A.2.7).

Parse → validate → encrypt PII → upsert ``Holding`` / insert deduped
``InvestmentTransaction`` / insert ``Liability``.

Workflow (from plan):
  1. ``APP_ENV=test`` → ``data/arth_test.db`` first
  2. Run ingest + ``scripts/validate_investment_crossref.py``
  3. Backup ``data/arth.db`` before first prod ingest
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from pathlib import Path

from sqlmodel import Session, select

from api.database import get_engine, init_db
from api.models import Holding, HoldingValueSnapshot, InvestmentTransaction, Liability
from pipeline.holding_parsers import HOLDING_PARSER_REGISTRY, parse_bike_loan_txt, parse_term_insurance_pdf
from pipeline.holding_parsers.base import ParsedHolding, ParsedInvestmentTxn, ParsedLiability
from pipeline.holding_parsers.nps import NPS_CANONICAL_HOLDING_NAME, PLATFORM as NPS_CRA_PLATFORM
from pipeline.investment_txn_linking import (
    find_holding_id_for_parsed_txn,
    link_unlinked_investment_transactions,
)
from pipeline.models import AssetClass, InvestmentTxnType, LiquidityClass, ValuationMethod

logger = logging.getLogger(__name__)

_VALID_ASSET = {e.value for e in AssetClass}
_VALID_VALUATION = {e.value for e in ValuationMethod}
_VALID_LIQ = {e.value for e in LiquidityClass}
_VALID_INV_TXN = {e.value for e in InvestmentTxnType}


def _default_user_id() -> str:
    return (os.environ.get("ARTH_USER_ID") or "sashank").strip() or "sashank"


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


def investment_txn_exists(session: Session, t: ParsedInvestmentTxn) -> bool:
    """Dedup: date + platform + type + amounts + symbol (or notes when symbol is null)."""
    stmt = select(InvestmentTransaction).where(
        InvestmentTransaction.txn_date == t.txn_date,
        InvestmentTransaction.account_platform == t.account_platform,
        InvestmentTransaction.txn_type == t.txn_type,
        InvestmentTransaction.quantity == t.quantity,
        InvestmentTransaction.total_amount == t.total_amount,
        InvestmentTransaction.price_per_unit == t.price_per_unit,
    )
    if t.symbol:
        stmt = stmt.where(InvestmentTransaction.symbol == t.symbol)
    rows = list(session.exec(stmt).all())
    if not rows:
        return False
    if not t.symbol:
        n = (t.name or "").strip()
        for r in rows:
            rn = (r.notes or "").strip()
            if n and (n in rn or rn.endswith(n)):
                return True
        return False
    return True


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
        # So the next row in this batch can ``SELECT`` what we just attached (NPS: same scheme × FY files).
        if not dry_run:
            session.flush()
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
) -> dict[str, int]:
    """Insert deduped ledger rows. When ``user_id`` is set, resolve ``holding_id`` (MF + equity).

    When ``source_type=\"email\"`` (Gmail scraper / statement PDF attachment path), new rows
    get ``is_reviewed=False`` so they surface on the investment review queue — same rule as
    :func:`pipeline.db_writer.write_to_db` for bank transactions.
    """
    inserted = 0
    skipped = 0
    errors = 0
    linked_inline = 0
    uid = user_id.strip() if user_id and str(user_id).strip() else None

    for t in txns:
        bad = validate_parsed_inv_txn(t)
        if bad:
            logger.warning("Skip inv txn %s %s: %s", t.txn_date, t.txn_type, bad)
            errors += 1
            continue
        if investment_txn_exists(session, t):
            skipped += 1
            continue
        if dry_run:
            inserted += 1
            continue
        notes_parts = []
        if t.name:
            notes_parts.append(t.name)
        if t.notes:
            notes_parts.append(t.notes)
        hid: int | None = None
        if uid:
            hid = find_holding_id_for_parsed_txn(session, uid, t)
            if hid is not None:
                linked_inline += 1
        # File/CLI imports omit source_type → reviewed. Email path passes source_type="email".
        is_reviewed_default = source_type != "email"
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
        session.flush()

    orphan_backfill = {"examined": 0, "linked": 0, "still_orphan": 0, "ambiguous": 0}
    if uid and not dry_run:
        orphan_backfill = link_unlinked_investment_transactions(
            session, user_ids=[uid]
        )

    if not dry_run:
        session.commit()
    return {
        "inserted": inserted,
        "skipped_duplicate": skipped,
        "errors": errors,
        "linked_inline": linked_inline,
        "orphans_linked": int(orphan_backfill.get("linked", 0)),
        "orphans_examined": int(orphan_backfill.get("examined", 0)),
        "orphans_ambiguous": int(orphan_backfill.get("ambiguous", 0)),
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
        help="icici_direct_equity | icici_direct_mf | icici_ppf | nps | liability_bike | liability_term_insurance",
    )
    p.add_argument("--input", required=True, type=Path, help="File or directory path")
    p.add_argument("--user-id", default=None, help="Defaults to ARTH_USER_ID or sashank")
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
