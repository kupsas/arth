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

  classification_source?: string | null;
  /** HIGH | MEDIUM | LOW — email-ingest review heuristic */
  review_confidence?: string | null;

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
// Auth — mirrors AuthStatusResponse in api/routes/auth.py
// ─────────────────────────────────────────────────────────────────────────────

/** GET /api/auth/me — session check without reading the httpOnly cookie in JS. */
export interface AuthStatus {
  authenticated: boolean;
  username: string | null;
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
  review_confidence?: string;
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

/** How automatic progress is evaluated (EXPENSE_LIMIT + INVESTMENT on dashboard). */
export type ProgressCadence = "MONTHLY" | "ANNUAL";

/**
 * Goal pyramid tier (Phase B). Stored uppercase on the API; list filter uses same values.
 */
/** L1–L4 replace legacy VISION…OPERATIONAL; API may still accept legacy on write. */
export type GoalTier =
  | "L1"
  | "L2"
  | "L3"
  | "L4"
  | "VISION"
  | "STRATEGY"
  | "TACTIC"
  | "OPERATIONAL";

export type GoalTimeHorizon =
  | "MONTHLY"
  | "QUARTERLY"
  | "ANNUAL"
  | "MULTI_YEAR"
  | "DECADE";

export type GoalFundingMode =
  | "ACCUMULATION"
  | "CONSTRAINT"
  | "EVENT"
  | "MAINTENANCE";

/**
 * Lifecycle state for when a goal is "in play" in the pyramid — separate from
 * computed progress percentage.
 */
export type GoalActivationStatus = "PENDING" | "ACTIVE" | "COMPLETED";

export type SensitivityToReturns = "LOW" | "MEDIUM" | "HIGH";

/** From `resolve_goal_inflation` — category vs headline CPI EMA. */
export interface GoalInflationResolution {
  annual_pct: number;
  category?: string | null;
  method?: string;
  label?: string;
  detail?: string;
}

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
  chart_key: string | null;
  /** Defaults to MONTHLY when omitted (older API responses). */
  progress_cadence?: ProgressCadence;
  user_id: string;
  /** Set when this goal was created as a decomposition child of another goal. */
  parent_goal_id?: number | null;
  current_value: number | null;    // manually entered
  notes: string | null;
  /** Phase B — optional on legacy rows; API returns null when unset. */
  pyramid_id?: string | null;
  tier?: string | null;
  time_horizon?: string | null;
  funding_mode?: string | null;
  activation_status?: string | null;
  /** DSL: e.g. goal:S4:completed AND event:child_born */
  activation_condition?: string | null;
  monthly_allocation?: number | null;
  allocation_priority?: number | null;
  interruptible?: boolean | null;
  sensitivity_to_returns?: string | null;
  /** Goals architecture V2 — optional until backfilled */
  goal_class?: string | null;
  recurrence_amount?: number | null;
  recurrence_frequency?: string | null;
  recurrence_start?: string | null;
  recurrence_end?: string | null;
  goal_specific_inflation_rate?: number | null;
  expected_return_rate?: number | null;
  starting_balance?: number | null;
  system_priority_score?: number | null;
  goal_subtype?: string | null;
  /** Present when goal was loaded with session (GET list/detail). */
  inflation_resolution?: GoalInflationResolution | null;
  // Computed progress (live from DB)
  computed_current_value: number;
  computed_percentage: number;     // 0–100+
  created_at: string;
  updated_at: string;
}

export interface GoalCreate {
  name: string;
  goal_type: GoalType;
  target_amount?: number;
  target_date?: string;
  target_metric?: string | null;
  priority?: number;
  linked_layer?: number;
  linked_category?: string;
  chart_key?: string | null;
  progress_cadence?: ProgressCadence | null;
  current_value?: number;
  notes?: string;
  pyramid_id?: string | null;
  tier?: string | null;
  time_horizon?: string | null;
  funding_mode?: string | null;
  activation_status?: string | null;
  activation_condition?: string | null;
  monthly_allocation?: number | null;
  allocation_priority?: number | null;
  interruptible?: boolean | null;
  sensitivity_to_returns?: string | null;
  goal_class?: string | null;
  recurrence_amount?: number | null;
  recurrence_frequency?: string | null;
  recurrence_start?: string | null;
  recurrence_end?: string | null;
  goal_specific_inflation_rate?: number | null;
  expected_return_rate?: number | null;
  starting_balance?: number | null;
  goal_subtype?: string | null;
}

