#!/usr/bin/env python3
"""
Download Gmail HTML bodies into tests/fixtures/email_samples/ per manifest.

  - Auth: reuses GmailClient (data/gmail_credentials.json + data/gmail_token.json).
  - Selection: scripts/email_parser_fixtures_manifest.yaml — each row has
    ``filename`` plus either ``message_id`` (pinned) or ``query`` (newest match).
  - Redaction: optional find/replace list in data/email_fixture_redactions.json
    (gitignored — copy from data/email_fixture_redactions.example.json).
    Applied longest-first so short tokens do not clobber longer phrases.
  - Maintainer-only: do not run from CI; never log full HTML.

Usage:
  python3 scripts/sync_email_parser_fixtures.py --dry-run
  python3 scripts/sync_email_parser_fixtures.py
  python3 scripts/sync_email_parser_fixtures.py --emit-expectations
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.email_parser_test_accounts import HDFC_ALERT_ACCOUNTS, ICICI_INSTA_ACCOUNTS
from parsers.alerts.hdfc import (  # noqa: E402
    HDFCAccountUpdateParser,
    HDFCCreditCardAlertParser,
    HDFCUPIAlertParser,
)
from parsers.alerts.icici import ICICINetBankingParser  # noqa: E402
from scraper.gmail_client import GmailClient  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST = REPO_ROOT / "scripts" / "email_parser_fixtures_manifest.yaml"
DEFAULT_OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "email_samples"
REDACTIONS_PATH = REPO_ROOT / "data" / "email_fixture_redactions.json"

# Same fallback date as tests/test_email_parsers.py (only used where body dates absent).
RECEIVED = datetime.date(2026, 3, 19)

REQUIRED_FILENAMES = [
    "alerts_hdfcbank_net_01.html",
    "alerts_hdfcbank_net_02.html",
    "alerts_hdfcbank_net_03.html",
    "alerts_hdfcbank_net_04.html",
    "alerts_hdfcbank_net_05.html",
    "alerts_hdfcbank_net_06_cc_payment_made_2026.html",
    "hdfc_upi_inbound_01.html",
    "hdfc_upi_inbound_02.html",
    "hdfc_upi_inbound_03.html",
    "icici_bank_in_01.html",
    "icici_bank_in_02.html",
]


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = raw.get("fixtures") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"Manifest {path} must contain a 'fixtures:' list")
    return rows


def _load_redactions(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Redactions file {path} must be a JSON array of objects")
    pairs: list[tuple[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        find = item.get("find")
        repl = item.get("replace")
        if isinstance(find, str) and isinstance(repl, str) and find:
            pairs.append((find, repl))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _apply_redactions(html: str, pairs: list[tuple[str, str]]) -> str:
    out = html
    for find, repl in pairs:
        out = out.replace(find, repl)
    return out


def _norm_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_row(client: GmailClient, row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (gmail_message_id, subject_snippet, source_description)."""
    filename = row.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError(f"Invalid row (missing filename): {row!r}")

    mid = _norm_id(row.get("message_id"))
    if mid:
        meta = client.fetch_message_by_id(mid)
        subj = meta.subject[:120] + ("…" if len(meta.subject) > 120 else "")
        return mid, subj, f"message_id for {filename}"

    query = row.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"Row for {filename!r} needs non-empty 'query' or 'message_id'")

    hits = client.search_messages(query.strip(), paginate=False)
    if not hits:
        raise RuntimeError(f"No Gmail results for {filename!r} query: {query!r}")

    if len(hits) > 1:
        logger.warning(
            "Query for %s returned %d messages — using NEWEST. Pin message_id in manifest if wrong.",
            filename,
            len(hits),
        )
        for h in hits[:8]:
            logger.warning("  id=%s  subject=%s", h.id, h.subject[:100])

    chosen = hits[0]
    subj = chosen.subject[:120] + ("…" if len(chosen.subject) > 120 else "")
    return chosen.id, subj, f"newest query match for {filename}"


