# Archived scripts

These files are **kept for history and rare one-off upgrades**. They are not part of normal day-to-day operation.

| Script | Why it lives here |
|--------|-------------------|
| [`migrate_phase45.py`](migrate_phase45.py) | Phase 4.5 schema migration — baseline `init_db()` now includes this; run only when upgrading a very old `arth.db`. |
| [`remove_duplicate_pdf_email_transactions.py`](remove_duplicate_pdf_email_transactions.py) | Targeted dedupe after a fixed PDF-email ingest bug — prefer current parsers; use only if legacy duplicates remain. |
