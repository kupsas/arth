"""
Build per-user ``ScraperBankSender`` + ``ScraperAccountMapping`` rows from Gmail discovery.

Onboarding runs :func:`discover_sources_iter` first (cheap ``messages.list`` ID probes).  This module
fills the SQLite tables that :func:`scraper.config_loader.get_bank_senders_config` reads
by sampling a few full messages per discovered sender and inferring last-4 digits from
subjects/HTML (heuristic — same idea as the parsers, without duplicating every bank regex).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlmodel import Session, select

from api.models import ScraperAccountMapping, ScraperBankSender, Transaction, UserPipelineSource
from api.services.family_member_utils import self_member_id
from scraper.config import BANK_SENDERS
from scraper.email_router import _normalise_sender
from scraper.gmail_client import GmailClient

logger = logging.getLogger(__name__)

# Statement parsers whose account last-4 lives only inside the encrypted PDF — persist the
# sender during onboarding even when email text has no digits; mapping is filled at parse time.
_PDF_ONLY_SELF_MAPPING_PARSERS = frozenset({"sbi_statement"})


def sync_user_pipeline_sources_from_scraper_mappings(session: Session, user_id: str) -> int:
    """Create missing ``UserPipelineSource`` rows from Gmail ``ScraperAccountMapping``.

    **Why this exists:** Onboarding ``persist-sources`` only upserted scraper tables, while
    ``POST /pipeline/upload`` and the CLI resolve the destination account via
    :func:`pipeline.config.get_source_configs`, which reads **only** ``UserPipelineSource``.
    Users could already have HDFC/ICICI transactions from email ingest yet still see
    ``outcome=no_source`` on manual uploads — this bridges that gap.

    **Safety:** Inserts are skipped when a ``(user_id, source_key)`` row already exists; we
    never overwrite ``account_id`` or ``statement_folder`` you set by hand or via migrate.

    Returns:
        Count of new ``UserPipelineSource`` rows staged on *session* (caller commits).
    """
    from parsers.uploads import PARSER_REGISTRY

    uid = (user_id or "").strip()
    if not uid:
        return 0

    rows = session.exec(
        select(ScraperAccountMapping).where(ScraperAccountMapping.user_id == uid)
    ).all()
    account_ids_by_sk: dict[str, set[str]] = {}
    for row in rows:
        sk = (row.source_key or "").strip()
        if not sk or sk not in PARSER_REGISTRY:
            continue
        aid = (row.account_id or "").strip()
        if not aid:
            continue
        account_ids_by_sk.setdefault(sk, set()).add(aid)

    added = 0
    for sk in sorted(account_ids_by_sk.keys()):
        existing = session.exec(
            select(UserPipelineSource).where(
                UserPipelineSource.user_id == uid,
                UserPipelineSource.source_key == sk,
            )
        ).first()
        if existing is not None:
            continue
        ids = account_ids_by_sk[sk]
        if len(ids) > 1:
            picked = sorted(ids)[0]
            logger.warning(
                "sync-user-pipeline-sources: user_id=%r source_key=%r has multiple account_id "
                "values %s — using %r for uploads (registry has a single key per format)",
                uid,
                sk,
                sorted(ids),
                picked,
            )
        else:
            picked = next(iter(ids))
        session.add(
            UserPipelineSource(
                user_id=uid,
                source_key=sk,
                account_id=picked,
                currency="INR",
                statement_folder=None,
            )
        )
        added += 1
    return added


def sync_user_pipeline_sources_from_transactions(session: Session, user_id: str) -> int:
    """Create missing ``UserPipelineSource`` rows from existing ``Transaction`` account_ids.

    Covers users who already have email-parsed (or migrated) rows but never got
    ``ScraperAccountMapping`` / ``UserPipelineSource`` from Gmail persist-sources — they
    would otherwise hit ``outcome=no_source`` on manual statement upload.

    **HDFC savings:** Uses ``hdfc_savings`` for the first distinct ``HDFC_SAL_*`` account when
    that slot is free; additional accounts use ``hdfc_savings_<last4>`` (registry extended via
    :func:`parsers.uploads.register_dynamic_hdfc_savings_key`).

    **HDFC credit card:** ``hdfc_cc_<last4>`` when that key can be registered.

    **ICICI savings:** ``icici_savings`` for the first ``ICICI_SAV_*``; further accounts use
    ``icici_savings_<last4>``.

    Returns:
        Count of new ``UserPipelineSource`` rows staged (caller commits).
    """
    from parsers.uploads import (
        PARSER_REGISTRY,
        register_dynamic_hdfc_cc_key,
        register_dynamic_hdfc_savings_key,
        register_dynamic_icici_savings_key,
    )

    uid = (user_id or "").strip()
    if not uid:
        return 0

    # Use execute().scalars() so each row is the column value (str). session.exec().all()
    # returns Row/tuple-shaped rows that confuse mypy when mixed with scalar unpacking.
    account_ids_raw = session.execute(
        select(Transaction.account_id).where(Transaction.user_id == uid)
    ).scalars().all()
    account_ids_set: set[str] = set()
    for aid in account_ids_raw:
        s = (aid or "").strip()
        if s:
            account_ids_set.add(s)
    account_ids = sorted(account_ids_set)

    added = 0
    for aid in account_ids:
        if session.exec(
            select(UserPipelineSource).where(
                UserPipelineSource.user_id == uid,
                UserPipelineSource.account_id == aid,
            )
        ).first():
            continue

        m_hdfc_sal = re.match(r"^HDFC_SAL_(\d{4})$", aid)
        m_hdfc_cc = re.match(r"^HDFC_CC_(\d{4})$", aid)
        m_icici = re.match(r"^ICICI_SAV_(\d{4})$", aid)

        if m_hdfc_sal:
            last4 = m_hdfc_sal.group(1)
            base_taken = session.exec(
                select(UserPipelineSource).where(
                    UserPipelineSource.user_id == uid,
                    UserPipelineSource.source_key == "hdfc_savings",
                )
            ).first()
            if base_taken is None:
                sk = "hdfc_savings"
            else:
                sk = register_dynamic_hdfc_savings_key(last4)
            if sk not in PARSER_REGISTRY:
                continue
            if session.exec(
                select(UserPipelineSource).where(
                    UserPipelineSource.user_id == uid,
                    UserPipelineSource.source_key == sk,
                )
            ).first():
                continue
            session.add(
                UserPipelineSource(
                    user_id=uid,
                    source_key=sk,
                    account_id=aid,
                    currency="INR",
                    statement_folder=None,
                )
            )
            added += 1
            logger.info(
                "sync-user-pipeline-sources-from-txns: user_id=%r added %r → %r",
                uid,
                sk,
                aid,
            )
            continue

        if m_hdfc_cc:
            last4 = m_hdfc_cc.group(1)
            sk = register_dynamic_hdfc_cc_key(last4)
            if session.exec(
                select(UserPipelineSource).where(
                    UserPipelineSource.user_id == uid,
                    UserPipelineSource.source_key == sk,
                )
            ).first():
                continue
            session.add(
                UserPipelineSource(
                    user_id=uid,
                    source_key=sk,
                    account_id=aid,
                    currency="INR",
                    statement_folder=None,
                )
            )
            added += 1
            logger.info(
                "sync-user-pipeline-sources-from-txns: user_id=%r added %r → %r",
                uid,
                sk,
                aid,
            )
            continue

        if m_icici:
            last4 = m_icici.group(1)
            base_taken = session.exec(
                select(UserPipelineSource).where(
                    UserPipelineSource.user_id == uid,
                    UserPipelineSource.source_key == "icici_savings",
                )
            ).first()
            if base_taken is None:
                sk = "icici_savings"
            else:
                sk = register_dynamic_icici_savings_key(last4)
            if sk not in PARSER_REGISTRY:
                continue
            if session.exec(
                select(UserPipelineSource).where(
                    UserPipelineSource.user_id == uid,
                    UserPipelineSource.source_key == sk,
                )
            ).first():
                continue
            session.add(
                UserPipelineSource(
                    user_id=uid,
                    source_key=sk,
                    account_id=aid,
                    currency="INR",
                    statement_folder=None,
                )
            )
            added += 1
            logger.info(
                "sync-user-pipeline-sources-from-txns: user_id=%r added %r → %r",
                uid,
                sk,
                aid,
            )

    return added


# Masked card/account endings in bank mail — several shapes seen in production templates.
# See ``hdfc_bank.py`` / ``icici_bank.py`` parsers: not everything uses ``**1234``.
_LAST4_PATTERNS: list[re.Pattern[str]] = [
    # HDFC inbound UPI / generic masks: **3703, ***3703
    re.compile(r"\*{1,4}(\d{4})\b"),
    # HDFC CC transaction alert (legacy + 2026): "…Credit Card ending 1905 …"
    re.compile(r"(?i)credit\s+card\s+ending\s+(\d{4})\b"),
    # HDFC UPI outbound: "debited from account 3703"
    re.compile(r"(?i)debited\s+from\s+account\s+(\d{4})\b"),
    re.compile(r"(?i)has\s+been\s+debited\s+from\s+account\s+(\d{4})\b"),
    # HDFC UPI inbound: "credited to your account **3703"
    re.compile(r"(?i)credited\s+to\s+your\s+account\s+\*{1,4}(\d{4})\b"),
    re.compile(r"(?i)your\s+account\s+\*{1,4}(\d{4})\b"),
    # ICICI IMPS/NEFT: "Savings Account XXXX6118"
    re.compile(r"(?i)icici\s+bank\s+savings\s+account\s+[xX*]{2,}(\d{4})\b"),
    # Same mask without the word "Savings" (seen on some credit / transfer alerts).
    re.compile(r"(?i)your\s+icici\s+bank\s+account\s+[xX*]{2,}(\d{4})\b"),
    re.compile(r"(?i)savings\s+account\s+[xX]{3,}(\d{4})\b"),
    # Legacy HDFC email statement subject/body: "…HDFC Bank Account 3703 for…"
    re.compile(r"(?i)hdfc\s+bank\s+account\s+(\d{4})\s+for\b"),
    re.compile(r"(?i)bank\s+account\s+(\d{4})\s+for\s+the\s+period\b"),
    # HDFC Smart/Combined Statement subject: "…A/c No. XXXXXXXX3703…" or "…A/c XXXXXXXX3703…"
    # HDFC uses X-masking (not asterisks) in statement subjects, so \*{1,4}(\d{4}) never fires.
    re.compile(r"(?i)\ba/c\s+(?:no\.?\s*)?[xX*]{2,}(\d{4})\b"),
]

# ICICI often masks savings as XX118 (2 X + 3 digits) or XXXX118 (4 X + 3 digits) —
# the visible tail is the last three digits of the account last-four (e.g. 6118 → 118).
_ICICI_PARTIAL_TAIL3: list[re.Pattern[str]] = [
    re.compile(r"(?i)your\s+icici\s+bank\s+account\s+[xX]{2}(\d{3})\b"),
    re.compile(r"(?i)your\s+icici\s+bank\s+account\s+[xX]{4}(\d{3})\b"),
    re.compile(r"(?i)from\s+your\s+icici\s+bank\s+savings\s+account\s+[xX]{2}(\d{3})\b"),
    re.compile(r"(?i)from\s+your\s+icici\s+bank\s+savings\s+account\s+[xX]{4}(\d{3})\b"),
    re.compile(r"(?i)icici\s+bank\s+savings\s+account\s+[xX]{4}(\d{3})\b"),
    re.compile(r"(?i)icici\s+bank\s+savings\s+account\s+[xX]{2}(\d{3})\b"),
]


def _template_for_sender(sender_norm: str) -> dict[str, Any] | None:
    row = BANK_SENDERS.get(sender_norm)
    if not row:
        logger.warning("persist-sources: sender %r not in BANK_SENDERS template — skip", sender_norm)
        return None
    return dict(row)


def _email_count_estimate_from_raw(raw: dict[str, Any]) -> int:
    est = raw.get("email_count_estimate")
    try:
        return int(est) if est is not None else 0
    except (TypeError, ValueError):
        return 0


def _is_nse_co_in_broker_sender(sender_norm: str) -> bool:
    """True for configured ``@nse.co.in`` broker senders (trade confirmations).

    These overlap ICICI Direct imports when both are present — we treat NSE as fallback-only.
    """
    row = BANK_SENDERS.get(sender_norm)
    if not row or row.get("instrument_type") != "broker":
        return False
    return "@nse.co.in" in sender_norm.lower()


def discovery_has_non_nse_broker_mail(sources: list[Any]) -> bool:
    """True when discovery found broker mail from a non-NSE sender (e.g. ICICI Direct statements)."""
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        sender_raw = raw.get("sender_email")
        if not isinstance(sender_raw, str) or not sender_raw.strip():
            continue
        if _email_count_estimate_from_raw(raw) <= 0:
            continue
        sender_norm = _normalise_sender(sender_raw)
        cfg = BANK_SENDERS.get(sender_norm)
        if not cfg or cfg.get("instrument_type") != "broker":
            continue
        if _is_nse_co_in_broker_sender(sender_norm):
            continue
        return True
    return False


def filter_redundant_nse_broker_sources(sources: list[Any]) -> list[Any]:
    """Remove NSE broker discovery rows when another broker channel already has mail.

    Trade confirmations from ``nse.co.in`` duplicate ICICI Direct ledger rows when both feeds
    are enabled — persist-sources and password hints should behave as if NSE were absent.
    """
    if not discovery_has_non_nse_broker_mail(sources):
        return sources
    out: list[Any] = []
    dropped = 0
    for raw in sources:
        if isinstance(raw, dict):
            sender_raw = raw.get("sender_email")
            if isinstance(sender_raw, str) and sender_raw.strip():
                sender_norm = _normalise_sender(sender_raw)
                # Also catch NSE senders that arrived via discovery (not in BANK_SENDERS)
                # when the raw row itself advertises instrument_type == "broker".
                raw_is_nse_broker = (
                    "@nse.co.in" in sender_norm.lower()
                    and raw.get("instrument_type") == "broker"
                )
                if _is_nse_co_in_broker_sender(sender_norm) or raw_is_nse_broker:
                    dropped += 1
                    continue
        out.append(raw)
    if dropped:
        logger.info(
            "discovery: suppressed %d NSE broker sender(s); primary broker mail already present",
            dropped,
        )
    return out


def _normalise_sample_chunks(parser_key: str, sample_texts: list[str]) -> str:
    """Turn Gmail samples into plain text similar to what bank email parsers see.

    Raw ``get_message_body`` returns HTML; transaction-alert regexes in ``hdfc_bank`` /
    ``icici_bank`` run on **plain** text extracted from specific tags.  Reuse those
    extractors so last-4 heuristics see the same digits the parsers would.
    """
    pk = (parser_key or "").strip().lower()
    chunks: list[str] = []
    for t in sample_texts:
        if not t:
            continue
        looks_html = "<" in t and ">" in t
        if looks_html and pk in ("hdfc_bank", "hdfc_cc_statement", "hdfc_combined_statement"):
            from parsers.alerts.hdfc import _extract_hdfc_body_text

            chunks.append(_extract_hdfc_body_text(t))
        elif looks_html and pk in ("icici_bank", "icici_statement"):
            from parsers.alerts.icici import _extract_icici_body_text

            chunks.append(_extract_icici_body_text(t))
        else:
            chunks.append(t)
    return "\n".join(chunks)


def _icici_three_digit_tails_from_blob(blob: str) -> set[str]:
    """Collect 3-digit tails after ICICI X-masks (XX118 / XXXX118 style)."""
    out: set[str] = set()
    for pat in _ICICI_PARTIAL_TAIL3:
        for m in pat.finditer(blob):
            t = m.group(1)
            if len(t) == 3 and t.isdigit():
                out.add(t)
    return out


def _full_last4_visible_in_blob(blob_lower: str, t3: str) -> str | None:
    """If exactly one ``d+t3`` (four digits) appears as a standalone number in *blob*, return it."""
    hits: list[str] = []
    for d in "0123456789":
        cand = f"{d}{t3}"
        if re.search(rf"(?<![0-9]){cand}(?![0-9])", blob_lower):
            hits.append(cand)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        logger.warning(
            "persist-sources: ICICI tail %r — multiple full last-4 appear in sample text: %s",
            t3,
            sorted(set(hits)),
        )
    return None


def _resolve_icici_tail3_to_last4(
    session: Session,
    user_id: str,
    tails: set[str],
    blob_lower: str,
) -> set[str]:
    """Map 3-digit masked tails to canonical 4-digit ``last_4_digits`` keys."""
    resolved: set[str] = set()
    if not tails:
        return resolved

    for t3 in sorted(tails):
        via_blob = _full_last4_visible_in_blob(blob_lower, t3)
        if via_blob:
            resolved.add(via_blob)
            continue

        rows = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.user_id == user_id,
                ScraperAccountMapping.source_key == "icici_savings",
            )
        ).all()
        from_maps = sorted(
            {
                r.last_4_digits
                for r in rows
                if len(r.last_4_digits) == 4 and r.last_4_digits.isdigit() and r.last_4_digits.endswith(t3)
            }
        )
        if len(from_maps) == 1:
            resolved.add(from_maps[0])
            continue
        if len(from_maps) > 1:
            logger.warning(
                "persist-sources: ICICI tail %r matches several saved last_4 values: %s",
                t3,
                from_maps,
            )
            continue

        psrc = session.exec(
            select(UserPipelineSource).where(
                UserPipelineSource.user_id == user_id,
                UserPipelineSource.source_key == "icici_savings",
            )
        ).all()
        from_pipeline: list[str] = []
        for ps in psrc:
            aid = (ps.account_id or "").strip()
            m = re.search(r"(\d{4})\s*$", aid)
            if m and len(m.group(1)) == 4 and m.group(1).isdigit() and m.group(1).endswith(t3):
                from_pipeline.append(m.group(1))
        uniq_p = sorted(set(from_pipeline))
        if len(uniq_p) == 1:
            resolved.add(uniq_p[0])
        elif len(uniq_p) > 1:
            logger.warning(
                "persist-sources: ICICI tail %r matches several user_pipeline_sources account_ids: %s",
                t3,
                uniq_p,
            )

    return resolved


def _collect_last4s_from_text(
    blob: str,
    *,
    parser_key: str,
    session: Session | None = None,
    user_id: str | None = None,
) -> set[str]:
    """Find candidate last-4 digit groups from subjects + bodies (plain or HTML)."""
    found: set[str] = set()
    for pat in _LAST4_PATTERNS:
        for m in pat.finditer(blob):
            d4 = m.group(1)
            if len(d4) == 4 and d4.isdigit():
                found.add(d4)
    # Statement subjects often carry product keywords instead of bodies (PDF-only).
    pk = (parser_key or "").strip().lower()

    if pk in ("icici_bank", "icici_statement") and session is not None and user_id:
        tail3 = _icici_three_digit_tails_from_blob(blob)
        if tail3:
            found |= _resolve_icici_tail3_to_last4(session, user_id, tail3, blob.lower())

    # HDFC Combined/Smart Statement emails contain no account number in the subject
    # (e.g. "HDFC Bank Combined Email Statement for January-2026") and the PDF body
    # is an attachment — the email shell has nothing parseable. Fall back to whatever
    # hdfc_savings last-4s are already in the DB (written by alert senders processed
    # earlier in the same persist-sources run, since "alerts@hdfcbank.*" sorts before
    # "hdfcbanksmartstatement@*" alphabetically and each sender is committed before
    # the next one starts).
    if pk == "hdfc_combined_statement" and not found and session is not None and user_id:
        rows = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.user_id == user_id,
                ScraperAccountMapping.source_key == "hdfc_savings",
            )
        ).all()
        for r in rows:
            if len(r.last_4_digits) == 4 and r.last_4_digits.isdigit():
                found.add(r.last_4_digits)
        if found:
            logger.debug(
                "persist-sources: hdfc_combined_statement — no last-4 in email text; "
                "resolved %s from existing hdfc_savings DB mappings",
                sorted(found),
            )
        else:
            logger.warning(
                "persist-sources: hdfc_combined_statement — no last-4 in email text and "
                "no hdfc_savings rows in DB yet; cannot infer account mapping"
            )

    # ICICI e-statement notification mail is usually a PDF shell (digits live in the attachment).
    # Same idea as ``hdfc_combined_statement``: reuse last-4 keys already written from
    # ``icici_bank`` transaction alerts when those senders run first (see persist loop sort).
    if pk == "icici_statement" and not found and session is not None and user_id:
        rows = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.user_id == user_id,
                ScraperAccountMapping.source_key == "icici_savings",
            )
        ).all()
        for r in rows:
            if len(r.last_4_digits) == 4 and r.last_4_digits.isdigit():
                found.add(r.last_4_digits)
        if found:
            logger.debug(
                "persist-sources: icici_statement — no last-4 in email text; "
                "resolved %s from existing icici_savings DB mappings",
                sorted(found),
            )
        else:
            logger.warning(
                "persist-sources: icici_statement — no last-4 in email text and "
                "no icici_savings rows in DB yet; cannot infer account mapping"
            )

    # SBI CAS e-statement — PDF-only shell; reuse savings last-4 keys if alerts ran first.
    if pk == "sbi_statement" and not found and session is not None and user_id:
        rows = session.exec(
            select(ScraperAccountMapping).where(
                ScraperAccountMapping.user_id == user_id,
                ScraperAccountMapping.source_key == "sbi_savings",
            )
        ).all()
        for r in rows:
            if len(r.last_4_digits) == 4 and r.last_4_digits.isdigit():
                found.add(r.last_4_digits)
        if found:
            logger.debug(
                "persist-sources: sbi_statement — no last-4 in email text; "
                "resolved %s from existing sbi_savings DB mappings",
                sorted(found),
            )
        else:
            logger.warning(
                "persist-sources: sbi_statement — no last-4 in email text and "
                "no sbi_savings rows in DB yet; cannot infer account mapping"
            )

    return found


def _infer_account_for_last4(
    last4: str,
    *,
    parser_key: str,
    sample_blob: str,
) -> tuple[str, str] | None:
    """Return ``(account_id, source_key)`` for a masked last-4, or ``None``."""
    pk = (parser_key or "").strip().lower()
    blob = sample_blob.lower()

    if pk in ("hdfc_cc_statement",):
        return (f"HDFC_CC_{last4}", f"hdfc_cc_{last4}")

    if pk in ("hdfc_combined_statement",):
        return (f"HDFC_SAL_{last4}", "hdfc_savings")

    if pk in ("icici_statement",):
        return (f"ICICI_SAV_{last4}", "icici_savings")

    if pk in ("sbi_statement",):
        return (f"SBI_SAV_{last4}", "sbi_savings")

    if pk in ("icici_bank",):
        return (f"ICICI_SAV_{last4}", "icici_savings")

    if pk in ("hdfc_bank",):
        # Do **not** use a blob-wide "credit card" substring — alert footers and UPI
        # payment lines often mention cards while the masked digits are still savings.
        # Only treat as CC when this specific last-4 appears in an explicit CC context.
        cc_pattern = re.compile(
            rf"(?i)(credit\s+card\s+ending\s+{re.escape(last4)}"
            rf"|payment\s+was\s+made\s+using\s+your\s+credit\s+card\s+\S*{re.escape(last4)})",
        )
        if cc_pattern.search(blob):
            return (f"HDFC_CC_{last4}", f"hdfc_cc_{last4}")
        return (f"HDFC_SAL_{last4}", "hdfc_savings")

    logger.warning(
        "persist-sources: unknown parser_key=%r — cannot infer account for last4=%s",
        parser_key,
        last4,
    )
    return None


def _infer_accounts_dict(
    cfg: dict[str, Any],
    sample_texts: list[str],
    *,
    session: Session,
    user_id: str,
) -> dict[str, dict[str, str]]:
    """Build ``accounts`` dict (last_4 → account_id, source_key) for one sender."""
    parser_key = str(cfg.get("parser_key") or "")
    blob = _normalise_sample_chunks(parser_key, sample_texts)

    # Broker ICICI Direct PDFs — template placeholder (statement bodies are not reliable for last-4).
    if parser_key == "icici_direct_statement":
        tmpl = cfg.get("accounts") or {}
        accounts: dict[str, dict[str, str]] = {}
        if isinstance(tmpl, dict):
            for k, v in tmpl.items():
                if isinstance(v, dict) and "account_id" in v and "source_key" in v:
                    accounts[str(k)] = {
                        "account_id": str(v["account_id"]),
                        "source_key": str(v["source_key"]),
                    }
        return accounts

    last4s = _collect_last4s_from_text(blob, parser_key=parser_key, session=session, user_id=user_id)
    accounts = {}
    for last4 in sorted(last4s):
        pair = _infer_account_for_last4(last4, parser_key=parser_key, sample_blob=blob)
        if pair:
            accounts[last4] = {"account_id": pair[0], "source_key": pair[1]}

    return accounts


def _delete_sender_rows(session: Session, user_id: str, sender_norm: str) -> None:
    for row in session.exec(
        select(ScraperAccountMapping).where(
            ScraperAccountMapping.user_id == user_id,
            ScraperAccountMapping.sender_email == sender_norm,
        )
    ).all():
        session.delete(row)
    sender_row = session.exec(
        select(ScraperBankSender).where(
            ScraperBankSender.user_id == user_id,
            ScraperBankSender.sender_email == sender_norm,
        )
    ).first()
    if sender_row:
        session.delete(sender_row)
    # Ensure DELETE hits the DB before any INSERT with the same UNIQUE key in this
    # session (SQLite + SQLAlchemy can otherwise order INSERT before DELETE in one flush).
    session.flush()


def _upsert_sender_with_accounts(
    session: Session,
    user_id: str,
    sender_norm: str,
    cfg: dict[str, Any],
    accounts: dict[str, dict[str, str]],
    member_id: int | None,
) -> None:
    pats = cfg.get("discovery_subject_patterns")
    meta_json = json.dumps(pats) if isinstance(pats, list) else None
    session.add(
        ScraperBankSender(
            user_id=user_id,
            sender_email=sender_norm,
            parser_key=str(cfg["parser_key"]) if cfg.get("parser_key") else None,
            first_run_lookback_days=cfg.get("first_run_lookback_days"),
            enabled=True,
            display_name=cfg.get("display_name"),
            instrument_type=cfg.get("instrument_type"),
            expected_cadence=cfg.get("expected_cadence"),
            discovery_subject_patterns_json=meta_json,
        )
    )
    for last4, acct in accounts.items():
        session.add(
            ScraperAccountMapping(
                user_id=user_id,
                sender_email=sender_norm,
                last_4_digits=str(last4),
                account_id=str(acct["account_id"]),
                source_key=str(acct["source_key"]),
                member_id=member_id,
            )
        )


def _fetch_sample_texts_for_sender(
    gmail_client: GmailClient,
    sender_norm: str,
    sample_message_ids: list[str],
) -> list[str]:
    """Fetch email bodies for a single sender.

    Uses ``sample_message_ids`` carried forward from discovery so we skip the
    ``search_messages`` call entirely (saves one API round-trip per sender).
    Falls back to a fresh search only when no IDs were stored (e.g. old discovery
    data created before this optimisation was added).

    **Important:** HDFC/CC and ICICI statement notifications often put the only
    usable hints (product name → placeholder last-4, or account masks) in the
    **Subject** line while the HTML body is a short “open attachment” shell.
    We therefore prefix each sample with ``Subject: …`` via a cheap metadata fetch,
    mirroring how unit tests pass ``[subject, html]`` pairs into inference.
    """
    sample_texts: list[str] = []
    ids_to_fetch: list[str] = list(sample_message_ids[:5])

    if not ids_to_fetch:
        # Fallback: old discovery payload without sample_message_ids.
        try:
            hits = gmail_client.search_messages(
                f"from:{sender_norm} after:2000/01/01",
                paginate=False,
                max_results_per_page=5,
            )
            ids_to_fetch = [m.id for m in hits[:5]]
        except Exception:
            logger.warning(
                "persist-sources: fallback search_messages failed for sender=%r",
                sender_norm,
                exc_info=True,
            )
            return sample_texts

    for mid in ids_to_fetch:
        try:
            subj = ""
            try:
                meta = gmail_client.fetch_message_by_id(mid)
                subj = (meta.subject or "").strip()
            except Exception:
                logger.debug("persist-sources: no metadata for id=%s", mid, exc_info=True)
            body = gmail_client.get_message_body(mid)
            prefix = f"Subject: {subj}\n\n" if subj else ""
            remain = max(0, 8000 - len(prefix))
            sample_texts.append(prefix + body[:remain])
        except Exception:
            logger.debug("persist-sources: no body for id=%s", mid, exc_info=True)

    return sample_texts


def persist_scraper_sources_from_discovery(
    session: Session,
    user_id: str,
    gmail_client: GmailClient,
    discovery_envelope: dict[str, Any],
) -> dict[str, Any]:
    """Upsert scraper tables for every discovered sender with ``email_count_estimate > 0``.

    Args:
        session: Open SQLModel session (caller commits).
        user_id: Authenticated Arth username.
        gmail_client: Authenticated Gmail client.
        discovery_envelope: Parsed ``OnboardingState.discovery_results_json`` dict
            (must contain ``sources``: list of rows with ``sender_email``,
            ``email_count_estimate``, and optionally ``sample_message_ids``).

    Returns:
        Summary dict with ``senders_processed``, ``senders_skipped``, ``accounts_inferred`` (int).
    """
    sources = discovery_envelope.get("sources")
    if not isinstance(sources, list):
        raise ValueError("discovery envelope must contain a 'sources' list")
    sources = filter_redundant_nse_broker_sources(sources)

    def _persist_sender_sort_key(raw: Any) -> tuple[int, str]:
        """Process ICICI transaction alerts before e-statement shells so statement persist can reuse last-4."""
        if not isinstance(raw, dict):
            return (9, "")
        se = raw.get("sender_email")
        if not isinstance(se, str) or not se.strip():
            return (9, "")
        sn = _normalise_sender(se)
        cfg_row = BANK_SENDERS.get(sn)
        pk = str(cfg_row.get("parser_key") or "") if cfg_row else ""
        if pk == "icici_bank":
            return (0, sn)
        if pk == "icici_statement":
            return (1, sn)
        return (2, sn)

    sources.sort(key=_persist_sender_sort_key)

    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id must not be empty")

    mid = self_member_id(session, uid)
    # ``self_member_id`` may INSERT ``FamilyMember`` (flush only). Commit now so we
    # never hold an open SQLite transaction across slow Gmail network calls below —
    # otherwise another thread (e.g. saving discovery JSON) waits until SQLite's
    # busy timeout and surfaces ``database is locked``.
    session.commit()

    processed = 0
    skipped = 0
    inferred_digits = 0
    # Discovery payloads may list the same sender twice. Commit after each sender so
    # later iterations see prior UPSERTs and we avoid UNIQUE(user_id, sender_email).
    seen_sender: set[str] = set()

    for raw in sources:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        sender_raw = raw.get("sender_email")
        if not isinstance(sender_raw, str) or not sender_raw.strip():
            skipped += 1
            continue
        est = raw.get("email_count_estimate")
        try:
            n_est = int(est) if est is not None else 0
        except (TypeError, ValueError):
            n_est = 0
        if n_est <= 0:
            continue

        sender_norm = _normalise_sender(sender_raw)
        if sender_norm in seen_sender:
            skipped += 1
            continue

        cfg = _template_for_sender(sender_norm)
        if not cfg:
            skipped += 1
            continue

        stored_ids = raw.get("sample_message_ids")
        sample_ids: list[str] = stored_ids if isinstance(stored_ids, list) else []

        sample_texts = _fetch_sample_texts_for_sender(gmail_client, sender_norm, sample_ids)

        accounts = _infer_accounts_dict(cfg, sample_texts, session=session, user_id=uid)
        parser_key = str(cfg.get("parser_key") or "").strip().lower()
        if not accounts:
            if parser_key in _PDF_ONLY_SELF_MAPPING_PARSERS:
                accounts = {}
            else:
                logger.warning(
                    "persist-sources: no last-4 inferred for sender=%r (parser_key=%r) — skip",
                    sender_norm,
                    cfg.get("parser_key"),
                )
                skipped += 1
                continue

        inferred_digits += len(accounts)
        _delete_sender_rows(session, uid, sender_norm)
        _upsert_sender_with_accounts(session, uid, sender_norm, cfg, accounts, mid)
        seen_sender.add(sender_norm)
        session.commit()
        processed += 1

    pipeline_sources_synced = sync_user_pipeline_sources_from_scraper_mappings(session, uid)
    if pipeline_sources_synced:
        session.commit()

    txn_pipeline_synced = sync_user_pipeline_sources_from_transactions(session, uid)
    if txn_pipeline_synced:
        session.commit()

    return {
        "senders_processed": processed,
        "senders_skipped": skipped,
        "accounts_inferred": inferred_digits,
        "pipeline_sources_synced": pipeline_sources_synced,
        "txn_pipeline_sources_synced": txn_pipeline_synced,
    }