def _parser_and_parse(filename: str, html: str) -> tuple[str, list[Any]]:
    """Return (parser_label, parsed_transactions)."""
    hdfc_accts = HDFC_ALERT_ACCOUNTS
    icici_accts = ICICI_INSTA_ACCOUNTS

    if filename == "alerts_hdfcbank_net_01.html":
        return "HDFCUPIAlertParser", HDFCUPIAlertParser(hdfc_accts).parse(html, RECEIVED)
    if filename.startswith("alerts_hdfcbank_net_") and filename.endswith(".html"):
        return "HDFCCreditCardAlertParser", HDFCCreditCardAlertParser(hdfc_accts).parse(
            html, RECEIVED
        )
    if filename.startswith("hdfc_upi_inbound_"):
        return "HDFCAccountUpdateParser", HDFCAccountUpdateParser(hdfc_accts).parse(
            html, RECEIVED
        )
    if filename.startswith("icici_bank_in_"):
        return "ICICINetBankingParser", ICICINetBankingParser(icici_accts).parse(html, RECEIVED)
    raise ValueError(f"No parser mapping for fixture {filename!r}")


def _txn_to_dict(t: Any) -> dict[str, Any]:
    """Serialize ParsedTransaction-like object for JSON (debug / emit)."""
    return {
        "txn_date": t.txn_date.isoformat() if t.txn_date else None,
        "raw_description": t.raw_description,
        "debit_amount": str(t.debit_amount) if t.debit_amount is not None else None,
        "credit_amount": str(t.credit_amount) if t.credit_amount is not None else None,
        "ref_number": t.ref_number,
        "metadata": dict(t.metadata) if t.metadata else {},
    }


def emit_expectations(out_dir: Path) -> None:
    """Run parsers on every fixture file and print JSON for updating tests."""
    print("# Parser outputs for HTML in", out_dir, "\n")
    for fname in REQUIRED_FILENAMES:
        path = out_dir / fname
        if not path.exists():
            print(f"# MISSING {fname}\n")
            continue
        html = path.read_text(encoding="utf-8")
        label, txns = _parser_and_parse(fname, html)
        payload = {
            "fixture": fname,
            "parser": label,
            "txn_count": len(txns),
            "transactions": [_txn_to_dict(t) for t in txns],
        }
        print(json.dumps(payload, indent=2))
        print()


def run_sync(
    *,
    manifest: Path,
    out_dir: Path,
    dry_run: bool,
    no_redact: bool,
) -> None:
    rows = _load_manifest(manifest)
    seen_names = {r.get("filename") for r in rows if isinstance(r.get("filename"), str)}
    missing = [f for f in REQUIRED_FILENAMES if f not in seen_names]
    if missing:
        raise ValueError(f"Manifest missing required filenames: {missing}")

    pairs = [] if no_redact else _load_redactions(REDACTIONS_PATH)
    if pairs and not no_redact:
        logger.info("Loaded %d redaction pair(s) from %s", len(pairs), REDACTIONS_PATH)
    elif not no_redact and not REDACTIONS_PATH.exists():
        logger.info("No redactions file at %s — writing raw HTML (--no-redact to silence)", REDACTIONS_PATH)

    client = GmailClient()
    client.authenticate()

    summary: list[tuple[str, str, str]] = []

    for row in rows:
        fname = row["filename"]
        mid, subj, src = _resolve_row(client, row)
        summary.append((fname, mid, subj))
        if dry_run:
            print(f"[dry-run] {fname} ← {mid} ({src})\n  subject: {subj}\n")
            continue

        html = client.get_message_body(mid)
        if not html.strip():
            raise RuntimeError(f"Empty HTML body for {fname} (id={mid})")

        if no_redact:
            final_html = html
        else:
            final_html = _apply_redactions(html, pairs)

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / fname
        out_path.write_text(final_html, encoding="utf-8")
        print(f"Wrote {out_path.relative_to(REPO_ROOT)} ({len(final_html):,} chars) ← gmail_id={mid}")

    if dry_run:
        print(f"Dry run complete — {len(summary)} row(s); no files written.")
    else:
        print(f"\nDone — wrote {len(summary)} fixture(s) to {out_dir}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="YAML manifest path")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Directory for .html fixtures")
    ap.add_argument("--dry-run", action="store_true", help="Resolve IDs and print metadata only")
    ap.add_argument(
        "--no-redact",
        action="store_true",
        help="Skip find/replace redactions (writes raw bank HTML — handle with care)",
    )
    ap.add_argument(
        "--emit-expectations",
        action="store_true",
        help="Do not call Gmail; parse existing files in --out-dir and print JSON for test updates",
    )
    args = ap.parse_args()

    if args.emit_expectations:
        emit_expectations(args.out_dir.resolve())
        return

    run_sync(
        manifest=args.manifest.resolve(),
        out_dir=args.out_dir.resolve(),
        dry_run=args.dry_run,
        no_redact=args.no_redact,
    )


if __name__ == "__main__":
    main()
