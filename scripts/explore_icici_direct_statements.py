#!/usr/bin/env python3
"""
Explore ICICI Direct **equity** and **MF** statement PDFs from Gmail (WS1 Phase 0).

What this script does (step by step):
  1. Loads ``.env`` via ``pipeline.config`` so ``ICICI_STATEMENT_MONTHLY_PASSWORD`` works.
  2. Logs into Gmail (OAuth; may open a browser the first time).
  3. Runs two searches (same idea as the Gmail web UI):
        - ``from:service@icicisecurities.com`` + equity subject
        - same sender + mutual fund subject
  4. For each hit: prints message id, subject, date, and PDF attachment names.
  5. For the newest N messages per category (default 3), downloads PDFs, decrypts them
     with the monthly ICICI password chain, saves **decrypted** copies under
     ``data/samples/icici_direct_{equity|mf}/``, and writes ``.txt`` dumps of
     page text + ``extract_tables()`` output for layout inspection.

No database writes. Safe to re-run; it overwrites same-named sample files.

Usage::

    # List matches only (no files written)
    python3 scripts/explore_icici_direct_statements.py --list-only

    # Default: up to 3 PDFs per kind, with text/table dumps
    python3 scripts/explore_icici_direct_statements.py

    # Wider search window
    python3 scripts/explore_icici_direct_statements.py --after 2018/01/01

**Phase 0B (format decision):** After you have ``*.txt`` dumps, look for:
  - Phrases like *Consolidated Account Statement* (CDSL/NSDL style) → heavier CAS parser.
  - Simple transaction grids with Date / Symbol / Qty / Rate → closer to reusing
    column logic from the CSV parsers with pdfplumber table extraction.
  The script also prints a **heuristic guess** at the end (keyword-based, not a guarantee).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: F401 — load ``.env`` before password resolution

import pdfplumber  # noqa: E402

from scraper.gmail_client import GmailClient  # noqa: E402
from scraper.pdf_passwords import (  # noqa: E402
    ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS,
    resolve_pdf_password_chain,
)
from scraper.pdf_utils import decrypt_pdf  # noqa: E402

# --- Gmail queries (match user-specified web UI searches) -----------------
SENDER = "service@icicisecurities.com"

# Subject filters — phrase search like ``subject:(Equity Transaction Statement from)``
EQUITY_SUBJECT = "Equity Transaction Statement from"
MF_SUBJECT = "Mutual Fund Account Statement from"

# Output roots (gitignored via data/samples/)
OUT_EQUITY = REPO_ROOT / "data" / "samples" / "icici_direct_equity"
OUT_MF = REPO_ROOT / "data" / "samples" / "icici_direct_mf"

# ---------------------------------------------------------------------------
# Phase 0B — confirmed from data/samples/icici_direct_* / dump_*.txt (2026-05).
# ---------------------------------------------------------------------------
FORMAT_DECISION_EQUITY = (
    "ICICI Securities **Equity Transaction Statement** (period e.g. FY): NSE cash-market "
    "legs with ISIN, security name, B/S, qty, rates, net amount — **not** a CDSL/NSDL CAS. "
    "Plain-text extract is readable but multi-line header noise; implement with pdfplumber "
    "(words/lines or toleranced table), map ISIN → NSE via existing equity resolver, "
    "txn_date from settlement/trade columns per row. Comparable difficulty to contract-note "
    "style PDFs, different layout than annual CSV export."
)
FORMAT_DECISION_MF = (
    "ICICI Securities **Mutual Fund Account Statement**: folio blocks + transaction lines "
    "(Purchase-SIP, Redemption, etc.), plus summary pages (scheme, NAV, units, amount). "
    "**Not** CAS. pdfplumber extract_tables() is patchy on transaction pages (garbled header "
    "OCR in extract_text); expect regex/word-box parsing or hand-tuned table settings. "
    "Semantic mapping aligns with icici_direct_mf CSV (folio, scheme, type, date, units, "
    "amounts) but PDF layout needs its own parser module."
)


def _gmail_subject_query(phrase: str) -> str:
    """Build a Gmail ``subject:"..."`` clause (phrase must match web UI search)."""
    # Escape double quotes inside phrase (unlikely for these fixed strings).
    safe = phrase.replace("\\", "\\\\").replace('"', '\\"')
    return f'from:{SENDER} subject:"{safe}"'


def _safe_filename(s: str, max_len: int = 80) -> str:
    """Make a fragment safe for use in a file name (cross-platform)."""
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", "_", s.strip())
    return s[:max_len] if len(s) > max_len else s


def _decrypt_password() -> str:
    """Return resolved PDF password (monthly ICICI chain — same as bank statements)."""
    return resolve_pdf_password_chain(*ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS)


def _dump_pdf_to_txt(pdf_path: Path, out_txt: Path) -> str:
    """
    Read decrypted PDF; write full text + table attempts to ``out_txt``.

    Returns the concatenated plain text of all pages (for heuristic scoring).
    """
    full_text: list[str] = []
    lines: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            full_text.append(text)
            lines.append(f"{'=' * 72}\nPAGE {i}\n{'=' * 72}\n")
            lines.append(text)
            lines.append("\n")

            # Table extraction — may be empty on complex CAS layouts; still useful signal.
            try:
                tables = page.extract_tables() or []
            except Exception as exc:  # noqa: BLE001 — exploration script
                lines.append(f"[extract_tables failed: {exc!r}]\n\n")
                continue

            if not tables:
                lines.append("(extract_tables: no tables on this page)\n\n")
                continue

            for ti, table in enumerate(tables, start=1):
                lines.append(f"--- table {ti} (page {i}) ---\n")
                for row in table or []:
                    cells = [str(c) if c is not None else "" for c in (row or [])]
                    lines.append(" | ".join(cells) + "\n")
                lines.append("\n")

    body = "".join(lines)
    out_txt.write_text(body, encoding="utf-8")
    return "\n".join(full_text)


def _heuristic_format_label(combined_text: str) -> str:
    """
    Quick keyword-based guess for Phase 0B. Refine after eyeballing the .txt files.
    """
    t = (combined_text or "").upper()
    if "CONSOLIDATED" in t and "ACCOUNT" in t and "STATEMENT" in t:
        return "likely_CAS_or_consolidated_style (expect harder multi-page / merged cells)"
    if "CDSL" in t or "NSDL" in t or "DEPOSITORY" in t:
        return "likely_depository_CAS (dedicated CAS parser may be needed)"
    if "SCHEME" in t and "FOLIO" in t and "NAV" in t:
        return "likely_MF_statement_table (map columns to icici_direct_mf CSV-like fields)"
    if "NSE" in t or "BSE" in t or "ISIN" in t:
        return "likely_equity_ledger_table (map to icici_direct_equity / NSE symbol resolution)"
    return "unknown — open the .txt dump and classify by hand"


def _search(client: GmailClient, base_query: str, after: str):
    full = f"{base_query} after:{after}"
    return client.search_messages(full, paginate=True, max_results_per_page=100)


def _list_messages_verbose(client: GmailClient, label: str, messages: list) -> None:
    """Print every hit with attachment count (list-only mode)."""
    print(f"\n{label} — {len(messages)} message(s):")
    for msg in messages:
        pdfs = client.get_attachments(msg.id)
        n_pdf = len(pdfs)
        names = ", ".join(fn for fn, _ in pdfs[:5])
        if len(pdfs) > 5:
            names += ", ..."
        print(
            f"  [{msg.id}] {msg.received_at.date()} | PDFs={n_pdf} | "
            f"{(msg.subject or '')[:90]}"
        )
        if names:
            print(f"      attachments: {names}")


def _download_and_dump_samples(
    client: GmailClient,
    out_dir: Path,
    messages: list,
    max_save: int,
    password: str,
) -> tuple[list[str], int]:
    """
    Download + dump up to ``max_save`` PDFs from the newest messages in ``messages``.

    Returns (text blobs for heuristics, number of PDF files saved).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    texts_for_guess: list[str] = []
    saved = 0

    for msg in messages:
        if saved >= max_save:
            break

        pdfs = client.get_attachments(msg.id)
        if not pdfs:
            print(f"  skip {msg.id} — no PDF attachment")
            continue

        sub_short = _safe_filename((msg.subject or "no_subj")[:60])
        for fn, raw in pdfs:
            if saved >= max_save:
                break
            base = f"{msg.id[:12]}_{sub_short}_{_safe_filename(fn)}"
            if not base.lower().endswith(".pdf"):
                base += ".pdf"
            dec_path = out_dir / f"decrypted_{base}"
            txt_path = out_dir / f"dump_{Path(base).stem}.txt"

            try:
                tmp = decrypt_pdf(raw, password)
                try:
                    dec_path.write_bytes(tmp.read_bytes())
                finally:
                    tmp.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR decrypt {msg.id} ({fn}): {exc!r}")
                continue

            try:
                combined = _dump_pdf_to_txt(dec_path, txt_path)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR pdfplumber {dec_path}: {exc!r}")
                continue

            texts_for_guess.append(combined)
            saved += 1
            print(f"  wrote {dec_path.name} + {txt_path.name}")

    return texts_for_guess, saved


