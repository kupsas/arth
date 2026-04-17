# Notes from Prior ChatGPT Chat (Expense Tracking / Arth)

Condensed notes from `prior_ChatGPT_chat.md` for quick reference. Not a substitute for the full transcript—use when you need the gist without re-reading 2600+ lines.

---

## 1. Mental model: Financial OS

- **Transactions** = what happened (the backbone; only true temporal source of truth).
- **Accounts** = where it happened (HDFC salary, ICICI invest, credit cards).
- **Instruments / Assets** = what you own (MF, equity, EPF, etc.); “current position” is **derived** from transactions, not a primary table.
- **Goals** = why it matters; used to interpret facts, not to compute Layer 1–3.

Layers:

- **Layer 1** — Net worth (from instruments + valuations).
- **Layer 2** — Income & spending (from transactions only).
- **Layer 3** — Cash flow & liquidity (transactions + accounts).
- **Layer 4** — Risk & contingencies (needs static tables: insurance, obligations).
- **Layer 5** — Tax & legal.

Schema-first: design the canonical transactions schema **before** looking at bank output formats.

---

## 2. Transactions table schema (canonical)

**Core identity**

- `txn_id` — Unique ID (sequential e.g. TXN_000001); no meaning encoded.
- `txn_date` — Posted/effective date.
- `account_id` — Which account (HDFC, ICICI, CC).
- `direction` — INFLOW | OUTFLOW.
- `amount` — Always **positive**; sign is in `direction`.

**Classification**

- `txn_type` — Economic intent (enum).
- `channel` — UPI | BANK | CARD | BROKER.
- `upi_type` — P2P | P2M | NA (only when channel = UPI).
- `counterparty` — Who was on the other side (normalized name).
- `counterparty_category` — Theme/category for analysis (e.g. Food & Dining, Rent & Housing).
- `linked_asset`, `linked_txn_id` — Optional; for pairing events.

**Raw & audit (never edit)**

- `raw_description` — Exact bank/CC text (sacred).
- `source_statement` — Which statement (e.g. HDFC).
- `notes` — Human explanation.

**txn_type enum (v1)**

- SALARY, UPI_EXPENSE, UPI_TRANSFER, CARD_EXPENSE, CARD_PAYMENT, INVESTMENT_BUY, INVESTMENT_SELL, BROKER_FEE, TAX, TRANSFER_INTERNAL, LOAN_INSURANCE_PAYMENT, INCOME_OTHER, EXPENSE_OTHER, SELF_TRANSFER, etc.

Rules:

- One row = one economic intent.
- Credit card **swipe** = expense event (no cash movement); **bill payment** = cash settlement; both in same table; different computations use different subsets via `txn_type`.
- Transactions immutable; corrections = new rows.
- Add `currency` column; default INR.

---

## 3. HDFC statement format (raw)

Columns (from user’s message in chat):

- **Date** — Transaction date.
- **Narration** — Full description (this becomes `raw_description`).
- **Chq./Ref.No.** — Reference number.
- **Value Dt** — Value date (ignored for v1; use Date as posted date).
- **Withdrawal Amt.** — Debit amount.
- **Deposit Amt.** — Credit amount.
- **Closing Balance** — Balance after txn.

Translation:

- If Deposit filled → direction = INFLOW, amount = Deposit.
- If Withdrawal filled → direction = OUTFLOW, amount = Withdrawal.
- `txn_date` = Date.
- `raw_description` = Narration.
- account_id = same for whole file (e.g. HDFC_SALARY).
- Channel/txn_type/counterparty from Narration via rules or AI.

---

## 4. Credit cards

- Include **both** swipes and bill payments in the transactions table.
- **Expense / categorization** → use CARD_EXPENSE (and UPI_EXPENSE, etc.).
- **Cash flow / liquidity** → use only real cash movements (e.g. CARD_PAYMENT from bank account); exclude CARD_EXPENSE from cash.
- No “include_in_X” column; logic lives in queries/formulas.

---

## 5. AI-assisted classification (Gemini in Sheets)

User used Google Sheets `=AI()` with prompts to fill:

- `txn_type`
- `channel`
- `upi_type`
- `counterparty` (few-shot prompt for Indian UPI/bank noise)
- `counterparty_category` (theme/category; input = “txn_type + counterparty” or similar)

Recommendation from chat: keep AI output in one column and a “_final” override column (e.g. `counterparty_ai` / `counterparty_final`) for auditability.

---

## 6. SELF_TRANSFER and naming

- Transfers between own accounts (e.g. HDFC → ICICI, or name/MEICICI in narration) → SELF_TRANSFER.
- Account naming: consistent labels (e.g. HDFC_SALARY, ICICI_INVEST, CC_HDFC); never rename later.

---

## 7. What was deferred

- Investment platforms (CAS, broker ledger): not needed for Phase 1 transactions table; bank statements sufficient for “did money move”; semantic detail (e.g. which MF) can be layered later.
- FX conversion, multi-currency reporting: column exists, default INR; no logic yet.
- Perfect merchant extraction, perfect P2P vs P2M: v1 acceptable; refine later.

---

## 8. Where the chat left off

- Full canonical schema defined and mapped from HDFC sample rows.
- Gemini prompts given for txn_type, channel, upi_type, counterparty, and counterparty_category (with few-shot examples).
- Recommendation to add category_ai / category_final columns.
- Next steps suggested: collapse categories into Needs/Wants/Savings, subscription detector, or anomaly flags—but user moved to Cursor for a more “agentic” setup instead of continuing in Sheets.

---

*Source: `docs/personal-data/prior_ChatGPT_chat.md`.*
