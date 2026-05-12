# Arth’s server (`api/`)

**What this is:** The **FastAPI** app behind the website — session cookie sign-in, money diary, **Holdings**, **Goals**, **Simulate**, optional Gmail polling, and background jobs (prices, inflation helpers). One **local** database file; no Arth-owned cloud in the middle.

If you’re **not** writing code, you can ignore this file. If you’re **extending** Arth, you’ll open it while wiring new routes or reading how data is saved.

---

## Start the server

```bash
# From repo root
python3 -m uvicorn api.main:app --port 8000 --reload --no-access-log
```

Use `python3 -m uvicorn` (not a global `uvicorn` binary) so you pick up the same Python environment as the rest of the project.

- `--no-access-log` — quiets the one-line-per-click access log. Drop the flag when you’re hunting which URL the website just called.
- **Interactive map of every URL** — with the server up, open **[http://localhost:8000/docs](http://localhost:8000/docs)** (Swagger).

---

## Logs and terminals

Python code that uses the shared logger writes **the same message shape** to:


| Where                    | What you see                                                                                             |
| ------------------------ | -------------------------------------------------------------------------------------------------------- |
| **This terminal**        | Start-up, background jobs, errors — usually INFO and up |
| **`data/logs/arth.log`** | Same as above **plus** DEBUG (mail fetch detail, label batches, etc.) — file rotates when it gets big |


The **website** dev terminal is mostly layout + hot reload. If a screen looks “stuck,” check the **browser** network tab for failed calls, then the **server** terminal or `arth.log`.

**Backups:** keep database copies under `data/backups/` (gitignored) instead of sprinkling random `.bak` files in `data/`.

**On boot** the server: creates any missing tables, starts the **Gmail** poll (pauses until you’ve signed in), and schedules light **price** and **inflation** refresh work. See `api/main.py` `lifespan` for the exact order.

---

## Sign-in (local install)

Arth is **single-user on your machine** — there is **no** username/password check in the API. The website still calls `POST /api/auth/login` so the browser gets an **httpOnly session cookie** (and Ask Arth / websockets can trust a signed ticket). The body is ignored for validation; everyone maps to the same local install user (`api.constants.DEFAULT_LOCAL_USER`).

**Optional:** Set `AUTH_SECRET_KEY` in the **root** `.env` so cookies and WS tickets survive an API restart. If you skip it, Arth generates a random key per process — fine for dev; annoying if restarts log everyone out.

- **`POST /api/auth/login`** / **`POST /api/auth/logout`** — set or clear that cookie.
- **`GET /api/auth/me`** — “do I still have a valid session?”
- **Almost everything under `/api/...`** — expects a valid session cookie, except **`GET /health`**.

Swagger’s “Try it out” is awkward with cookies — use the real website or a tool that saves cookies.

---

## Which database file?


| Mode               | File                | How to start                      |
| ------------------ | ------------------- | --------------------------------- |
| Normal             | `data/arth_main.db` | default `uvicorn` command         |
| Local test profile | `data/arth_test.db` | `APP_ENV=test` + e.g. port `8001` |
| Automated tests    | in-memory           | `pytest`                          |


---

## Route map (high level)

Exact query params and bodies live in **Swagger** (`/docs`). This is the **human** map:


| Area                                | What it’s for                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- |
| **Auth**                            | Sign in, sign out, “who am I”                                                                     |
| **Transactions**                    | List, filter, edit lines in your diary                                                            |
| **Metrics**                         | Totals, trends, categories, drill-downs for **Home** charts                                       |
| **Pipeline**                        | Kick off imports, uploads, run history                                                            |
| **Scraper**                         | Gmail status, manual poll, OAuth helpers — details in `[scraper/README.md](../scraper/README.md)` |
| **Recurring**                       | Detect / list subscription-style patterns                                                         |
| **Goals**                           | Create goals, tree, links, life events                                                            |
| **Surplus / liquidity / inflation** | Inputs for planning                                                                               |
| **Simulate**                        | “What if” funding runs                                                                            |
| **Holdings**                        | Positions, history, refreshes                                                                     |
| **Investment activity**             | Broker-style rows alongside cash                                                                  |
| **Liabilities / prices**            | Loans and price history                                                                           |
| **Settings**                        | Reminders                                                                                         |


---

## Related docs

- Website behaviour: `[dashboard/README.md](../dashboard/README.md)`
- Gmail & matching rules: `[scraper/README.md](../scraper/README.md)`
- Mail vs upload policy: `[docs/system-design/INGESTION_PATHS.md](../docs/system-design/INGESTION_PATHS.md)`

