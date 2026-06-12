#!/usr/bin/env python3
"""
Zerodha monthly demat statement probe — decrypt PDF and print parsed investment transactions.

Usage (from repo root):

    # Gmail (needs ``data/gmail_token.json``; optional --gmail-from override):
    python3 scripts/zerodha_demat_probe.py

    # Local PDF (password via flag or env — never commit real PANs):
    python3 scripts/zerodha_demat_probe.py --pdf path/to/statement.pdf --password ABCDE1234F

    # Raw PDF text (debug layout only):
    python3 scripts/zerodha_demat_probe.py --pdf statement.pdf --dump-text

Env: ``ZERODHA_DEMAT_STATEMENT_PASSWORD`` (PAN), ``ZERODHA_DEMAT_PROBE_GMAIL_FROM`` (optional).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: E402, F401 — loads ``.env``

import pdfplumber  # noqa: E402

from parsers.holdings.zerodha_demat_statement_pdf import (  # noqa: E402
    parse_zerodha_demat_statement_pdf,
)
from pipeline.isin_amfi_resolver import lookup_amfi_scheme_by_isin  # noqa: E402
from pipeline.isin_nse_resolver import lookup_isin, lookup_isin_symbol  # noqa: E402
from scraper.pdf_utils import decrypt_pdf  # noqa: E402

# Zerodha production mailer (public); override with ZERODHA_DEMAT_PROBE_GMAIL_FROM for your inbox.
_DEFAULT_GMAIL_FROM = (
    "no-reply-transaction-with-holding-statement@reportsmailer.zerodha.net"
)
_BUILD_SUBJECT_FRAGMENT = "Monthly Demat Transaction"


def _resolve_password(cli_password: str | None) -> str:
    if cli_password and cli_password.strip():
        return cli_password.strip()
    env = os.getenv("ZERODHA_DEMAT_STATEMENT_PASSWORD", "").strip()
    if env:
        return env
    sys.exit(
        "No PDF password. Pass --password or set ZERODHA_DEMAT_STATEMENT_PASSWORD "
        "(account holder PAN)."
    )


def _gmail_from() -> str:
    return os.getenv("ZERODHA_DEMAT_PROBE_GMAIL_FROM", _DEFAULT_GMAIL_FROM).strip()


def _isin_mapping_line(isin: str | None) -> str:
    """How this ISIN resolves for Arth (same path as ICICI Direct equity PDFs)."""
    if not isin:
        return "isin=—"
    iso = str(isin).strip().upper()
    entry = lookup_isin(iso)
    nse = lookup_isin_symbol(iso)
    if nse:
        nm = (entry or {}).get("name") or ""
        if nm:
            shown = nm if len(nm) <= 40 else nm[:37] + "…"
            return f"isin={iso} → NSE:{nse} ({shown})"
        return f"isin={iso} → NSE:{nse}"
    amfi = lookup_amfi_scheme_by_isin(iso)
    if amfi:
        code = amfi.get("scheme_code") or "?"
        sname = amfi.get("scheme_name") or ""
        name_bit = f" ({sname[:37]}…)" if len(sname) > 38 else (f" ({sname})" if sname else "")
        return f"isin={iso} → AMFI:{code}{name_bit}"
    if iso.startswith("INF"):
        return f"isin={iso} → AMFI:— (run python3 -m pipeline.refresh_amfi_cache)"
    if iso.startswith("INE"):
        return f"isin={iso} → NSE:— (missing from local bhav map — run price refresh / consolidate)"
    return f"isin={iso} → NSE:—"


def _print_investment_txns(path: Path) -> None:
    _holdings, txns = parse_zerodha_demat_statement_pdf(
        path,
        aggregate=True,
        apply_market_prices=True,
    )
    print(f"Investment transactions: {len(txns)}")
    print(
        "Ticker key: NSE bhav for equities; AMFI scheme code for MF demat "
        "(pipeline.isin_nse_resolver / pipeline.isin_amfi_resolver).\n"
    )
    if not txns:
        print("(none — check password, PDF layout, or use --dump-text)")
        return
    print()
    print(f"{'date':<12} {'side':<5} {'qty':>12}  {'stored':<10}  zerodha label / notes")
    print("-" * 96)
    for t in txns:
        stored = (t.symbol or "—")[:10]
        label = (t.name or "")[:32]
        note = (t.notes or "")[:36]
        extra = f" | {note}" if note else ""
        print(
            f"{t.txn_date!s:<12} {t.txn_type:<5} {t.quantity:>12.3f}  {stored:<10}  {label}{extra}"
        )
        meta = t.metadata or {}
        zsym = meta.get("zerodha_symbol")
        if zsym and zsym != (t.name or ""):
            print(f"             zerodha_symbol={zsym}")
        print(f"             {_isin_mapping_line(meta.get('isin'))}")


def _dump_pdf_text(path: Path, *, max_pages: int | None) -> None:
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        limit = n if max_pages is None else min(n, max_pages)
        print(f"Pages: {n} (showing {limit})\n")
        for pi in range(limit):
            print("=" * 72)
            print(f"PAGE {pi + 1}")
            print("=" * 72)
            print(pdf.pages[pi].extract_text() or "")
            print()


def _run_on_pdf(path: Path, *, password: str, dump_text: bool, max_pages: int | None) -> None:
    work = path
    tmp: Path | None = None
    try:
        raw = path.read_bytes()
        try:
            tmp = decrypt_pdf(raw, password=password)
            work = tmp
            print("Decrypt: OK\n")
        except Exception:
            print("Decrypt failed or PDF not encrypted — parsing file as-is\n")
        if dump_text:
            _dump_pdf_text(work, max_pages=max_pages)
        else:
            _print_investment_txns(work)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _probe_gmail(password: str, *, max_messages: int, dump_text: bool, max_pages: int | None) -> bool:
    from scraper.gmail_client import GmailClient

    client = GmailClient()
    client.authenticate()
    from_addr = _gmail_from()
    query = f'from:{from_addr} subject:"{_BUILD_SUBJECT_FRAGMENT}" after:2024/01/01'
    print(f"Gmail query: {query}\n")
    matches = client.search_messages(query, paginate=False, max_results_per_page=max_messages)
    if not matches:
        print("No messages matched. Connect Gmail or pass --pdf.")
        return False

    for i, msg in enumerate(matches):
        print("─" * 72)
        print(f"Message {i + 1}/{len(matches)}: {msg.id}")
        print(f"  Date: {msg.received_at.date()}  Subject: {(msg.subject or '')[:90]}")
        pdfs = client.get_attachments(msg.id)
        if not pdfs:
            print("  SKIP — no PDF attachments")
            continue
        name, raw = pdfs[0]
        print(f"  Attachment: {name} ({len(raw):,} bytes)\n")
        out: Path | None = None
        try:
            out = decrypt_pdf(raw, password=password)
            if dump_text:
                _dump_pdf_text(out, max_pages=max_pages)
            else:
                _print_investment_txns(out)
        except Exception as exc:
            print(f"  FAIL — {exc!r}")
        finally:
            if out is not None:
                out.unlink(missing_ok=True)
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Probe Zerodha demat PDF → investment transactions.")
    p.add_argument("--pdf", type=Path, help="Local encrypted or plain PDF path")
    p.add_argument("--password", help="PDF password (PAN); else env ZERODHA_DEMAT_STATEMENT_PASSWORD")
    p.add_argument("--max-messages", type=int, default=3, help="Gmail messages to inspect")
    p.add_argument("--max-pages", type=int, default=None, help="With --dump-text: pages to show")
    p.add_argument(
        "--dump-text",
        action="store_true",
        help="Dump raw PDF text instead of parsed investment transactions",
    )
    args = p.parse_args()

    password = _resolve_password(args.password)

    if args.pdf is not None:
        pdf_path = args.pdf.expanduser().resolve()
        if not pdf_path.is_file():
            sys.exit(f"File not found: {pdf_path}")
        _run_on_pdf(
            pdf_path,
            password=password,
            dump_text=args.dump_text,
            max_pages=args.max_pages,
        )
        return

    if not (REPO_ROOT / "data" / "gmail_token.json").is_file():
        print(
            "No data/gmail_token.json — connect Gmail in the app, or pass --pdf.\n"
        )
    _probe_gmail(
        password,
        max_messages=args.max_messages,
        dump_text=args.dump_text,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    main()
