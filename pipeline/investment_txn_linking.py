"""
Link ``InvestmentTransaction`` rows to ``Holding`` for XIRR and reporting.

ICICI MF ledger rows carry folio in metadata / notes; holdings store folio encrypted.
Equity rows match on canonical NSE symbol + platform + user.

Use :func:`find_holding_id_for_parsed_txn` during ingest and
:func:`link_unlinked_investment_transactions` to backfill ``holding_id IS NULL``.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from sqlmodel import Session, col, select

from api.models import Holding, InvestmentTransaction
from api.services.price_feed import canonical_nse_symbol
from parsers.holdings.base import ParsedInvestmentTxn
from parsers.holdings.nps import NPS_CANONICAL_HOLDING_NAME
from pipeline.models import AssetClass

logger = logging.getLogger(__name__)

_FOLIO_LINE = re.compile(r"(?im)^folio\s*[:\s]?\s*(\S+)\s*$")
_AMFI_PARENS = re.compile(r"\((\d{4,7})\)")


def _norm_name(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def parse_mf_txn_notes(notes: str | None) -> tuple[str | None, str | None]:
    """Return ``(folio_plain, name_hint)`` from stored MF transaction notes."""
    if not notes or not str(notes).strip():
        return None, None
    text = str(notes).strip()
    folio_m = _FOLIO_LINE.search(text)
    folio = folio_m.group(1) if folio_m else None
    if folio_m:
        name_hint = text[: folio_m.start()].strip()
    else:
        # Notes may be a single line (fund name only) or "name\nFolio x"
        name_hint = text.split("\n")[0].strip()
    return folio, name_hint if name_hint else None


def extract_amfi_scheme_code(text: str | None) -> str | None:
    """Pull AMFI code from ``(123456)`` suffix or anywhere in string."""
    if not text:
        return None
    m = _AMFI_PARENS.search(text)
    return m.group(1) if m else None


def _mf_candidates(session: Session, user_id: str, platform: str) -> list[Holding]:
    """Include inactive MF holdings so historical txns can link after full redemption."""
    return list(
        session.exec(
            select(Holding).where(
                Holding.user_id == user_id,
                Holding.account_platform == platform,
                Holding.asset_class == AssetClass.MUTUAL_FUND.value,
            )
        ).all()
    )


def _match_mf_holding(
    session: Session,
    user_id: str,
    platform: str,
    *,
    folio: str | None,
    name: str | None,
    amfi_code: str | None,
) -> int | None:
    cands = _mf_candidates(session, user_id, platform)
    if not cands:
        return None

    if folio and folio.strip():
        f = folio.strip()
        matched = [
            h
            for h in cands
            if h.folio_number_encrypted and str(h.folio_number_encrypted).strip() == f
        ]
        if len(matched) == 1:
            return matched[0].id
        if len(matched) > 1:
            logger.warning(
                "Multiple MF holdings share folio %r on %s — picking lowest id",
                f,
                platform,
            )
            ids = [m.id for m in matched if m.id is not None]
            return min(ids) if ids else None

    if amfi_code and amfi_code.strip():
        code = amfi_code.strip()
        matched = [h for h in cands if (h.symbol or "").strip() == code]
        if len(matched) == 1:
            return matched[0].id

    n = _norm_name(name)
    if n:
        exact = [h for h in cands if _norm_name(h.name) == n]
        if len(exact) == 1:
            return exact[0].id
        # Single strong substring match (e.g. CSV name vs longer holding label)
        sub = [
            h
            for h in cands
            if n in _norm_name(h.name) or _norm_name(h.name) in n
        ]
        if len(sub) == 1:
            return sub[0].id

    return None


def _pick_nps_holding_id(matched: list[Holding]) -> int | None:
    """Prefer the consolidated NPS row; else a single legacy row; else lowest id."""
    if not matched:
        return None
    canon = [h for h in matched if h.name == NPS_CANONICAL_HOLDING_NAME]
    if len(canon) == 1 and canon[0].id is not None:
        return canon[0].id
    if len(matched) == 1 and matched[0].id is not None:
        return matched[0].id
    if canon:
        ids = [c.id for c in canon if c.id is not None]
        return min(ids) if ids else None
    ids = [h.id for h in matched if h.id is not None]
    return min(ids) if ids else None


def _nps_holdings_matching_pran(hs: list[Holding], pran: str) -> list[Holding]:
    pran = pran.strip()
    return [
        h
        for h in hs
        if (h.folio_number_encrypted and str(h.folio_number_encrypted).strip() == pran)
        or (h.account_identifier_encrypted and str(h.account_identifier_encrypted).strip() == pran)
    ]


def find_holding_id_for_parsed_txn(session: Session, user_id: str, t: ParsedInvestmentTxn) -> int | None:
    """Resolve ``holding_id`` for a parsed ledger row (call after holdings upserted)."""
    platform = (t.account_platform or "").strip()
    if not platform:
        return None

    # Single PPF account per platform (ICICI / SBI export) → attach all ledger rows.
    if platform.endswith(" PPF") or platform == "ICICI PPF":
        hs = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == user_id,
                    Holding.account_platform == platform,
                    Holding.asset_class == AssetClass.PPF.value,
                )
            ).all()
        )
        if len(hs) == 1 and hs[0].id is not None:
            return hs[0].id
        return None

    # NPS: PRAN on folio or account_identifier; multiple legacy E/C/G → pick canonical or min id.
    if platform == "NPS (CRA)":
        meta = t.metadata or {}
        pran = str(meta.get("pran") or "").strip()
        hs = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == user_id,
                    Holding.account_platform == platform,
                    Holding.asset_class == AssetClass.NPS.value,
                )
            ).all()
        )
        if pran:
            matched = _nps_holdings_matching_pran(hs, pran)
            hid = _pick_nps_holding_id(matched)
            if hid is not None:
                return hid
        if len(hs) == 1 and hs[0].id is not None:
            return hs[0].id
        return None

    if platform == "ICICI Direct MF":
        meta = t.metadata or {}
        folio_raw = meta.get("folio")
        folio = str(folio_raw).strip() if folio_raw else None
        name = _norm_name(t.name) or None
        code = (
            (str(meta.get("amfi_scheme_code")).strip() if meta.get("amfi_scheme_code") else None)
            or extract_amfi_scheme_code(t.name or "")
            or (str(t.symbol).strip() if t.symbol else None)
        )
        return _match_mf_holding(
            session, user_id, platform, folio=folio, name=name, amfi_code=code
        )

    if t.symbol and str(t.symbol).strip():
        sym = canonical_nse_symbol(t.symbol)
        h = session.exec(
            select(Holding).where(
                Holding.user_id == user_id,
                Holding.account_platform == platform,
                Holding.symbol == sym,
                col(Holding.asset_class).in_(
                    (
                        AssetClass.EQUITY.value,
                        AssetClass.ESOP.value,
                        AssetClass.GOLD.value,
                        AssetClass.SOVEREIGN_GOLD_BOND.value,
                    )
                ),
            )
        ).first()
        if h and h.id is not None:
            return h.id

    return None


def find_holding_id_for_stored_txn(
    session: Session, user_id: str, txn: InvestmentTransaction
) -> int | None:
    """Resolve ``holding_id`` for an existing DB row (historical backfill)."""
    platform = (txn.account_platform or "").strip()
    if not platform:
        return None

    if platform.endswith(" PPF") or platform == "ICICI PPF":
        hs = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == user_id,
                    Holding.account_platform == platform,
                    Holding.asset_class == AssetClass.PPF.value,
                )
            ).all()
        )
        if len(hs) == 1 and hs[0].id is not None:
            return hs[0].id
        return None

    if platform == "NPS (CRA)":
        pran_m = re.search(r"PRAN\D*(\d{12})\b", txn.notes or "", re.I)
        pran = pran_m.group(1) if pran_m else ""
        hs = list(
            session.exec(
                select(Holding).where(
                    Holding.user_id == user_id,
                    Holding.account_platform == platform,
                    Holding.asset_class == AssetClass.NPS.value,
                )
            ).all()
        )
        if pran:
            matched = _nps_holdings_matching_pran(hs, pran)
            hid = _pick_nps_holding_id(matched)
            if hid is not None:
                return hid
        if len(hs) == 1 and hs[0].id is not None:
            return hs[0].id
        return None

    if platform == "ICICI Direct MF":
        folio_hint, name_hint = parse_mf_txn_notes(txn.notes)
        code = (
            extract_amfi_scheme_code(txn.notes or "")
            or (str(txn.symbol).strip() if txn.symbol else None)
        )
        return _match_mf_holding(
            session,
            user_id,
            platform,
            folio=folio_hint,
            name=name_hint,
            amfi_code=code,
        )

    if txn.symbol and str(txn.symbol).strip():
        sym = canonical_nse_symbol(txn.symbol)
        h = session.exec(
            select(Holding).where(
                Holding.user_id == user_id,
                Holding.account_platform == platform,
                Holding.symbol == sym,
            )
        ).first()
        if h and h.id is not None:
            return h.id

    return None


def _distinct_holding_user_ids(session: Session) -> list[str]:
    # ``Holding.user_id`` is required (non-optional str); exec returns one str per row.
    rows = session.exec(select(Holding.user_id).distinct()).all()
    return sorted({str(r) for r in rows})


def link_unlinked_investment_transactions(
    session: Session,
    *,
    user_ids: Iterable[str] | None = None,
) -> dict[str, int]:
    """
    Set ``holding_id`` where it is NULL.

    Tries each ``user_id`` in ``user_ids`` (default: every distinct owner in ``holdings``).
    """
    orphans = list(
        session.exec(
            select(InvestmentTransaction).where(
                col(InvestmentTransaction.holding_id).is_(None)
            )
        ).all()
    )
    if not orphans:
        return {"examined": 0, "linked": 0, "still_orphan": 0, "ambiguous": 0}

    uids = list(user_ids) if user_ids is not None else _distinct_holding_user_ids(session)
    if not uids:
        return {
            "examined": len(orphans),
            "linked": 0,
            "still_orphan": len(orphans),
            "ambiguous": 0,
        }

    linked = 0
    ambiguous = 0
    for t in orphans:
        hits: list[int] = []
        for uid in uids:
            hid = find_holding_id_for_stored_txn(session, uid, t)
            if hid is not None:
                hits.append(hid)
        unique = list(dict.fromkeys(hits))
        if len(unique) == 1:
            t.holding_id = unique[0]
            session.add(t)
            linked += 1
        elif len(unique) > 1:
            ambiguous += 1
            logger.warning(
                "Ambiguous holding match for investment_transaction id=%s platforms=%r — skipped",
                t.id,
                t.account_platform,
            )

    session.flush()
    still = len(orphans) - linked
    return {
        "examined": len(orphans),
        "linked": linked,
        "still_orphan": still,
        "ambiguous": ambiguous,
    }
