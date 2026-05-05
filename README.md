# Arth

**Arth is a personal finance app built for how money actually moves in India** — one place on your own computer to see spending, investments, and goals, without handing your life’s ledger to a random cloud.

[CI](https://github.com/kupsas/Arth-AFS/actions/workflows/ci.yml)
[License: AGPL v3](https://www.gnu.org/licenses/agpl-3.0)
[Python 3.13](https://www.python.org/downloads/)
[PRs Welcome](http://makeapullrequest.com)

---

## What Arth does

1. **Your transactions land in one place, on your machine.** Connect Gmail and Arth pulls in bank alerts and statements from your email so your spends and credits fill a **local** money diary. If mail doesn’t have everything (some banks only send certain alerts), you can **upload statements** and Arth folds those in too.
2. **Talk to your money with Ask Arth.** Once your data is here, you can ask questions in plain language.
3. **Local first:** All your data stays in your system. All LLM calls made from within contain anonymised traces of data with no personal information within.

There’s more around **Holdings**, **Goals**, and **Simulate** once you’re in — but the heart of it is: **local diary + local Ask Arth, with minimal traces to any AI you turn on.**

Privacy detail: [PRIVACY.md](PRIVACY.md).

---

## Supported banks, cards, and brokers

Most people’s first question is “does it know my bank?” Here’s what ships **today** — we’re adding more over time.


| Kind              | Supported today      |
| ----------------- | -------------------- |
| **Savings**       | HDFC Bank ICICI Bank |
| **Credit cards**  | HDFC Credit Cards    |
| **Demat brokers** | ICICI Direct         |


**Yours isn’t listed?** We’ll publish a proper contributing guide soon — till then, open an issue and we’ll figure it out together.

---

## Quick start

**Easiest path:** Open this project in **Cursor**, **Claude Code**, **GitHub Copilot**, **Codex**, or any coding agent you already use. You’ll want **[Docker Desktop](https://docs.docker.com/get-docker/)** (Mac or Windows) or **Docker + Compose** on Linux installed first — that’s the one thing the agent can’t download into thin air for you.

**Docker housekeeping:** Keep Docker Desktop (or your Docker engine) **reasonably up to date** — older installs sometimes choke on newer Compose setups. When **this repo updates** (`git pull`), run `**docker compose up --build`** again (or `docker compose build --pull` then `docker compose up`) so your images match the latest code.

Copy **everything** in the box below, paste it into your agent as **your** message, and let it run the steps (clone, env file, `docker compose`, checks). If anything fails, it can read error output and adjust.

```text
You’re my local setup assistant for Arth — an AGPL-3.0 personal finance app.

Repository: https://github.com/kupsas/Arth-AFS  (GitHub name: kupsas/Arth-AFS)

Goal: Run Arth on my machine with Docker so I can use the app in the browser at http://localhost:3000

Please do the following:

1. If we’re not already inside this repo, clone it and `cd` into the project root:
   git clone https://github.com/kupsas/Arth-AFS.git && cd Arth-AFS

2. Verify Docker works on my system (`docker --version` and `docker compose version`). If Docker isn’t installed or the daemon isn’t running, stop and tell me exactly what to install or start (Docker Desktop on Mac/Windows, or Docker Engine + Compose on Linux). If Docker Desktop has a pending update, mention that I should install it — stale Docker often causes weird failures.

3. From the repo root, if `.env` doesn’t exist yet, copy the template:
   cp .env.example .env
   Tell me I can edit `.env` later if I want Ask Arth or auto-categorisation (optional API keys are documented in the comments there).

4. Start the stack (first run builds images — it may take a few minutes):
   docker compose up --build
   Keep this running while I use the app.
   If I just pulled fresh code from GitHub, use the same command (or `docker compose build --pull` then `docker compose up`) so containers rebuild — don’t assume old images are still valid.

5. When the containers are healthy, tell me to open http://localhost:3000 in my browser — that’s Arth’s UI. The backend also listens on port 8000; only mention http://localhost:8000/docs if I ask for developer API docs.

6. Optional heads-up for later: if I connect Gmail, Google needs redirect URL http://127.0.0.1:8090/ (Compose already exposes 8090). Full walkthrough: scraper/README.md in this repo.

If anything errors, show me the command and the message, fix what you can, and explain the next step in plain language.
```

**If you’d rather run commands yourself (Docker)**

```bash
git clone https://github.com/kupsas/Arth-AFS.git
cd Arth-AFS
cp .env.example .env   # optional: edit for Ask Arth / auto-categorisation
docker compose up --build
```

Open **[http://localhost:3000](http://localhost:3000)**. Your data survives restarts in the `**arth_data`** Docker volume. After you **pull new commits**, run `**docker compose up --build`** again so images stay in sync with the repo.

**No Docker — developer install (Python + Node)**

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env

# Terminal A — backend
python3 -m uvicorn api.main:app --port 8000 --reload --no-access-log

# Terminal B — frontend
cd dashboard && npm install && npm run dev
```

First time you open the app, **onboarding** walks you through mail, uploads, and getting transactions in — you don’t need to run bulk import commands first. (Power users touching parsers can still read [pipeline/README.md](pipeline/README.md).)

Open **[http://localhost:3000](http://localhost:3000)** for the app, **[http://localhost:8000/docs](http://localhost:8000/docs)** if you want the API explorer.

---

## For developers

- **Pieces:** SQLite database, FastAPI backend, Next.js frontend — module READMEs have the real depth.
- **Where to read:**
  - [pipeline/README.md](pipeline/README.md) — importing bank lines, sorting rules, adding a new bank format
  - [api/README.md](api/README.md) — backend behaviour, routes, logging
  - [scraper/README.md](scraper/README.md) — Gmail connection and mail-driven imports
  - [dashboard/README.md](dashboard/README.md) — frontend app
  - [agent/README.md](agent/README.md) — Ask Arth (terminal path + contributor map)
  - [docs/system-design/INGESTION_PATHS.md](docs/system-design/INGESTION_PATHS.md) — how mail vs uploads fit together
  - [docs/README.md](docs/README.md) — design notes index
- **Tests:** `pytest tests/` — CI runs lint, types, tests, and a dashboard build (see [.github/workflows/ci.yml](.github/workflows/ci.yml)).
- **Hooks:** `python3 -m pip install pre-commit && pre-commit install`

**Privacy:** [PRIVACY.md](PRIVACY.md)

**Contributing:** Issues and PRs welcome — run tests and match CI before you send something big; ping us on an issue first if it’s a large change.

---

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (`AGPL-3.0-or-later`).