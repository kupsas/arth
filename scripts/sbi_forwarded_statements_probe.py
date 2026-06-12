#!/usr/bin/env python3
"""
Probe SBI e-account statement PDFs from forwarded Gmail — decrypt and list transactions.

Typical use (dad's forwards in the last ~36 hours)::

    export SBI_STATEMENT_PASSWORD='…'   # mobile-last-5 + DOB DDMMYY — never commit
    python3 scripts/sbi_forwarded_statements_probe.py

    # Explicit sender / window:
    python3 scripts/sbi_forwarded_statements_probe.py \\
        --gmail-from 4ks.murthy@gmail.com --newer-than-days 2

    # One or more message ids (skip search):
    python3 scripts/sbi_forwarded_statements_probe.py \\
        --message-id 19eb550f23324ba6 --message-id 19eb0034666fb37e

Env: ``SBI_STATEMENT_PASSWORD`` (or ingredients in UserSecrets when DB context is set).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: E402, F401 — loads ``.env``

import pikepdf  # noqa: E402

from parsers.statements.sbi import SBIStatementEmailParser, classify_sbi_statement_subject
from parsers.uploads.sbi_savings import SBISavingsParser
from scraper.gmail_client import GmailClient
from scraper.pdf_passwords import resolve_sbi_statement_pdf_password_candidates
from scraper.pdf_utils import decrypt_pdf_with_password_candidates

_DEFAULT_GMAIL_FROM = "4ks.murthy@gmail.com"


def _resolve_password_candidates(cli_password: str | None) -> list[str]:
    if cli_password and cli_password.strip():
        return [cli_password.strip()]
    env = os.getenv("SBI_STATEMENT_PASSWORD", "").strip()
    if env:
        return [env]
    cands = resolve_sbi_statement_pdf_password_candidates()
    if cands:
        return cands
    sys.exit(
        "No PDF password. Pass --password or set SBI_STATEMENT_PASSWORD "
        "(registered mobile last-5 + DOB as DDMMYY)."
    )


def _gmail_from(cli: str | None) -> str:
    return (cli or os.getenv("SBI_PROBE_GMAIL_FROM", _DEFAULT_GMAIL_FROM)).strip()


def _search_messages(client: GmailClient, *, gmail_from: str, newer_than_days: int) -> list:
    days = max(1, newer_than_days)
    query = (
        f'from:{gmail_from} subject:"E-account statement" newer_than:{days}d'
    )
    return client.search_messages(query, paginate=True, max_total=20)


def _accounts_for_probe(last4s: set[str]) -> dict[str, dict[str, str]]:
    """Synthetic last-4 map so :class:`SBIStatementEmailParser` can stamp rows in probes."""
    return {
        l4: {"account_id": f"SBI_SAV_{l4}", "source_key": "sbi_savings"}
        for l4 in sorted(last4s)
        if l4
    }


def _parse_message_pdf(
    pdf_bytes: bytes,
    candidates: list[str],
    *,
    received: date,
    subject: str,
) -> list:
    """Decrypt once, then run :class:`SBIStatementEmailParser` (full email-parser path)."""
    if not classify_sbi_statement_subject(subject):
        print(f"  (warning: subject does not match SBI statement classifier)", file=sys.stderr)

    # Peek at account tails so the email parser can stamp rows (probe-only synthetic map).
    try:
        peek_path, _used = decrypt_pdf_with_password_candidates(pdf_bytes, candidates)
    except pikepdf.PasswordError as e:
        raise SystemExit(f"PDF password failed for subject {subject!r}") from e
    try:
        peek_rows = SBISavingsParser().parse(peek_path)
        last4s = {
            str(r.metadata.get("account_last4") or "").strip()
            for r in peek_rows
            if r.metadata.get("account_last4")
        }
    finally:
        peek_path.unlink(missing_ok=True)

    parser = SBIStatementEmailParser(_accounts_for_probe(last4s))
    return parser.parse_attachment(
        pdf_bytes,
        received,
        email_sender="probe@local",
        email_subject=subject,
    )


def _print_txns(label: str, rows: list) -> None:
    print(f"\n{'=' * 72}")
    print(label)
    print(f"{'=' * 72}")
    if not rows:
        print("  (no transactions — empty period or unmapped account tails)")
        return

    by_acct: dict[str, list] = defaultdict(list)
    for r in rows:
        acct = r.metadata.get("account_id") or r.metadata.get("account_last4") or "?"
        by_acct[str(acct)].append(r)

    for acct, acct_rows in sorted(by_acct.items()):
        print(f"\n  Account {acct} — {len(acct_rows)} transaction(s)")
        print(f"  {'Date':<12} {'Debit':>12} {'Credit':>12}  Description")
        print(f"  {'-' * 12} {'-' * 12} {'-' * 12}  {'-' * 40}")
        for r in sorted(acct_rows, key=lambda x: x.txn_date):
            dr = r.debit_amount if r.debit_amount else Decimal("0")
            cr = r.credit_amount if r.credit_amount else Decimal("0")
            desc = (r.raw_description or "")[:70]
            print(f"  {r.txn_date!s:<12} {dr:>12} {cr:>12}  {desc}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List SBI CAS transactions from forwarded Gmail statement PDFs",
    )
    ap.add_argument(
        "--gmail-from",
        help=f"Forwarded-from address (default: {_DEFAULT_GMAIL_FROM})",
    )
    ap.add_argument(
        "--newer-than-days",
        type=int,
        default=2,
        help="Gmail newer_than window in days (default 2 ≈ last 36h)",
    )
    ap.add_argument(
        "--message-id",
        action="append",
        dest="message_ids",
        metavar="ID",
        help="Gmail message id (repeatable; skips search when set)",
    )
    ap.add_argument(
        "--password",
        help="PDF password override (else SBI_STATEMENT_PASSWORD / UserSecrets)",
    )
    args = ap.parse_args()

    candidates = _resolve_password_candidates(args.password)
    client = GmailClient()
    client.authenticate()

    if args.message_ids:
        from datetime import datetime, timezone

        from googleapiclient.errors import HttpError

        from scraper.gmail_client import GmailMessage

        targets: list[GmailMessage] = []
        for mid in args.message_ids:
            try:
                meta = (
                    client._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                    .execute()
                )
            except HttpError:
                print(f"Could not load message {mid}", file=sys.stderr)
                continue
            headers = {
                h["name"].lower(): h["value"]
                for h in (meta.get("payload") or {}).get("headers") or []
            }
            internal_ms = int(meta.get("internalDate", "0") or "0")
            received_at = datetime.fromtimestamp(internal_ms / 1000.0, tz=timezone.utc)
            targets.append(
                GmailMessage(
                    id=mid,
                    thread_id=str(meta.get("threadId") or ""),
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    received_at=received_at,
                )
            )
    else:
        targets = _search_messages(
            client,
            gmail_from=_gmail_from(args.gmail_from),
            newer_than_days=args.newer_than_days,
        )

    if not targets:
        print("No matching Gmail messages.", file=sys.stderr)
        return 1

    print(f"Found {len(targets)} message(s).")

    total = 0
    for msg in targets:
        subject = msg.subject or ""
        recv = msg.received_at.date() if msg.received_at else date.today()
        atts = client.get_attachments(msg.id)
        if not atts:
            print(f"\nSkipping {msg.id}: no PDF attachment", file=sys.stderr)
            continue
        fname, pdf_bytes = atts[0]
        rows = _parse_message_pdf(
            pdf_bytes,
            candidates,
            received=recv,
            subject=subject,
        )
        total += len(rows)
        when = msg.received_at.isoformat() if msg.received_at else "?"
        _print_txns(
            f"{when} | {subject}\n  Gmail id: {msg.id} | attachment: {fname}",
            rows,
        )

    print(f"\nTotal: {total} transaction(s) across {len(targets)} email(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
