/**
 * types.ts — shared TypeScript types for the Arth dashboard.
 *
 * These mirror the Python models in:
 *   - api/models.py          (Transaction, RecurringPattern, Goal SQLModels)
 *   - pipeline/models.py     (enums + CanonicalTransaction)
 *   - api/routes/transactions.py (PaginatedResponse, TransactionUpdate)
 *   - api/routes/metrics.py  (MetricsSummary, CategoryBreakdown, etc.)
 *   - api/routes/recurring.py (RecurringPatternOut, RecurringSummary)
 *   - api/routes/goals.py    (Goal with progress)
 *
 * Design note: enum values are string union types (not TypeScript enums).
 * Reason: TypeScript enums compile to runtime objects and cause issues with
 * tree-shaking + exhaustive checks. String unions give us autocomplete,
 * type-safety, and zero runtime overhead.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Enum string union types  (one-to-one with Python enums in pipeline/models.py)
// ─────────────────────────────────────────────────────────────────────────────

/** Whether money is coming in or going out of the account. */
export type Direction = "INFLOW" | "OUTFLOW";

/**
 * Macro classification of what an OUTFLOW transaction is going towards.
 * NULL for INFLOW (income) and Friends & Family transactions (user must tag manually).
 */
export type SpendCategory = "NEED" | "WANT" | "INVESTMENT";

/**
 * The specific economic nature of a transaction.
 * CARD_PAYMENT = paying your CC bill → excluded from expense totals (self-transfer).
 * CARD_EXPENSE = actual purchase on CC → included in expense totals.
 */
export type TxnType =
  | "BANK_TRANSFER"
  | "CARD_EXPENSE"
  | "CARD_PAYMENT"
  | "EQUITY_PURCHASE"
  | "EQUITY_SALE"
  | "EXPENSE_OTHER"
  | "INCOME_DIVIDEND"
  | "INCOME_OTHER"
  | "INCOME_SALARY"
  | "LOAN_INSURANCE_PAYMENT"
  | "MF_PURCHASE"
  | "MF_SALE"
  | "SELF_TRANSFER"
  | "UPI_EXPENSE"
  | "UPI_TRANSFER";

/** The payment rail / channel used. */
export type Channel = "UPI" | "UPI-LITE" | "BANK" | "CARD" | "BROKER";

/** For UPI transactions: person-to-person, person-to-merchant, etc. */
export type UPIType = "P2P" | "P2M" | "LITE_SELF_FUND" | "NA";

/**
 * High-level spending category assigned by the LLM classifier.
 * These string values match the Python CounterpartyCategory enum exactly.
 */
export type CounterpartyCategory =
  | "Asset Markets"
  | "Entertainment & Events"
  | "Fees, Charges & Interest"
  | "Financial Services, Insurance & Banking"
  | "Food & Dining"
  | "Friends and Family"
  | "Gifts & Personal Transfers"
  | "Healthcare & Pharmacy"
  | "Miscellaneous"
  | "Mobile, OTT & Subscriptions"
  | "Personal Grooming"
  | "Rent & Housing"
  | "Salary & Income"
  | "Self Transfer"
  | "Shopping & E-commerce"
  | "Swiggy"
  | "Transport & Fuel"
  | "Travel & Stay"
  | "Utilities & Internet";

// ─────────────────────────────────────────────────────────────────────────────
// Core entity: Transaction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * A single financial transaction — mirrors the Transaction SQLModel in api/models.py.
 *
 * Date fields are ISO strings (e.g. "2025-03-15") because JSON has no native
 * Date type. Use `new Date(txn.txn_date)` or date-fns to parse them.
 */
export interface Transaction {
  id: number;
  content_hash: string;

  txn_date: string;           // "YYYY-MM-DD"
  account_id: string;
  source_statement: string;

  direction: Direction;
  amount: number;
  currency: string;           // "INR" for all current data

  txn_type: TxnType | null;
  channel: Channel | null;
  upi_type: UPIType | null;
  counterparty: string | null;
  counterparty_category: CounterpartyCategory | null;
  spend_category: SpendCategory | null;

  raw_description: string;
  ref_number: string | null;
  closing_balance: number | null;
  value_date: string | null;  // "YYYY-MM-DD" or null
  notes: string | null;

  is_reviewed: boolean;
  pipeline_run_id: number | null;
  /** When true, row still appears in the table but is omitted from all metrics. */
  exclude_from_analytics?: boolean;
  exclusion_reason?: string | null;
  created_at: string;         // ISO datetime string
  updated_at: string;         // ISO datetime string
}

/**
 * Fields the user is allowed to edit — mirrors TransactionUpdate in
 * api/routes/transactions.py.  All fields are optional (undefined = don't touch).
 */
