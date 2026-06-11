# Development worktrees

Arth uses **git worktrees** so long-lived branches do not fight for the same checkout.

## Layout

| Path | Branch | Purpose |
|------|--------|---------|
| `~/Documents/Arth` | `main` | Day-to-day product development |
| `~/Documents/Arth-demo` | `demo` | Public demo site, Fly deploy (`arth-demo`), local `docker-compose.demo.yml` |

Ephemeral CI worktrees live under `.ci-worktrees/` — do not use that folder for demo.

## One-time setup

From the main worktree:

```bash
cd ~/Documents/Arth
git fetch origin
git worktree add ~/Documents/Arth-demo demo
```

Then wire **local-only** files into the demo tree (gitignored secrets and data stay in `Arth`; demo worktree symlinks to them):

```bash
cd ~/Documents/Arth-demo

ln -sf ../Arth/.env .env
ln -sf ../../Arth/dashboard/.env.local dashboard/.env.local

mkdir -p data
ln -sf ../../Arth/data/arth_demo_seed.db data/arth_demo_seed.db
ln -sf ../../Arth/data/.nse_cache data/.nse_cache
ln -sf ../../Arth/data/.amfi_cache data/.amfi_cache
ln -sf ../../Arth/data/gmail_credentials.json data/gmail_credentials.json

rm -rf docs/personal-data docs/private 2>/dev/null || true
ln -sf ../../Arth/docs/personal-data docs/personal-data
ln -sf ../../Arth/docs/private docs/private
```

Targets use `../../Arth/...` for paths under `dashboard/`, `data/`, and `docs/` because symlink resolution is relative to each link’s parent directory, not the repo root.

**Do not symlink** `data/arth_main.db`, `.venv/`, or `dashboard/node_modules/` — install those separately in each worktree if needed.

Install demo deps once (if not using Docker only):

```bash
cd ~/Documents/Arth-demo
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd dashboard && npm install
```

## Keep demo in sync with main

Work only in `~/Documents/Arth-demo` when updating the demo branch. Before deploy or after meaningful `main` changes:

```bash
cd ~/Documents/Arth-demo
git fetch origin
git merge origin/main
# resolve conflicts, then:
pytest tests/test_demo_mode.py
git push origin demo
```

**Do not** merge `demo` into `main` wholesale. If a fix on `demo` must land on `main`, **cherry-pick** that commit onto `main`.

While `Arth-demo` exists, `git checkout demo` in `~/Documents/Arth` will fail — that is intentional.

## Local demo stack

```bash
cd ~/Documents/Arth-demo
python3 scripts/generate_demo_seed.py   # if seed missing
docker compose -f docker-compose.demo.yml up --build
# → http://localhost:3000
```

## Remove the demo worktree

```bash
git worktree remove ~/Documents/Arth-demo
```
