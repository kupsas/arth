#!/usr/bin/env python3
"""
Phase 0f — Compare a statement PDF from Gmail against rows already in ``transactions``.

Use this after you can parse a statement PDF (e.g. ICICI savings) to prove the email
extract matches your database **100%** for a date window — same idea as ground-truth
checks, but for one email attachment.

What it does (high level):
  1. Load Gmail → find the message (by id or search query).
  2. Download the PDF, decrypt with a password from ``.env`` (or ``--password``).
  3. Run the chosen pipeline PDF parser → ``list[ParsedTransaction]``.
  4. Load ``Transaction`` rows from SQLite for ``--account-id`` in ``--date-from`` … ``--date-to``.
  5. Greedy 1:1 matching on (date, direction, amount), then description similarity.

Matching rules (strict, but practical for INR floats and bank text quirks):
  - Same **calendar date**, **INFLOW/OUTFLOW**, and amount within ``--amount-tol``.
  - **Fuzzy description**: :func:`difflib.SequenceMatcher` ratio on normalized text
    must be ≥ ``--fuzzy-min``. If date/amount/direction match but text is weaker, we
    still pair once and label the row **partial** (so you can chase 100% narration match).

Usage examples (repo root, ``.env`` loaded via ``pipeline.config``):

    # Newest email matching a subject-style query (first hit). Monthly ICICI statement:
    python3 scripts/validate_email_statement.py \\
        --query 'subject:\"ICICI Bank Statement from\"' \\
        --account-id ICICI_SAL_XXXX \\
        --date-from 2025-01-01 --date-to 2025-01-31 \\
        --password-env ICICI_STATEMENT_MONTHLY_PASSWORD \\
        --parser icici_savings

    # Known Gmail message id (from URL or API).
    python3 scripts/validate_email_statement.py \\
        --message-id 18abc... \\
        --account-id ICICI_SAL_XXXX \\
        --date-from 2025-01-01 --date-to 2025-01-31 \\
        --parser icici_savings

Requires: Gmail OAuth token, DB at ``pipeline.config.DB_PATH``, parser deps (pdfplumber, pikepdf).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from difflib import SequenceMatcher
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline.config  # noqa: E402, F401 — loads ``.env``

from sqlmodel import Session, col, select  # noqa: E402

from api.database import get_engine, init_db  # noqa: E402
from api.models import Transaction  # noqa: E402
from pipeline.models import ParsedTransaction  # noqa: E402
from pipeline.parsers.hdfc_cc_pdf import HDFCCreditCardPdfParser  # noqa: E402
from pipeline.parsers.hdfc_savings_pdf import HDFCSavingsPdfParser  # noqa: E402
from pipeline.parsers.icici_savings import ICICISavingsParser  # noqa: E402
from scraper.gmail_client import GmailClient  # noqa: E402
from scraper.pdf_utils import decrypt_pdf  # noqa: E402


def _norm_desc(s: str) -> str:
    """Lowercase and collapse whitespace — better fuzzy compare on bank strings."""
    return " ".join((s or "").lower().split())


def _fuzzy_ratio(a: str, b: str) -> float:
    na, nb = _norm_desc(a), _norm_desc(b)
    if not na and not nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _parsed_direction_amount(p: ParsedTransaction) -> tuple[str, Decimal]:
    if p.debit_amount > 0:
        return "OUTFLOW", p.debit_amount
    return "INFLOW", p.credit_amount


def _amount_close(a: Decimal, b: float, tol: float) -> bool:
    return abs(float(a) - float(b)) <= tol + 1e-9


def _parse_pdf_for_parser(
    parser_name: str,
    pdf_path: Path,
) -> list[ParsedTransaction]:
    """Dispatch to the right pipeline parser (extend when HDFC PDF parsers land)."""
    if parser_name == "icici_savings":
        return ICICISavingsParser().parse(pdf_path)
    if parser_name == "hdfc_cc_pdf":
        return HDFCCreditCardPdfParser().parse(pdf_path)
    if parser_name == "hdfc_savings_pdf":
        return HDFCSavingsPdfParser().parse(pdf_path)
    raise ValueError(f"Unknown --parser {parser_name!r}")


def _load_db_transactions(
    session: Session,
    *,
    account_id: str,
    date_from: dt.date,
    date_to: dt.date,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.account_id == account_id)
        .where(col(Transaction.txn_date) >= date_from)
        .where(col(Transaction.txn_date) <= date_to)
    )
    return list(session.exec(stmt).all())


def _match_rows(
    parsed: list[ParsedTransaction],
    db_rows: list[Transaction],
    *,
    amount_tol: float,
    fuzzy_min: float,
    partial_threshold: float,
) -> tuple[
    list[tuple[ParsedTransaction, Transaction, float, str]],
    list[ParsedTransaction],
    list[Transaction],
]:
    """Greedy 1:1 match: for each parsed row, best unused DB row on key + fuzzy score.

    Returns:
        matched: (parsed, db_row, fuzzy_ratio, label) where label is \"exact\" or \"partial\"
        missing: parsed rows with no key-compatible DB row left
        extra: DB rows never matched
    """
    # Work on copies we can mark "used"
    unused_db = {t.id: t for t in db_rows if t.id is not None}
    matched: list[tuple[ParsedTransaction, Transaction, float, str]] = []

    # Stable order so greedy matching is reproducible when several rows share date/amount.
    parsed_sorted = sorted(
        parsed,
        key=lambda x: (x.txn_date, float(x.debit_amount + x.credit_amount), x.raw_description),
    )

    for p in parsed_sorted:
        p_dir, p_amt = _parsed_direction_amount(p)
        best_tid: int | None = None
        best_fuzzy = -1.0

        for tid, t in list(unused_db.items()):
            if t.direction != p_dir:
                continue
            if t.txn_date != p.txn_date:
                continue
            if not _amount_close(p_amt, t.amount, amount_tol):
                continue
            f = _fuzzy_ratio(p.raw_description, t.raw_description)
            if f > best_fuzzy:
                best_fuzzy = f
                best_tid = tid

        if best_tid is None:
            continue

        t = unused_db.pop(best_tid)
        label = "exact" if best_fuzzy >= partial_threshold else "partial"
        if best_fuzzy < fuzzy_min:
            label = "partial"
        matched.append((p, t, best_fuzzy, label))

    matched_p = {id(p) for p, _, _, _ in matched}
    missing = [p for p in parsed if id(p) not in matched_p]

    matched_ids = {t.id for _, t, _, _ in matched}
    extra = [t for t in db_rows if t.id is not None and t.id not in matched_ids]

    return matched, missing, extra


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Validate a Gmail statement PDF against DB transactions (Phase 0f).",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--message-id",
        help="Gmail message id (use the id from Gmail URL or API).",
    )
    src.add_argument(
        "--query",
        help='Gmail search string (same syntax as web UI), e.g. subject:"ICICI Bank Statement"',
    )
    ap.add_argument(
        "--parser",
        default="icici_savings",
        choices=["icici_savings", "hdfc_cc_pdf", "hdfc_savings_pdf"],
        help="Which pipeline parser reads the decrypted PDF (more choices as PDF parsers ship).",
    )
    ap.add_argument("--account-id", required=True, help="Must match Transaction.account_id in DB.")
    ap.add_argument("--date-from", required=True, type=dt.date.fromisoformat)
    ap.add_argument("--date-to", required=True, type=dt.date.fromisoformat)
    ap.add_argument(
        "--password-env",
        default="",
        help=(
            "Env var for the PDF password (e.g. ICICI_STATEMENT_MONTHLY_PASSWORD or "
            "ICICI_STATEMENT_ANNUAL_PASSWORD — see docs/personal-data/email-parsers-subject.txt)."
        ),
    )
    ap.add_argument(
        "--password",
        default="",
        help="Inline password (prefer --password-env so secrets stay in .env).",
    )
    ap.add_argument(
        "--attachment-index",
        type=int,
        default=0,
        help="Which PDF attachment to use if the email has several (0-based).",
    )
    ap.add_argument(
        "--amount-tol",
        type=float,
        default=0.02,
        help="Absolute INR tolerance when comparing amounts (float vs Decimal).",
    )
    ap.add_argument(
        "--fuzzy-min",
        type=float,
        default=0.72,
        help="Minimum fuzzy ratio to count as a confident match.",
    )
    ap.add_argument(
        "--partial-below",
        type=float,
        default=0.88,
        help="Ratios below this (but still ≥ fuzzy-min) are reported as partial.",
    )
    args = ap.parse_args()

    if args.date_from > args.date_to:
        ap.error("--date-from must be ≤ --date-to")

    pwd = (args.password or "").strip()
    if not pwd:
        env_name = (args.password_env or "").strip()
        if not env_name:
            ap.error(
                "Provide --password or --password-env "
                "(e.g. ICICI_STATEMENT_MONTHLY_PASSWORD for monthly ICICI statements)",
            )
        pwd = (os.getenv(env_name) or "").strip()
        if not pwd:
            ap.error(f"Environment variable {env_name} is unset or empty")

    client = GmailClient()
    client.authenticate()

    if args.message_id:
        msg_id = args.message_id.strip()
    else:
        q = args.query.strip()
        after = "2015/01/01"
        full = f"{q} after:{after}"
        hits = client.search_messages(full, paginate=False, max_results_per_page=5)
        if not hits:
            print("No Gmail messages matched the query.", file=sys.stderr)
            sys.exit(2)
        msg_id = hits[0].id
        subject = hits[0].subject

    pdfs = client.get_attachments(msg_id)
    if not pdfs:
        print("No PDF attachments on this message.", file=sys.stderr)
        sys.exit(2)
    if args.attachment_index < 0 or args.attachment_index >= len(pdfs):
        ap.error(f"--attachment-index out of range (0..{len(pdfs) - 1})")

    fname, raw = pdfs[args.attachment_index]
    print(f"Message: {msg_id}")
    print(f"Attachment[{args.attachment_index}]: {fname} ({len(raw):,} bytes)")
    if not args.message_id:
        print(f"Subject: {subject[:120]}")

    decrypted: Path | None = None
    try:
        decrypted = decrypt_pdf(raw, password=pwd)
        parsed_all = _parse_pdf_for_parser(args.parser, decrypted)
    finally:
        if decrypted is not None:
            decrypted.unlink(missing_ok=True)

    # Only compare txns inside the user window (statement PDFs often include adjacent days).
    parsed = [p for p in parsed_all if args.date_from <= p.txn_date <= args.date_to]
    print(
        f"Parsed {len(parsed)} transaction(s) in [{args.date_from} .. {args.date_to}] "
        f"(from {len(parsed_all)} row(s) in PDF).",
    )

    init_db()
    with Session(get_engine()) as session:
        db_rows = _load_db_transactions(
            session,
            account_id=args.account_id,
            date_from=args.date_from,
            date_to=args.date_to,
        )

    matched, missing_p, extra_db = _match_rows(
        parsed,
        db_rows,
        amount_tol=args.amount_tol,
        fuzzy_min=args.fuzzy_min,
        partial_threshold=args.partial_below,
    )

    exact_n = sum(1 for *_, lab in matched if lab == "exact")
    partial_n = sum(1 for *_, lab in matched if lab == "partial")

    print()
    print("=== Summary ===")
    print(f"DB rows in window:     {len(db_rows)}")
    print(f"Parsed rows in window: {len(parsed)}")
    print(f"Matched pairs:         {len(matched)} (exact≈{exact_n}, partial≈{partial_n})")
    print(f"Missing from DB:       {len(missing_p)}")
    print(f"Extra in DB:           {len(extra_db)}")

    ok = len(missing_p) == 0 and len(extra_db) == 0 and partial_n == 0
    print()
    if ok:
        print("100% alignment for this window (no missing/extra/partial).")
    else:
        print("Not 100% — inspect lists below.")

    if missing_p:
        print("\n--- Missing from DB (in PDF, no key match in DB) ---")
        for p in missing_p[:50]:
            d, a = _parsed_direction_amount(p)
            print(f"  {p.txn_date} {d} {a} | {p.raw_description[:100]}")
        if len(missing_p) > 50:
            print(f"  ... and {len(missing_p) - 50} more")

    if extra_db:
        print("\n--- Extra in DB (no matching PDF row) ---")
        for t in extra_db[:50]:
            print(f"  id={t.id} {t.txn_date} {t.direction} {t.amount} | {t.raw_description[:100]}")
        if len(extra_db) > 50:
            print(f"  ... and {len(extra_db) - 50} more")

    if matched:
        weak = [m for m in matched if m[3] == "partial"]
        if weak:
            print("\n--- Partial matches (review narration) ---")
            for p, t, f, _ in weak[:40]:
                print(f"  fuzzy={f:.3f} | PDF: {p.raw_description[:80]}")
                print(f"           DB:  {t.raw_description[:80]}")
            if len(weak) > 40:
                print(f"  ... and {len(weak) - 40} more")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