def _analyze_existing_dumps() -> None:
    """
    Re-score whatever ``dump_*.txt`` files already exist under the sample dirs.

    Use this **after** a successful download run — no Gmail needed the second time.
    """
    print("=== Phase 0B — analyze existing dump_*.txt (no Gmail) ===\n")
    for label, out_dir in [("Equity", OUT_EQUITY), ("MF", OUT_MF)]:
        if not out_dir.is_dir():
            print(f"{label}: directory missing → {out_dir}")
            continue
        dumps = sorted(out_dir.glob("dump_*.txt"))
        if not dumps:
            print(f"{label}: no dump_*.txt under {out_dir}")
            continue
        latest = dumps[-1]
        text = latest.read_text(encoding="utf-8", errors="replace")
        print(f"{label}: latest file {latest.name} ({len(text)} chars)")
        print(f"  heuristic: {_heuristic_format_label(text)}")
    print()
    print("Recorded human decisions in this file (edit after your review):")
    print(f"  FORMAT_DECISION_EQUITY = {FORMAT_DECISION_EQUITY!r}")
    print(f"  FORMAT_DECISION_MF     = {FORMAT_DECISION_MF!r}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--after",
        default="2015/01/01",
        help="Gmail after: filter (YYYY/MM/DD)",
    )
    ap.add_argument(
        "--max-save",
        type=int,
        default=3,
        help="Max decrypted PDFs + dumps per category (equity / MF)",
    )
    ap.add_argument(
        "--list-only",
        action="store_true",
        help="Only print matching messages; do not download",
    )
    ap.add_argument(
        "--analyze-existing",
        action="store_true",
        help="Only read data/samples/icici_direct_*/dump_*.txt and print heuristics (no Gmail)",
    )
    args = ap.parse_args()

    if args.analyze_existing:
        _analyze_existing_dumps()
        print(
            "Human step: open the .txt files, confirm table shape, then edit "
            "FORMAT_DECISION_EQUITY / FORMAT_DECISION_MF at the top of this script."
        )
        return

    pwd = _decrypt_password()
    if not pwd and not args.list_only:
        print(
            f"Missing password — set one of {ICICI_MONTHLY_STATEMENT_PASSWORD_KEYS} in .env",
            file=sys.stderr,
        )
        sys.exit(3)

    equity_q = _gmail_subject_query(EQUITY_SUBJECT)
    mf_q = _gmail_subject_query(MF_SUBJECT)

    client = GmailClient()
    client.authenticate()

    print("\n--- Equity search ---\n", equity_q + f" after:{args.after}", "\n")
    eq_msgs = _search(client, equity_q, args.after)
    print(f"Hits: {len(eq_msgs)}")

    print("\n--- Mutual fund search ---\n", mf_q + f" after:{args.after}", "\n")
    mf_msgs = _search(client, mf_q, args.after)
    print(f"Hits: {len(mf_msgs)}")

    if args.list_only:
        _list_messages_verbose(client, "Equity", eq_msgs)
        _list_messages_verbose(client, "Mutual fund", mf_msgs)
        print("\nDone (list-only).")
        return

    print("\nDownloading sample PDFs (equity)…")
    eq_texts, eq_saved = _download_and_dump_samples(
        client, OUT_EQUITY, eq_msgs, args.max_save, pwd
    )
    print("\nDownloading sample PDFs (mutual fund)…")
    mf_texts, mf_saved = _download_and_dump_samples(
        client, OUT_MF, mf_msgs, args.max_save, pwd
    )

    print(
        f"\nSaved PDFs: equity={eq_saved} dir={OUT_EQUITY} | "
        f"mf={mf_saved} dir={OUT_MF}"
    )

    # --- Phase 0B heuristic summary (confirm by reading dump_*.txt) ---
    print("\n=== Phase 0B heuristic (keyword-based; verify in dump_*.txt) ===\n")
    if eq_texts:
        blob = "\n".join(eq_texts)
        print(f"Equity: {_heuristic_format_label(blob)}")
    else:
        print("Equity: no PDFs saved — nothing to score.")
    if mf_texts:
        blob = "\n".join(mf_texts)
        print(f"MF:     {_heuristic_format_label(blob)}")
    else:
        print("MF:     no PDFs saved — nothing to score.")

    print(
        "\nRecommended next step: open the latest dump_*.txt files and confirm "
        "whether tables are clean grids or CAS-style. Then implement Phase 2 parsers."
    )


if __name__ == "__main__":
    main()