export interface GoalUpdate {
  name?: string;
  target_amount?: number | null;
  target_date?: string | null;
  target_metric?: string | null;
  priority?: number;
  linked_category?: string | null;
  chart_key?: string | null;
  progress_cadence?: ProgressCadence | null;
  current_value?: number | null;
  notes?: string | null;
  pyramid_id?: string | null;
  tier?: string | null;
  time_horizon?: string | null;
  funding_mode?: string | null;
  activation_status?: string | null;
  activation_condition?: string | null;
  monthly_allocation?: number | null;
  allocation_priority?: number | null;
  interruptible?: boolean | null;
  sensitivity_to_returns?: string | null;
  /** Goals architecture V2 — mirrors api/routes/goals.py GoalUpdate */
  goal_class?: string | null;
  recurrence_amount?: number | null;
  recurrence_frequency?: string | null;
  recurrence_start?: string | null;
  recurrence_end?: string | null;
  goal_specific_inflation_rate?: number | null;
  expected_return_rate?: number | null;
  starting_balance?: number | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Simulation sandbox (Sub-Plan H) — mirrors api/services/simulation.py
// ─────────────────────────────────────────────────────────────────────────────

/** POINT_IN_TIME | RECURRING_CASH_FLOW */
export type SimulationGoalClass =
  | "POINT_IN_TIME"
  | "RECURRING_CASH_FLOW";

export interface OneTimeEvent {
  amount: number;
  date: string;
  description?: string;
}

/** Sandbox goal row — mirrors SimulationGoal (JSON uses ISO dates). */
export interface SimulationGoal {
  id?: number | null;
  /**
   * Stable React list key for hypothetical rows (`id == null`). Never sent to the engine
   * (API ignores extra fields); avoids remounting the row when `name` or sort order changes.
   */
  client_row_id?: string;
  name: string;
  goal_class: SimulationGoalClass | string;
  target_amount?: number | null;
  target_date?: string | null;
  starting_balance?: number;
  allocation_priority?: number;
  expected_return_rate?: number;
  /** Annual %; null → resolve from goal_subtype + map (same as API) */
  inflation_rate?: number | null;
  inflation_category?: string | null;
  inflation_method?: string | null;
  inflation_label?: string | null;
  recurrence_amount?: number | null;
  recurrence_frequency?: string | null;
  recurrence_start?: string | null;
  recurrence_end?: string | null;
  goal_subtype?: string | null;
}

export interface SimulationParams {
  goals: SimulationGoal[];
  monthly_surplus: number;
  salary_growth_rate?: number;
  general_inflation_rate?: number;
  simulation_months?: number;
  one_time_inflows?: OneTimeEvent[];
  one_time_outflows?: OneTimeEvent[];
  as_of_date?: string | null;
}

export interface MonthlySnapshot {
  month: string;
  cumulative_value: number;
  monthly_contribution: number;
  monthly_return: number;
  target_at_month?: number | null;
  /** Engine amortized need this month (PIT dynamic PMT, recurring monthly need). */
  monthly_need?: number | null;
}

export interface GoalProjection {
  goal_id: number | null;
  goal_name: string;
  monthly_allocation: number;
  projected_completion_date: string | null;
  /** POINT_IN_TIME: corpus at deadline / inflated target × 100 (uncapped). */
  projected_completion_pct?: number | null;
  corpus_at_deadline?: number | null;
  inflation_adjusted_target_at_deadline?: number | null;
  shortfall_at_deadline?: number | null;
  /** RECURRING_CASH_FLOW: periods that met need / total billable periods × 100. */
  periods_met_pct?: number | null;
  worst_period_deficit?: number | null;
  projected_final_amount: number;
  shortfall: number;
  monthly_trajectory: MonthlySnapshot[];
  /** RECURRING: billing periods with positive need (chunked by recurrence frequency). */
  periods_total?: number | null;
  /** RECURRING: periods where contribution sum >= 95% of need sum. */
  periods_funded?: number | null;
  /** RECURRING: periods_funded / periods_total. */
  funding_rate?: number | null;
  /** RECURRING: sum of monthly_contribution over the trajectory. */
  total_contributed?: number | null;
  /** RECURRING: sum of monthly_need over the trajectory. */
  total_needed?: number | null;
}

export interface CascadeEvent {
  month: string;
  completed_goal: string;
  freed_surplus: number;
  beneficiary_goals: string[];
}

export interface MonthlyNetWorth {
  month: string;
  total_value: number;
  total_contributions: number;
  total_returns: number;
  /** Investable surplus for that month (before allocation); equals sum of goal rows + unallocated. */
  monthly_surplus_pool?: number;
  /** Surplus not placed on any goal after allocation rules. */
  unallocated_surplus?: number;
}

export interface SimulationResult {
  projections: GoalProjection[];
  surplus_allocation: Record<string, number>;
  total_surplus_allocated: number;
  unallocated_surplus: number;
  cascade_events: CascadeEvent[];
  net_worth_projection: MonthlyNetWorth[];
  warnings: string[];
}

export interface GoalDelta {
  goal_name: string;
  base_completion?: string | null;
  variant_completion?: string | null;
  base_progress_pct?: number | null;
  variant_progress_pct?: number | null;
  months_shifted?: number | null;
}

export interface ScenarioComparison {
  scenario_name: string;
  changes_from_base: Record<string, unknown>;
  result: SimulationResult;
  deltas: GoalDelta[];
}

/** GET /api/surplus — mirrors SurplusResult */
export interface SurplusMonthDetail {
  month: string;
  income: number;
  expense_category_filtered: number;
  expense_need: number;
  expense_want: number;
  surplus_path_a: number;
  surplus_path_b: number;
}

export interface SurplusResult {
  user_id: string;
  monthly_income: number;
  monthly_expense_baseline: number;
  monthly_surplus: number;
  surplus_path_a: number;
  surplus_path_b: number;
  computation_method: string;
  /** Plain-language companion to ``computation_method`` from the API. */
  computation_method_label: string;
  months_analyzed: number;
  month_details: SurplusMonthDetail[];
  recurring_income_patterns: Record<string, unknown>[];
  warnings: string[];
}

/** POST /api/simulate/from-current */
export interface FromCurrentResponse {
  params: SimulationParams;
  meta: Record<string, unknown>;
  result: SimulationResult;
}

export interface PriorityBreakdown {
  time_pressure: number;
  consequence_severity: number;
  feasibility_urgency: number;
  asset_alignment: number;
}

export interface GoalPriorityRow {
  goal_id: number;
  goal_name: string;
  priority_score: number;
  suggested_rank: number;
  breakdown: PriorityBreakdown;
  explanation: string;
  needs_revision: boolean;
}

export interface PriorityResult {
  user_id: string;
  priorities: GoalPriorityRow[];
  monthly_surplus: number;
  active_goal_count: number;
  computed_at: string;
}

export interface GoalReorderItem {
  goal_id: number;
  allocation_priority: number;
}

/** GET /api/life-events — milestones referenced by activation_condition event:… atoms. */
export interface LifeEvent {
  id: number;
  event_key: string;
  occurred: boolean;
  occurred_date: string | null;
  user_id: string;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface LifeEventUpdate {
  occurred?: boolean;
  occurred_date?: string | null;
  notes?: string | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Dashboard V2 — metrics helpers
// ─────────────────────────────────────────────────────────────────────────────

export interface GoalProgressAdherenceMonth {
  month: string;
  hit: boolean | null;
  /** Net investment (INVESTMENT) or spend in scope (EXPENSE_LIMIT) for that month. */
  amount?: number | null;
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
  progress_cadence?: ProgressCadence;
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
  | "gifts"
  | "shopping"
  | "transport"
  | "travel";

export type BarDrilldownChart =
  | "investment_purchase"
  | "investment_sale"
  | "investment_month"
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
  /** DB-backed mapping: expense transactions that define payee + amount fingerprint. */
  example_transaction_ids: number[];
  /** True if some stored example IDs no longer exist (re-pick in Settings). */
  examples_stale: boolean;
  /**
   * Substrings matched case-insensitively against raw_description / ref_number.
   * ANY match counts (OR). Usually auto-derived from examples; edit as comma-separated text.
   */
  description_match_anchors: string[];
  /** Examples exist but no anchors — add comma-separated match text for accuracy. */
  suggest_manual_anchors: boolean;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface ReminderCreate {
  name: string;
  due_day_of_month: number;
  amount?: number | null;
  counterparty_category?: string | null;
  example_transaction_ids?: number[] | null;
  description_match_anchors?: string[] | null;
  is_active?: boolean;
}

export interface ReminderUpdate {
  name?: string;
  due_day_of_month?: number;
  amount?: number | null;
  counterparty_category?: string | null;
  example_transaction_ids?: number[] | null;
  description_match_anchors?: string[] | null;
  is_active?: boolean;
}

/** POST /api/settings/reminders/derive-anchors */
export interface DeriveReminderAnchorsResponse {
  anchors: string[];
  ok: boolean;
}

/** One row from GET /api/settings/reminders/status */
export interface ReminderMatchedTxn {
  id: number;
  txn_date: string;
  amount: number;
  counterparty: string | null;
}

export interface ReminderMonthStatus {
  reminder_id: number;
  has_mapping: boolean;
  examples_stale: boolean;
  matched_this_month: boolean;
  matched_transactions: ReminderMatchedTxn[];
  unmapped_reason: string | null;
}

export interface RemindersStatusResponse {
  month: string;
  items: ReminderMonthStatus[];
}

// ─────────────────────────────────────────────────────────────────────────────
// Statement upload (Phase 4.5d)
// ─────────────────────────────────────────────────────────────────────────────

export type StatementUploadOutcome =
  | "success"
  | "type_picker"
  | "account_picker"
  | "no_match"
  | "no_source"
  | "needs_password";

/** One row from POST /api/pipeline/upload for picker UIs */
export interface StatementUploadOption {
  source_type?: string | null;
  source_key?: string | null;
  label: string;
}

/** POST /api/pipeline/upload — content-based detection + disambiguation */
export interface StatementUploadResult {
  outcome: StatementUploadOutcome;
  message: string;
  run_id?: number | null;
  source_key?: string | null;
  contact_prompt?: boolean;
  /** When outcome is needs_password: user entered wrong pdf_password */
  password_invalid?: boolean;
  type_options?: StatementUploadOption[] | null;
  account_options?: StatementUploadOption[] | null;
}

/** POST /api/pipeline/upload/holdings — portfolio CSV/PDF */
export interface HoldingUploadResult {
  outcome: "success" | "type_picker" | "no_match" | "needs_password";
  message: string;
  contact_prompt?: boolean;
  password_invalid?: boolean;
  import_stats?: Record<string, unknown> | null;
  type_options?: StatementUploadOption[] | null;
}

/** @deprecated Use StatementUploadResult */
export type UploadResponse = StatementUploadResult;

// ─────────────────────────────────────────────────────────────────────────────
// Portfolio / asset layer (Phase F2) — mirrors holdings, investment txns,
// liabilities, and prices routes in api/routes/*.py
// ─────────────────────────────────────────────────────────────────────────────

/** Same string values as ``pipeline.models.AssetClass`` (holdings API). */
export type PortfolioAssetClass =
  | "EQUITY"
  | "MUTUAL_FUND"
  | "FD"
  | "PPF"
  | "NPS"
  | "SAVINGS"
  | "GOLD"
  | "SOVEREIGN_GOLD_BOND"
  | "REAL_ESTATE"
  | "ESOP"
  | "OTHER";

/** How a holding's mark is produced — mirrors ``ValuationMethod`` on the API. */
export type HoldingValuationMethod = "MARKET_PRICE" | "FIXED_RETURN" | "MANUAL";

/** Time-to-liquidity bucket for a holding. */
export type HoldingLiquidityClass =
  | "INSTANT"
  | "T_PLUS_1"
  | "T_PLUS_3"
  | "WEEKS"
  | "ILLIQUID";

/** Investment ledger line type — mirrors ``InvestmentTxnType`` in the pipeline. */
export type InvestmentLedgerTxnType =
  | "BUY"
  | "SELL"
  | "DIVIDEND"
  | "SIP"
  | "SWITCH_IN"
  | "SWITCH_OUT";

/**
 * India listed-equity LT/ST split at CMP — FIFO lots, >12 calendar months = long-term.
 * Mirrors ``EquityHoldingPeriodSplitOut`` in api/routes/holdings.py.
 */
export interface EquityHoldingPeriodSplit {
  long_term_value_inr: number;
  short_term_value_inr: number;
  unallocated_value_inr: number;
  fifo_quantity_after_txns: number;
  basis_note: string;
}

/**
 * One portfolio row — mirrors ``HoldingOut`` in api/routes/holdings.py.
 * PII (folio / account identifiers) is never included in JSON responses.
 */
export interface Holding {
  id: number | null;
  symbol: string | null;
  name: string;
  quantity: number | null;
  asset_class: PortfolioAssetClass | string;
  account_platform: string;
  valuation_method: HoldingValuationMethod | string;
  current_value: number | null;
  last_valued_date: string | null;
  liquidity_class: HoldingLiquidityClass | string;
  currency: string;
  average_cost_per_unit: number | null;
  current_price_per_unit: number | null;
  principal_amount: number | null;
  interest_rate: number | null;
  maturity_date: string | null;
  compounding_frequency: string | null;
  face_value: number | null;
  coupon_rate: number | null;
  coupon_frequency: string | null;
  fund_type: string | null;
  /** Enriched labels (optional until POST /api/holdings/enrich). */
  sector?: string | null;
  market_cap_class?: string | null;
  fund_category?: string | null;
  fund_house?: string | null;
  /** Earliest date value is accessible (Goals V2 / liquidity). */
  earliest_liquidity_date?: string | null;
  user_id: string;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
  /** B3 — current_value − cost basis when cost is known from the row. */
  overall_gain?: number | null;
  overall_gain_pct?: number | null;
  /** B3 — weight vs full user portfolio (all active holdings). */
  weight_pct?: number | null;
  /** PPF — earliest BUY on linked ledger (drives statutory maturity). */
  ppf_first_contribution_date?: string | null;
  /** PPF — illustrative balance at maturity if no further deposits (see API note). */
  ppf_projected_value_at_maturity?: number | null;
  /** PPF — annual % used for that illustration (live Wikipedia sentence or fallback). */
  ppf_projection_annual_rate_pct?: number | null;
  /** PPF — where the rate came from (always verify vs GOI notification). */
  ppf_projection_rate_note?: string | null;
  /** NPS — illustrative balance at normal exit (60th birthday) when API env has ``DOB``. */
  nps_projected_value_at_normal_exit?: number | null;
  /** NPS — nominal annual % used for that illustration (``NPS_PROJECTION_ANNUAL_RATE_PCT`` or default). */
  nps_projection_annual_rate_pct?: number | null;
  /** NPS — short disclaimer string from the API. */
  nps_projection_note?: string | null;
  /** EQUITY + MARKET_PRICE — ledger FIFO split for LTCG-style buckets. */
  equity_holding_period?: EquityHoldingPeriodSplit | null;
}

/**
 * Return metrics for one holding — shape varies by ``valuation_method``
 * (manual vs fixed_return vs xirr, etc.). See ``compute_returns`` in the API.
 */
export interface HoldingReturns {
  method: string;
  annualized_return?: number | null;
  [key: string]: unknown;
}

/** GET /api/holdings/{id} — holding plus returns breakdown. */
export interface HoldingDetail {
  holding: Holding;
  returns: HoldingReturns;
}

/** Allowed fields for PATCH /api/holdings/{id} (MANUAL valuation only on server). */
export interface HoldingValueUpdate {
  current_value?: number | null;
  last_valued_date?: string | null;
  notes?: string | null;
  earliest_liquidity_date?: string | null;
}

/** Snapshot inside GET /api/holdings/summary → ``net_worth``. */
export interface NetWorthSnapshot {
  total_assets: number;
  total_liabilities: number;
  net_worth: number;
  as_of: string | null;
}

/** Three percentage maps (0–100 of gross assets) from the summary endpoint. */
export interface HoldingsAllocation {
  by_asset_class: Record<string, number>;
  by_liquidity_class: Record<string, number>;
  by_account_platform: Record<string, number>;
}

/** Per asset class — investments table (B3). */
export interface AssetClassPortfolioRow {
  investment: number;
  current_value: number;
  overall_gain: number | null;
  overall_gain_pct: number | null;
}

/** GET /api/holdings/summary */
export interface HoldingsSummary {
  net_worth: NetWorthSnapshot;
  allocation: HoldingsAllocation;
  /** e.g. largest_holding_pct, esop_pct — backend uses float | str | null. */
  concentration: Record<string, number | string | null>;
  /** B3 — sum of holding economic values (Layer 1). */
  total_portfolio_value: number;
  total_cost_basis: number;
  total_overall_gain: number | null;
  total_overall_gain_pct: number | null;
  asset_class_breakdown: Record<string, AssetClassPortfolioRow>;
}

/** GET /api/holdings/portfolio-value-trend */
export interface PortfolioValueTrendPoint {
  date: string;
  total_portfolio_value: number;
  pct_change_vs_prior_month: number | null;
  /** INR per asset class key (e.g. EQUITY); sums to ~total_portfolio_value */
  by_asset_class: Record<string, number>;
}

export interface PortfolioValueTrend {
  range: string;
  granularity: string;
  points: PortfolioValueTrendPoint[];
}

/** Query param for GET /api/holdings/portfolio-value-trend — matches FastAPI ``range``. */
export type PortfolioValueTrendRange = "3M" | "6M" | "12M" | "all";

/** GET /api/holdings/batch-returns — map keyed by holding id string. */
export interface BatchReturnsResponse {
  returns: Record<string, Record<string, unknown>>;
}

/** GET /api/holdings/batch-returns */
export type BatchHoldingReturnsMap = Record<string, Record<string, unknown>>;

/** One point from GET /api/holdings/history. */
export interface NetWorthHistoryPoint {
  date: string;
  net_worth: number;
  total_assets: number;
  total_liabilities: number;
}

export interface NetWorthHistory {
  points: NetWorthHistoryPoint[];
  granularity: string;
}

/** GET /api/investment-transactions — mirrors ``InvestmentTransactionOut``. */
export interface InvestmentTxn {
  id: number | null;
  txn_date: string;
  symbol: string | null;
  txn_type: InvestmentLedgerTxnType | string;
  quantity: number;
  price_per_unit: number;
  total_amount: number;
  account_platform: string;
  holding_id: number | null;
  bank_transaction_id: number | null;
  notes: string | null;
  is_reviewed: boolean;
  source_type: string | null;
  gmail_message_id: string | null;
  created_at: string;
  updated_at: string;
}

/** PATCH /api/investment-transactions/{id} — mirrors InvestmentTransactionUpdate. */
export interface InvestmentTransactionUpdate {
  is_reviewed?: boolean;
  notes?: string | null;
  symbol?: string | null;
  txn_type?: string | null;
  quantity?: number;
  price_per_unit?: number;
  total_amount?: number;
  txn_date?: string;
  holding_id?: number | null;
}

/** PATCH /api/investment-transactions/bulk */
export interface BulkInvestmentUpdateRequest {
  ids: number[];
  update: InvestmentTransactionUpdate;
}

/** GET /api/liabilities/summary */
export interface LiabilitySummary {
  principal_outstanding: number;
  monthly_emi_burden: number;
  debt_to_asset_ratio: number;
  active_count: number;
}

/** GET /api/liabilities — mirrors ``LiabilityOut``. */
export interface Liability {
  id: number | null;
  name: string;
  liability_type: string;
  principal_outstanding: number;
  interest_rate: number;
  emi_amount: number | null;
  tenure_remaining_months: number | null;
  emi_start_date: string | null;
  emi_end_date: string | null;
  user_id: string;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/prices/{symbol}/history rows and ``PricePointOut``. */
export interface PricePoint {
  symbol: string;
  date: string;
  close_price: number;
  source: string;
}

/** POST /api/prices/refresh response. */
export interface RefreshPricesResult {
  as_of: string;
  price_rows_upserted: number;
  holdings_updated: number;
  nse_symbols: string[];
  mf_codes: string[];
  international_yfinance_symbols: string[];
}

/** Query params for GET /api/holdings (matches FastAPI ``list_holdings``). */
export interface HoldingsListFilters {
  user_id?: string;
  asset_class?: string;
  account_platform?: string;
  liquidity_class?: string;
  is_active?: boolean;
  /** When true, API returns archived rows (is_active=false). Default false. */
  include_inactive?: boolean;
}

export type NetWorthGranularity = "daily" | "weekly" | "monthly";

/**
 * Query params for GET /api/investment-transactions.
 * Pass ``user_id`` so the API scopes rows via holding ownership (F2.0 security fix).
 */
/** GET /api/investment-transactions — optional ``flow`` matches server INFLOW/OUTFLOW buckets. */
export type InvestmentFlowFilter = "INFLOW" | "OUTFLOW";

export interface InvestmentTransactionFilters {
  user_id?: string;
  holding_id?: number;
  txn_type?: string;
  symbol?: string;
  /** Substring match on symbol and notes (server-side). */
  search?: string;
  account_platform?: string;
  /** Buy/sip/dividend vs sell/switch-out — see API ``flow`` query param. */
  flow?: InvestmentFlowFilter;
  date_from?: string;
  date_to?: string;
  /** When false, only rows pending human review (email-sourced ledger lines). */
  is_reviewed?: boolean;
  page?: number;
  page_size?: number;
}

/** Plan F2.1 naming — same as ``PortfolioAssetClass``. */
export type AssetClass = PortfolioAssetClass;
/** Plan F2.1 naming — same as ``HoldingValuationMethod``. */
export type ValuationMethod = HoldingValuationMethod;
/** Plan F2.1 naming — same as ``HoldingLiquidityClass``. */
export type LiquidityClass = HoldingLiquidityClass;
/** Plan F2.1 naming — same as ``InvestmentLedgerTxnType``. */
export type InvestmentTxnType = InvestmentLedgerTxnType;

// ── Onboarding (Track 2 Phase 4) ───────────────────────────────────────────

/** One merged stretch of months missing coverage for a source. */
export interface OnboardingGapListItem {
  kind: string;
  period_label: string;
  period_start: string;
  period_end: string;
  reason: string;
}

export interface OnboardingGapReport {
  source: string;
  source_label: string;
  source_type: string;
  expected_cadence: string;
  date_range_start: string;
  date_range_end: string;
  transaction_count: number;
  gaps: OnboardingGapListItem[];
  note?: string;
}

export interface OnboardingGapsResponse {
  generated_at: string;
  reports: OnboardingGapReport[];
}

export interface OnboardingTemplatePreview {
  target_today_in_inr: number;
  horizon_years: number;
  inflation_annual_percent_used: number;
  inflation_fv_inr: number;
  copy: string;
  /** Present on onboarding goal-template previews — lump sum vs recurring wording. */
  preview_mechanism?: "POINT_IN_TIME" | "RECURRING_CASH_FLOW" | string;
}

/** Grouping hints for the onboarding template grid (mirrors API ``template_sections``). */
export interface OnboardingGoalTemplateSection {
  goal_class: string;
  title: string;
  description: string;
}

export interface OnboardingGoalTemplate {
  id: string;
  name: string;
  icon: string;
  default_target_amount_min: number;
  default_target_amount_max: number;
  default_timeframe_years_min: number;
  default_timeframe_years_max: number;
  suggested_priority: number;
  default_expected_return_rate: number;
  goal_type: string;
  goal_class: string;
  goal_subtype: string | null;
  time_horizon: string | null;
  funding_mode: string | null;
  inflation_rate_category: string;
  inflation_rate_label: string;
  inflation_annual_percent: number;
  preview?: OnboardingTemplatePreview;
  recurrence_amount_hint?: number;
  recurrence_frequency?: string;
}

export interface OnboardingGoalTemplatesResponse {
  headline_cpi_annual_percent: number;
  templates: OnboardingGoalTemplate[];
  headline_preview?: OnboardingTemplatePreview;
  /** Shown before a template is picked — explains run-rate vs one-time FV. */
  headline_preview_recurring?: OnboardingTemplatePreview;
  /** Order matches suggested UX: one-time block first, then recurring. */
  template_sections?: OnboardingGoalTemplateSection[];
}

/** GET /api/onboarding/state — persisted wizard snapshot. */
export interface OnboardingStateResponse {
  current_step: string;
  completed_steps: unknown[];
  discovery_results: Record<string, unknown>;
  backfill_progress: Record<string, unknown>;
  /** idle | running | done | error — background persist-sources after discovery */
  persist_sources_status: string;
  created_at: string | null;
  updated_at: string | null;
}

/** GET /api/onboarding/preclassification — last saved identity form fields (wizard resume). */
export interface OnboardingPreclassificationSavedResponse {
  first_name: string;
  last_name: string;
  extra_aliases: string[];
  account_hints: string[];
  family_names: string[];
  friend_names: string[];
}

/** GET /api/onboarding/backfill-sources — ordered pipeline keys from bank config. */
export interface OnboardingBackfillSourceRow {
  source_key: string;
  source_type: string;
}

/** One row inside ``GET /api/onboarding/portfolio-snapshot`` ``top_holdings``. */
export interface OnboardingPortfolioSnapshotHoldingRow {
  id: number | null;
  name: string | null;
  symbol: string | null;
  asset_class: string | null;
  account_platform: string | null;
  quantity: number | null;
  current_value: number;
}

/** GET /api/onboarding/portfolio-snapshot — broker-only holdings rollup for the wizard. */
export interface OnboardingPortfolioSnapshotResponse {
  holding_count: number;
  equity_count: number;
  mf_count: number;
  total_value_inr: number;
  top_holdings: OnboardingPortfolioSnapshotHoldingRow[];
}

/** POST /api/onboarding/portfolio-derive — link ledger rows + ingest derived holdings. */
export interface OnboardingPortfolioDeriveResponse {
  link_stats: Record<string, number | string>;
  derived_equity_positions: number;
  derived_mf_positions: number;
  ingest_inserted: number;
  ingest_updated: number;
  snapshots_upserted: number;
}

/** GET /api/metrics/classification-stats — coarse automation provenance mix. */
export interface ClassificationStatsResponse {
  total_transactions: number;
  rules_pct: number;
  llm_pct: number;
  user_confirmed_pct: number;
  unclassified_pct: number;
  other_pct: number;
}