export interface TransactionUpdate {
  counterparty?: string | null;
  counterparty_category?: CounterpartyCategory | null;
  txn_type?: TxnType | null;
  spend_category?: SpendCategory | null;
  notes?: string | null;
  is_reviewed?: boolean;
  exclude_from_analytics?: boolean;
  exclusion_reason?: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Pagination wrapper
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Generic paginated response — mirrors PaginatedResponse in the backend.
 * T will usually be Transaction, but kept generic so it can wrap anything.
 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;        // total matching rows (across all pages)
  page: number;         // current page (1-indexed)
  page_size: number;    // rows per page
  total_pages: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Bulk update types
// ─────────────────────────────────────────────────────────────────────────────

export interface BulkUpdateRequest {
  ids: number[];
  update: TransactionUpdate;
}

export interface BulkUpdateResponse {
  updated: number[];    // IDs that were successfully updated
  not_found: number[];  // IDs that didn't exist in the DB
}

// ─────────────────────────────────────────────────────────────────────────────
// Query filter types (used by the API client and hooks)
// ─────────────────────────────────────────────────────────────────────────────

/** All available filters for GET /api/transactions */
export interface TransactionFilters {
  date_from?: string;       // "YYYY-MM-DD"
  date_to?: string;         // "YYYY-MM-DD"
  account_id?: string;
  direction?: Direction;
  category?: CounterpartyCategory | string;
  txn_type?: TxnType;
  is_reviewed?: boolean;
  search?: string;          // free-text search on counterparty + raw_description
  page?: number;            // 1-indexed, default 1
  page_size?: number;       // default 50, max 200
  sort_by?: "txn_date" | "amount" | "created_at" | "counterparty";
  sort_order?: "asc" | "desc";
}

/** Date range used for metrics endpoints */
export interface DateRange {
  date_from?: string;  // "YYYY-MM-DD"
  date_to?: string;    // "YYYY-MM-DD"
}

// ─────────────────────────────────────────────────────────────────────────────
// Metrics response types (mirrors /api/metrics/* endpoints added in Phase 3b)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/metrics/summary
 * High-level financial snapshot for a date range.
 *
 * savings_rate = total_savings / income * 100 (what % of income went to investments).
 * total_savings = OUTFLOW to Asset Markets (equities, MFs).
 */
export interface MetricsSummary {
  date_from: string;       // "YYYY-MM-DD" — echoed back from the request (or defaulted)
  date_to: string;         // "YYYY-MM-DD"
  total_income: number;
  total_expense: number;
  total_savings: number;   // OUTFLOW to Asset Markets (investments)
  net: number;
  savings_rate: number;    // 0–100 percentage (e.g. 42.5 = 42.5% invested)
  txn_count: number;
}

/**
 * One row from GET /api/metrics/by-category
 * Sorted by amount descending.
 * category is null when transactions haven't been classified yet.
 */
export interface CategoryBreakdown {
  category: CounterpartyCategory | string | null;
  amount: number;
  percentage: number;    // 0–100 (e.g. 42.3 = 42.3% of total spend)
  txn_count: number;
}

/**
 * One row from GET /api/metrics/top-counterparties
 * Both counterparty and category may be null for unclassified transactions.
 */
export interface TopCounterparty {
  counterparty: string | null;
  category: CounterpartyCategory | string | null;
  amount: number;
  txn_count: number;
}

/**
 * One row from GET /api/metrics/monthly-trend
 * `month` is "YYYY-MM" (e.g. "2025-03").
 *
 * savings_rate = invested % of income (Asset Markets outflows / income).
 * Zero-filled rows are returned for months with no transactions so the
 * frontend can render a smooth chart without gaps.
 */
export interface MonthlyTrend {
  month: string;
  income: number;
  expense: number;
  net: number;
  savings_rate: number;  // 0–100 percentage (invested % of income)
}

/**
 * One row from GET /api/metrics/accounts-summary
 */
export interface AccountSummary {
  account_id: string;
  txn_count: number;
  last_txn_date: string | null;  // "YYYY-MM-DD" or null
  total_inflow: number;
  total_outflow: number;
}

/**
 * One deficit month from GET /api/metrics/negative-surplus-months (Q11)
 * net is always negative here (expense exceeded income that month).
 */
export interface DeficitMonthRow {
  month: string;   // "YYYY-MM"
  income: number;
  expense: number;
  net: number;     // negative value
}

/**
 * GET /api/metrics/negative-surplus-months (Q11)
 * Answers: "How many of my recent months had a spending deficit?"
 *
 * total_deficit is the sum of |net| across all deficit months — a positive number
 * representing how much more was spent than earned across those bad months.
 */
export interface NegativeSurplusResponse {
  months_with_deficit: number;
  total_months: number;
  deficit_months: DeficitMonthRow[];
  total_deficit: number;  // always positive — the cumulative shortfall
}

// ─────────────────────────────────────────────────────────────────────────────
// Spend category breakdown (Phase 4.5c)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One row from GET /api/metrics/by-spend-category
 * spend_category is "NEED" | "WANT" | "SAVING" | "INVESTMENT" | "UNCLASSIFIED"
 */
export interface SpendCategoryBreakdown {
  spend_category: SpendCategory | "UNCLASSIFIED";
  amount: number;
  percentage: number;   // 0–100
  txn_count: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Recurring patterns (Phase 4.5c)
// ─────────────────────────────────────────────────────────────────────────────

export type RecurringFrequency = "WEEKLY" | "MONTHLY" | "QUARTERLY" | "YEARLY";

/** Mirrors RecurringPatternOut from api/routes/recurring.py */
export interface RecurringPattern {
  id: number;
  counterparty: string;
  counterparty_category: CounterpartyCategory | string | null;
  direction: Direction;
  expected_amount: number;
  amount_tolerance: number;
  frequency: RecurringFrequency;
  day_of_month: number | null;
  last_seen_date: string;          // "YYYY-MM-DD"
  next_expected_date: string | null;
  is_active: boolean;
  is_confirmed: boolean;
  match_count: number;
  total_amount: number;
  created_at: string;
  updated_at: string;
}

/** Mirrors RecurringSummary from api/routes/recurring.py */
export interface RecurringSummary {
  total_monthly_fixed_cost: number;
  total_monthly_recurring_income: number;
  active_pattern_count: number;
  patterns_due_this_week: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Goals (Phase 4.5d)
// ─────────────────────────────────────────────────────────────────────────────

export type GoalType =
  | "SAVINGS"
  | "EXPENSE_LIMIT"
  | "EMERGENCY_FUND"
  | "INVESTMENT"
  | "DEBT_PAYOFF"
  | "INSURANCE"
  | "TAX";

export type GoalStatus = "ON_TRACK" | "AT_RISK" | "BEHIND" | "ACHIEVED" | "PAUSED";

/** Mirrors the goal dict returned by api/routes/goals.py */
export interface Goal {
  id: number;
  name: string;
  goal_type: GoalType;
  target_amount: number | null;
  target_date: string | null;      // "YYYY-MM-DD"
  target_metric: string | null;
  priority: number;                 // 1–5
  linked_layer: number;
  linked_category: string | null;
  user_id: string;
  current_value: number | null;    // manually entered
  notes: string | null;
  // Computed progress (live from DB)
  computed_current_value: number;
  computed_percentage: number;     // 0–100+
  status: GoalStatus;
  created_at: string;
  updated_at: string;
}

export interface GoalCreate {
  name: string;
  goal_type: GoalType;
  target_amount?: number;
  target_date?: string;
  priority?: number;
  linked_layer?: number;
  linked_category?: string;
  user_id?: string;
  current_value?: number;
  notes?: string;
}

export interface GoalUpdate {
  name?: string;
  target_amount?: number | null;
  target_date?: string | null;
  priority?: number;
  linked_category?: string | null;
  current_value?: number | null;
  status?: GoalStatus;
  notes?: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Dashboard V2 — metrics helpers
// ─────────────────────────────────────────────────────────────────────────────

export interface GoalProgressAdherenceMonth {
  month: string;
  hit: boolean | null;
}

export interface GoalProgressResponse {
  goal_id: number;
  goal_type: string;
  target_amount: number | null;
  current_value: number;
  purchases?: number | null;
  sales?: number | null;
  net_investment?: number | null;
  adherence: GoalProgressAdherenceMonth[];
}

export interface InvestmentTrendRow {
  month: string;
  purchases: number;
  sales: number;
  net: number;
}

export interface ExpenseStackedRow {
  month: string;
  need: number;
  want: number;
}

export interface CategoryTrendRow {
  month: string;
  amount: number;
}

/** Query param for GET /api/metrics/category-trend */
export type DashboardCategorySeries =
  | "swiggy_instamart"
  | "swiggy_food"
  | "food_and_dining"
  | "shopping"
  | "transport"
  | "travel";

export type BarDrilldownChart =
  | "investment_purchase"
  | "investment_sale"
  | "expense_need"
  | "expense_want"
  | "category";

// ─────────────────────────────────────────────────────────────────────────────
// Reminders (Settings)
// ─────────────────────────────────────────────────────────────────────────────

export interface Reminder {
  id: number;
  user_id: string;
  name: string;
  due_day_of_month: number;
  amount: number | null;
  counterparty_category: string | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ReminderCreate {
  name: string;
  due_day_of_month: number;
  amount?: number | null;
  counterparty_category?: string | null;
  is_active?: boolean;
}

export interface ReminderUpdate {
  name?: string;
  due_day_of_month?: number;
  amount?: number | null;
  counterparty_category?: string | null;
  is_active?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Statement upload (Phase 4.5d)
// ─────────────────────────────────────────────────────────────────────────────

export interface UploadResponse {
  run_id: number;
  source_key: string;
  message: string;
}
