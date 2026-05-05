# How money gets **into** Arth

**Plain rule:** For month-to-month life, **Gmail is the main lane** — alerts and statement PDFs we recognise. **Uploads and file imports** are the **backup** when mail is silent, incomplete, or you’re migrating old folders.

Matching logic when the **same** spend appears twice (mail + statement) lives in `[pipeline/db_writer.py](../pipeline/db_writer.py)`. Human-readable story: `[scraper/README.md](../scraper/README.md)`.

---

## Everyday bank transactions


| Source        | Prefer (ongoing)             | When files win                      |
| ------------- | ---------------------------- | ----------------------------------- |
| HDFC savings  | Mail alerts + PDFs we parse  | Yearly `.txt` or configured folders |
| HDFC cards    | Mail alerts + CC PDFs        | Monthly CSV exports you drop in     |
| ICICI savings | Mail alerts + statement PDFs | PDF / export folders you configure  |


“Which parser?” — mail-side pieces live under `scraper/email_parsers/`; file-side readers under `pipeline/parsers/`. Contributors wire **both** when a bank splits across channels.

**Big historical Gmail catch-up:** use backfill (`POST /api/scraper/backfill`) or `scripts/scrape_historical.py` instead of one-off legacy scripts per bank.

---

## Holdings & broker-style flows

Some investments arrive only as **broker exports** or **statement PDFs**; mail may complement or duplicate. Treat overlapping periods carefully — follow notes in code / hooks when both paths exist for the same FY.

---

## Ways to trigger an import


| You…                          | What runs               |
| ----------------------------- | ----------------------- |
| Leave the server on           | Scheduled Gmail passes  |
| Tap “fetch now” / API         | One mail cycle          |
| Choose a date window          | Historical mail import  |
| Upload in **Settings** or CLI | File readers + pipeline |


Holdings-specific CLIs use `holding_pipeline.py` — see `[pipeline/README.md](../pipeline/README.md)`.