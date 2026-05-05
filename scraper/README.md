# Gmail connection (`scraper/`)

**What this is:** The optional path that **reads bank mail** and turns alerts / PDF attachments into rows in your **local** money diary — on a timer, without you uploading every CSV.

**Default story:** Ongoing month → **mail first** (alerts, statement PDFs we know how to read). Gaps, old folders on disk, or weird one-offs → **uploads or file imports** as backup. Longer policy write-up: [docs/system-design/INGESTION_PATHS.md](../docs/system-design/INGESTION_PATHS.md).

---

## Why bother with mail?

Many banks ping you when you swipe a card or pay by UPI. Those pings land in Gmail; Arth can turn them into diary lines **near real time**. Salary lines or some net-banking moves might still wait for a **statement** — that’s normal; upload when the PDF arrives.

---

## Gmail credentials (already in the repo)

This tree ships **`data/gmail_credentials.json`** — a shared **OAuth “Desktop app” client** so you don’t have to create your own Google Cloud project just to try Arth. Each person still completes **Google sign-in** once; Arth saves **your** refresh token to **`data/gmail_token.json`** on **your** machine (that file stays **local** and gitignored — never commit it).

**Advanced:** If you prefer **your own** Google Cloud OAuth client, create a Desktop-app client in Google Cloud Console and **replace** `data/gmail_credentials.json` with that JSON (keep the same path, or edit `GMAIL_CREDENTIALS_PATH` in `scraper/config.py`).

Optional tuning (defaults usually fine):

```bash
SCRAPER_POLL_INTERVAL_MINUTES=15
SCRAPER_LOOKBACK_DAYS=7
```

---

## Sign in once

**With Arth’s server already running:**

```bash
curl -X GET http://localhost:8000/api/scraper/oauth/init
# Open the URL it prints → Allow access
```

**Or** run `python3 scripts/discover_emails.py` before the server if you prefer the discovery flow.

Check it stuck:

```bash
curl http://localhost:8000/api/scraper/oauth/status
```

The poller **starts with the server**. If you haven’t finished Google sign-in yet, it **waits** instead of spamming errors.

**Pull mail right now** (manual):

```bash
curl -X POST http://localhost:8000/api/scraper/trigger
```

---

## Matching mail lines with statement lines

Same coffee purchase can appear twice — once as an **alert**, later on the **PDF**. We **merge** instead of duplicating:

- Same account, **same amount**, date within **±1 day**, still marked as mail-only → **upgrade** that row with richer statement fields, keep **your** edits, mark as reconciled.
- No match → **new** row from the file.

So you keep **one row per real life transaction**. Implementation: `pipeline/db_writer.py` (same idea whether mail came first or file did).

---

## URLs under `/api/scraper` (quick list)


| Method | Path            | In plain words                      |
| ------ | --------------- | ----------------------------------- |
| GET    | `/status`       | Is the poller running? When’s next? |
| POST   | `/trigger`      | Run one fetch cycle now             |
| POST   | `/start`        | Resume scheduled polling            |
| POST   | `/stop`         | Pause scheduled polling             |
| PATCH  | `/config`       | Change how often we poll            |
| GET    | `/emails`       | What we already touched in Gmail    |
| GET    | `/oauth/init`   | Start Google sign-in                |
| GET    | `/oauth/status` | Signed in yet?                      |
| GET    | `/coverage`     | Which accounts get mail vs don’t    |


Full detail: **Swagger** at `http://localhost:8000/docs` while the server runs.

---

## Folder map (for contributors)


| Piece             | Job                                          |
| ----------------- | -------------------------------------------- |
| `gmail_client.py` | Sign-in, token refresh, fetching             |
| `email_router.py` | Pick which parser fits this sender + subject |
| `orchestrator.py` | One full “fetch → parse → save” lap          |
| `scheduler.py`    | Timers (mail + shared maintenance jobs)      |
| `email_parsers/`  | One file per bank / template family          |


**New bank mail:** Add a parser class (`can_parse` + `parse`), register it, add trusted senders in `config.py`.

---

## Behaviour notes

- **Subject-first filtering** — Marketing / OTT / “your MAB” noise often skipped before downloading heavy HTML bodies.
- **Processed mail ledger** — Each Gmail message ID we touch is remembered so restarts don’t re-import the same mail.
- **No overlapping scrapes** — A lock stops two cycles fighting each other.

Tests often force smart labels off by patching config — see existing tests for the pattern.