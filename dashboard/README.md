# Website (`dashboard/`)

**What this is:** The **Next.js** front door for Arth — **Home**, **Ask Arth**, your transaction table, **Review**, **Holdings**, **Goals**, **Simulate**, and **Settings** (reminders + uploads). You sign in once; the browser keeps a session cookie talking to **Arth’s server** on port **8000** by default.

---

## Run it locally

```bash
cd dashboard
npm install
npm run dev
# → http://localhost:3000
```

Keep **Arth’s server** running separately (see root `README.md` or `[api/README.md](../api/README.md)`) — the website is only the UI shell without it.

**Where logs go:** This terminal shows compile / hot-reload noise. **Imports, mail, and saves** log from the **Python** side → terminal + `data/logs/arth.log` ([details](../api/README.md#logs-and-terminals)).

---

## Screens (routes)


| Where           | What you use it for                                     |
| --------------- | ------------------------------------------------------- |
| `/login`        | Household login                                         |
| `/` (**Home**)  | This month, trends, categories, reminders, upload entry |
| `/transactions` | Full table, filters, slide-out edit                     |
| `/review`       | Lines that still need a quick eye (often from mail)     |
| `/goals`        | Goals, hierarchy, priorities                            |
| `/portfolio`    | **Holdings**, net worth, activity                       |
| `/simulate`     | Future funding “what ifs”                               |
| `/settings`     | Reminders + statement uploads                           |


---

## Stack (for contributors)


| Layer        | Choice                                                                                                             |
| ------------ | ------------------------------------------------------------------------------------------------------------------ |
| Framework    | Next.js (App Router, TypeScript)                                                                                   |
| UI           | shadcn/ui (this tree uses **@base-ui/react** — tooltips already render a button; don’t nest another button inside) |
| Styling      | Tailwind CSS v4                                                                                                    |
| Charts       | Recharts via shadcn chart wrappers                                                                                 |
| Tables       | TanStack Table                                                                                                     |
| Server state | TanStack Query                                                                                                     |
| Theme        | next-themes (defaults dark)                                                                                        |


---

## Environment variables

Put overrides in `dashboard/.env.local`.


| Variable                   | Typical dev value       | Meaning                                               |
| -------------------------- | ----------------------- | ----------------------------------------------------- |
| `NEXT_PUBLIC_API_URL`      | `http://localhost:8000` | Where the browser calls Arth’s server                 |
| `INTERNAL_API_URL`         | `http://127.0.0.1:8000` | Where **server-side** proxy forwards `/api-backend/*` |
| `NEXT_ALLOWED_DEV_ORIGINS` | *(optional)*            | Hostnames (no `https://`) for tunnel + HMR quirks     |


**Optional:** If you use tunnel setups, see `NEXT_PUBLIC_API_URL` above. For cookie stability across API restarts, set **`AUTH_SECRET_KEY`** in the **repo root** `.env` (see [`api/auth.py`](../api/auth.py)) — there is no household username/password env anymore.

---

## Project layout (short)

- `src/app/` — routes (`page.tsx` files)
- `src/components/` — feature UI (home widgets, transactions table, review cards, …)
- `src/hooks/` — data hooks wrapping the HTTP client
- `src/lib/api.ts` — typed client; `types.ts` mirrors Python shapes

`src/proxy.ts` middleware keeps unauthenticated visitors on `/login`. 