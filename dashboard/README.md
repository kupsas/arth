# Arth Dashboard

Next.js + shadcn/ui dashboard for the Arth personal finance pipeline. You log in once (session cookie against the FastAPI backend), then use the home dashboard (trends, charts, drill-downs), transactions, review queue, **portfolio**, **goals** (including hierarchy and simulation entry points), **simulate**, and settings (reminders + statement upload).

## Stack

| Layer | Choice |
|-------|--------|
| Framework | Next.js 16.2.x (App Router, TypeScript) |
| UI | shadcn/ui → base-ui (see note below) |
| Styling | Tailwind CSS v4 (CSS-based config) |
| Charts | Recharts (via shadcn chart components) |
| Data table | TanStack Table v8 |
| Data fetching | TanStack Query (React Query v5) |
| Theme | next-themes, defaults to dark |

> **shadcn/ui uses `@base-ui/react`** on this install, not the default Radix UI primitives. TooltipTrigger is already a `<button>` — do NOT nest a `<Button>` inside it.

## Quick Start

```bash
# 1. Install dependencies (from the dashboard/ directory)
cd dashboard
npm install

# 2. Start the dev server
npm run dev
# → http://localhost:3000

# 3. The dashboard talks to the FastAPI backend at http://localhost:8000
#    Make sure the backend is running (see root README) before opening the app.
```

**Logs:** this terminal shows Next.js dev-server output (compiles, Fast Refresh). API and scraper messages go to the **backend** terminal and to `data/logs/arth.log` — see [Logs and terminals](../api/README.md#logs-and-terminals) in `api/README.md`.

## Starting the Backend

The dashboard fetches data from the FastAPI backend. In a separate terminal:

```bash
# From the repo root (same folder as `api/` and `pipeline/`)
python3 -m uvicorn api.main:app --port 8000 --reload
# Swagger UI → http://localhost:8000/docs
```

> Use `python3 -m uvicorn` (not `uvicorn` directly). The global `uvicorn` binary
> may point to a different Python version than your SQLModel/FastAPI install.

## Pages

| Route | Description |
|-------|-------------|
| `/login` | Household login — posts to `POST /api/auth/login`; API sets httpOnly `arth_session` (configure `AUTH_*` in the **repo root** `.env`) |
| `/` | Dashboard — “V2” layout: this-month focus, trend charts, category grids, bar drill-down, goals/reminders snippets, statement upload entry points |
| `/transactions` | Full transaction table with filters, sorting, pagination, slide-out edit (including spend tags and exclude-from-analytics) |
| `/review` | Review queue — card-based view of unreviewed transactions with approve/edit/skip actions |
| `/goals` | Goals CRUD, hierarchy / priorities, progress (metrics + simulation hooks) |
| `/portfolio` | Holdings, net worth, trends, investment activity, price-backed valuations |
| `/simulate` | Surplus / goal funding scenarios (calls `/api/simulate` and related goal APIs) |
| `/settings` | Reminders (monthly due dates) and statement upload UI (calls API pipeline upload) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL of the FastAPI backend, or `same-origin` (see below) |
| `INTERNAL_API_URL` | `http://127.0.0.1:8000` | Where the **server-side** proxy sends `/api-backend/*` (FastAPI on your machine) |
| `NEXT_ALLOWED_DEV_ORIGINS` | _(unset)_ | Comma-separated hostnames only (no `https://`) so HMR works when using Cloudflare Tunnel in dev — optional |

Create a `.env.local` file in `dashboard/` to override.

**Auth:** The browser never sees the password hash — only the API validates login. You must set `AUTH_USERNAME`, `AUTH_PASSWORD`, and `AUTH_SECRET_KEY` in the **repository root** `.env` (same file the API loads), then restart `uvicorn`.

**Local dev (default):**

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Cloudflare Tunnel (or any public URL for the UI only):**  
If the browser loads the dashboard at `https://….trycloudflare.com` but you pointed `NEXT_PUBLIC_API_URL` at a *second* tunnel for port 8000, login sets `arth_session` on the API host — Next.js never sees that cookie and you bounce back to `/login`. Use **one tunnel to port 3000** and:

```bash
NEXT_PUBLIC_API_URL=same-origin
```

API calls go to `/api-backend/...` on the same hostname; `app/api-backend/[...path]/route.ts` forwards to FastAPI on loopback. You can stop the second tunnel to 8000.

Optional: `INTERNAL_API_URL` if FastAPI is not on `127.0.0.1:8000`.

## Project Structure

```
dashboard/src/
  proxy.ts                  # Middleware: auth gate + redirects (see file for matchers)
  app/
    layout.tsx                # Root shell: Providers + Sidebar + Header
    page.tsx                  # Dashboard V2 (this month + trends + drill-down)
    globals.css               # Tailwind v4 + shadcn oklch theme vars
    transactions/page.tsx     # Transactions table page
    goals/page.tsx            # Goals + hierarchy / simulation entry
    portfolio/page.tsx        # Portfolio / holdings / net worth
    simulate/page.tsx         # Goal funding & surplus simulation UI
    settings/page.tsx         # Reminders + statement upload
    review/page.tsx           # Review queue page
  components/
    layout/
      sidebar.tsx             # Fixed left nav
      header.tsx              # Page title + theme toggle
      mobile-blocker.tsx      # Viewport < 1024px → desktop-only message
      theme-toggle.tsx        # Dark/light mode button
    providers.tsx             # QueryClient + ThemeProvider + TooltipProvider
    dashboard/                # Dashboard V2 components (charts, reminders, uploads, …)
    portfolio/                # Holdings tables, net worth charts, grouping toggles
    simulation/               # Surplus waterfall, goal timeline, explorer UI
    transactions/             # Transaction table + filters + edit sheet
    review/                   # Review queue + investment review cards
    ui/                       # shadcn UI primitives
  hooks/
    use-transactions.ts       # Transaction endpoints
    use-metrics.ts            # Metrics endpoints
    use-goals.ts              # Goals + related APIs
    use-recurring.ts          # Recurring patterns
    use-settings.ts           # Reminders
    use-investment-transactions.ts  # Investment ledger (portfolio)
    …                         # Other feature hooks as needed
  lib/
    types.ts                  # Shared TypeScript types (mirrors Python models)
    api.ts                    # Typed HTTP client
    counterparty-categories.ts
    utils.ts                  # cn(), formatCurrency, formatDate, categoryColor, etc.
```

## Key Implementation Notes

- **Protected routes** — `/login` is public; all other app routes expect a valid session (`GET /api/auth/me`). The Next.js `src/proxy.ts` middleware enforces this — see that file for the exact matcher.
- **Date range presets** — "This Month", "Last Month", "Last 3M", "Last 6M" or custom via calendar popover.
- **Server-side pagination + sorting** — TanStack Table is used for column definitions and row selection only; the actual data operations happen on the backend.
- **Optimistic cache updates** — `useUpdateTransaction` writes the updated transaction into the React Query cache immediately, then invalidates list queries in the background.
- **Review queue skip** — "Skip" is local state only (no PATCH). Cards reappear on refresh. This is intentional: skip means "deal with later", not "reviewed".
- **Currency formatting** — Indian number system (lakhs/crores) using `Intl.NumberFormat("en-IN")`.
- **CC double-counting** — `CARD_EXPENSE` is included in expense totals; `CARD_PAYMENT` (the CC bill payment) is excluded (it's a self-transfer between your own accounts).
