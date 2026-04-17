# Understanding So Far — Expense Tracker / Arth (As of This Point)

Single-doc summary of what’s happened so far, for you and for future work (e.g. building the expense tracker in this repo).

---

## 1. Where this started

- **Goal:** Personal finance / expense tracking with a clear mental model and a path to an “agentic” system (Arth).
- **Context:** You had a long design conversation in ChatGPT about:
  - Foundational tables (Transactions, Accounts, Instruments, Goals).
  - Transactions as the only true temporal source of truth; positions derived from them.
  - A canonical transactions schema and how it maps to real bank data.
- **Current stance:** Start building the **expense tracker** in this repo as a testing ground, without getting stuck in planning. The repo is explicitly a **testing repo** for now; structure can be fixed later.

---

## 2. Single source of truth (SSOT)

- **Raw data:** HDFC savings account statement — **FY 2025–26** (and a bit beyond).
- **File:** `docs/personal-data/Acct_Statement_XXXXXXXX3703_11012026.txt` (text export; format documented in the prior chat and in `raw_vs_gsheet_notes.md`).
- **Columns in raw:** Date, Narration, Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance.
- This statement is the **salary/operating account**: salary, UPI, card payments, transfers to ICICI, etc. It is the SSOT for “what actually moved” in that account.

---

## 3. What you already built (Google Sheets → CSV)

- You took the HDFC statement and built an **enriched transactions table** in Google Sheets, then exported it as **GSheet_Transactions.csv** (~650 rows).
- You added:
  - **Stable IDs** (`txn_id`: T_00000001, …).
  - **Normalized fields:** one `amount` + `direction` (INFLOW/OUTFLOW), `txn_date`, `account_id`, `currency` (INR).
  - **Classification:** `txn_type`, `channel`, `upi_type` (P2P/P2M/NA).
  - **Counterparty:** normalized merchant/person/institution name.
  - **Category:** `counterparty_category` for analysis (e.g. Food & Dining, Self Transfer, Rent & Housing).
  - **Audit:** `raw_description` = exact Narration; optional `notes`, `source_statement`.
- You used **Google’s =AI() (Gemini)** with prompts to fill txn_type, channel, upi_type, counterparty, and counterparty_category. Those prompts are recorded in **GSheet_prompts_used.md** (including few-shot examples for counterparty and category).
- **Why you stopped:** Sheets felt “too less agentic”; you wanted to continue in Cursor with a more code/agent-driven workflow.

---

## 4. Design decisions already locked (from prior chat)

- **Transactions first.** One row = one economic event; schema is bank-agnostic and meaning-oriented.
- **Amount always positive;** direction in a separate column.
- **Credit card:** both swipes (CARD_EXPENSE) and bill payments (CARD_PAYMENT) live in the same table; expense views use CARD_EXPENSE (and UPI_EXPENSE, etc.); cash-flow views exclude CARD_EXPENSE and use real cash movements only.
- **IDs:** Sequential, no meaning encoded (e.g. T_00000001); immutable.
- **Raw description** is never altered; all interpretation lives in derived columns.
- **Currency** column exists; default INR; no FX logic yet.
- **Self-transfers** (e.g. HDFC ↔ ICICI, or your name in narration) classified as SELF_TRANSFER; same table, different filters for analysis.

---

## 5. What exists in the repo right now

| Item | Purpose |
|------|--------|
| **prior_ChatGPT_chat.md** | Full transcript of the design conversation. |
| **notes_prior_chatgpt_chat.md** | Condensed notes from that chat (this round of review). |
| **raw_vs_gsheet_notes.md** | How raw HDFC statement (.txt) and GSheet_Transactions differ. |
| **GSheet_prompts_used.md** | Prompts used for =AI() for txn_type, channel, upi_type, counterparty, counterparty_category. |
| **Acct_Statement_XXXXXXXX3703_11012026.txt** | Raw HDFC statement (SSOT); text export. |
| **GSheet_Transactions.csv** | Enriched transactions (one account, ~650 rows) with IDs and classifications. |

---

## 6. Gaps / open questions (for next steps)

- **Account coverage:** So far only HDFC salary account is in the sheet. ICICI (investments) and credit card statements are not yet ingested; the schema supports them.
- **Automation:** No pipeline yet to go from raw statement → enriched table (everything was manual + AI-in-Sheets). Building an expense tracker here is a good place to introduce scripts/agents.
- **Categories:** Category list and prompts are in place; no formal “Needs vs Wants vs Savings” or subscription detection yet.
- **Reconciliation:** Closing balance from raw statement is not in the GSheet; could be added for sanity checks.
- **Structure:** Repo structure is intentionally loose (“we will fix that later”); when you start building the expense tracker, we can introduce minimal structure (e.g. scripts, data, docs) without overdoing it.

---

## 7. Suggested next steps (when you’re ready)

- **Option A — Use existing data:** Treat GSheet_Transactions.csv as the first-class dataset; build the expense tracker (e.g. ingest CSV, compute monthly/category summaries, simple reports or UI) and only later add raw-statement ingestion.
- **Option B — Pipeline from raw:** Add a small pipeline: raw HDFC (export to CSV if needed) → canonical transaction rows (with or without reusing the same AI logic in code) → same schema as GSheet; then build expense views on top.
- **Option C — Hybrid:** Keep GSheet (or CSV) as the current “enriched” source; build the expense tracker against it, and design a path to replace or augment with code-driven ingestion later.

Once you pick a direction (or a mix), we can break it into concrete tasks (e.g. “ingest CSV”, “monthly by category”, “CLI or tiny web dashboard”) and start implementing in this testing repo.

---

*This doc reflects understanding after reviewing: prior_ChatGPT_chat.md, Acct_Statement (format), GSheet_Transactions.csv, and GSheet_prompts_used.md.*
