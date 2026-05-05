# Ask Arth — agent package (`agent/`)

**What this is:** The brains behind **Ask Arth** — the part that reads your **local** money diary (through Arth’s server), answers in plain language, and pulls exact numbers when it needs them. Nothing here ships your database to us; anything that leaves your laptop goes only to the **AI providers you turn on**, and only in **small slices** needed for that reply (same idea as [PRIVACY.md](../PRIVACY.md)).

Most people meet Ask Arth in the **website**. This folder also powers a **terminal companion** for hackers and power users who want to experiment next to their code.

---

## Why it exists

You already typed the transactions (or mail pulled them in). Ask Arth is how you **talk back** — “where did dining go this quarter?”, “am I on track for that goal?”, “what changed in **Holdings**?” — without exporting spreadsheets or trusting a random cloud vault.

---

## Two ways to use it


| Where                       | Vibe                                                                                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Website → Ask Arth**      | Click around, see reasoning when we show it, same trust bar as the rest of Arth.                                                                                  |
| **Terminal (command line)** | For contributors: tight loop next to logs and tests. Same data rules; a bit more technical, but answers should still sound like a smart friend, not a spec sheet. |


---

## Terminal quick start

From the **repo root**, with your usual Python env and `.env` keys set (see below):

```bash
python3 -m agent
# same as: python3 -m agent.cli
```

- Type a question, press Enter; `**quit**` or `**exit**` when done.
- Toggle `**debug**` if you want more detail about what Ask Arth reached for under the hood (still your machine).
- Each session writes a **trace file** under `agent/logs/` — handy when something looked wrong and you want to re-read the thread without pasting bank data into chat.

**Heads-up:** The command-line tool loads Arth’s server **inside the same Python process** so it can answer quickly without you juggling ports. That’s a contributor convenience — day‑to‑day users normally just open the website.

---

## Keys & settings (straight talk)

Ask Arth uses **separate** API keys from **auto-categorisation** on imports, so your bill/traffic stays understandable.

Set these in the **root** `.env` when you use the terminal (names mirror [.env.example](../.env.example)):

- `OPENAI_API_KEY_FOR_SINGLE_AGENT`
- `ANTHROPIC_API_KEY_FOR_SINGLE_AGENT`
- `GOOGLE_API_KEY_FOR_SINGLE_AGENT`

If you’ve saved keys in **Settings** inside the app for Ask Arth, prefer that flow for normal use — `.env` is mostly for CI, Docker-only setups, or terminal tinkering.

---

## Safety rails (how we keep it sane)

- **Quick safety check** — Before the heavy lifting: blocks clearly harmful asks, obvious “ignore all instructions” games, random trivia homework, and **buy/sell advice** framed as tips (tracking what you already own is fine). You’ll get a polite refusal, not a lecture.
- **Speed bump** — Per-minute message cap in the terminal so a stuck script doesn’t burn through your wallet.
- **No moving money** — Ask Arth won’t pay bills, place trades, or delete rows; it’s read‑oriented with tight guardrails.

When something’s wrong with **your** numbers, we stay straight and helpful — never jokey about your finances.

---

## For contributors — how it’s wired

**Shape:** A conversation loop loads your **profile snapshot**, then alternates between “think → call a small tool → read result → reply” until the answer is ready. Tools are grouped around **spend**, **Holdings**, **Goals**, and **Simulate**, plus small helpers (currency, dates, sanity checks).

**Important modules**


| Piece                    | Role                                                                                   |
| ------------------------ | -------------------------------------------------------------------------------------- |
| `cli.py` / `__main__.py` | Terminal entry (`python -m agent`).                                                    |
| `core.py`                | One user message through the loop until the final reply.                               |
| `client.py`              | Talks to Arth’s server **in-process** (trusted internal header — not cookie login).    |
| `tools/`                 | Registered capabilities (spend / **Holdings** / **Goals** / **Simulate** / utilities). |
| `security/`              | Gatekeeper pass, rate limit, reply scrubbing, rough cost estimates for logs.           |
| `prompts/`               | System wording for Ask Arth — YAML/text; safe to commit (no secrets).                  |
| `memory.py`              | Short conversational memory for the terminal session.                                  |
| `run_logger.py`          | Structured session logs under `agent/logs/`.                                           |
| `evals/`                 | Maintainer benchmark harness — [agent/evals/README.md](evals/README.md).               |


**Config knobs** live in `agent/config.py` — model ids, fallback order, caps on turns/tool calls, timeouts, optional “thinking depth” toggles for Gemini-flavoured installs. Prefer changing behaviour via env vars documented there rather than hard‑coding.

**Tests & hygiene:** `pytest` covers pieces of screening and tools; heavy multi‑turn suites are often marked slow — see repo CI.

---

## Related docs

- Website stack: [dashboard/README.md](../dashboard/README.md)
- Arth’s server: [api/README.md](../api/README.md)
- Privacy & traces: [PRIVACY.md](../PRIVACY.md)
- Prompt philosophy (import sorting, separate system): [prompts/README.md](../prompts/README.md)

