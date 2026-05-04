/**
 * api.ts — typed HTTP client for the Arth FastAPI backend.
 *
 * Architecture:
 *   - Two low-level helpers: get<T>() and patch<T>()
 *   - Typed functions on top that map to specific backend endpoints
 *   - All functions are async and return typed Promises
 *
 * The React Query hooks in src/hooks/ call these functions.
 * Components usually go through a hook; ``streamOnboardingDiscover`` is the one
 * exception that uses ``fetch`` directly so we can read an NDJSON body incrementally.
 *
 * Base URL is read from NEXT_PUBLIC_API_URL (see `api-base.ts`).
 * Use NEXT_PUBLIC_API_URL=same-origin when the UI is on a different hostname than
 * the API (e.g. two tunnel URLs) so session cookies stay on the dashboard origin.
 */

import type {
  AccountSummary,
  AuthStatus,
  BarDrilldownChart,
  BulkUpdateRequest,
  BulkUpdateResponse,
  CategoryBreakdown,
  CategoryTrendRow,
  DashboardCategorySeries,
  DateRange,
  Direction,
  ExpenseStackedRow,
  Goal,
  GoalCreate,
  GoalProgressResponse,
  GoalUpdate,
  Holding,
  HoldingDetail,
  HoldingValueUpdate,
  HoldingsListFilters,
  HoldingsSummary,
  BulkInvestmentUpdateRequest,
  InvestmentTxn,
  InvestmentTransactionFilters,
  InvestmentTransactionUpdate,
  InvestmentTrendRow,
  Liability,
  LiabilitySummary,
  LifeEvent,
  LifeEventUpdate,
  MetricsSummary,
  MonthlyTrend,
  NetWorthGranularity,
  NetWorthHistory,
  BatchReturnsResponse,
  NegativeSurplusResponse,
  PortfolioValueTrend,
  PortfolioValueTrendRange,
  PaginatedResponse,
  RecurringPattern,
  RecurringSummary,
  Reminder,
  ReminderCreate,
  RefreshPricesResult,
  ReminderUpdate,
  RemindersStatusResponse,
  DeriveReminderAnchorsResponse,
  SpendCategoryBreakdown,
  TopCounterparty,
  Transaction,
  TransactionFilters,
  TransactionUpdate,
  UploadResponse,
  SimulationParams,
  SimulationResult,
  FromCurrentResponse,
  SurplusResult,
  ScenarioComparison,
  PriorityResult,
  GoalReorderItem,
  OnboardingGapsResponse,
  OnboardingGoalTemplatesResponse,
  OnboardingStateResponse,
  OnboardingPreclassificationSavedResponse,
  OnboardingBackfillSourceRow,
  ClassificationStatsResponse,
} from "@/lib/types";

import { buildApiUrl } from "@/lib/api-base";
import { userMessageFromApiResponseBody } from "@/lib/user-facing-api-error";
import type { ChatSessionDetail, ChatSessionSummary } from "@/lib/chat-types";

// ─────────────────────────────────────────────────────────────────────────────
// Error type
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Thrown by get() and patch() when the server returns a non-2xx status.
 * You can catch this in React Query's onError handlers and inspect .status.
 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Core fetch helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * A record type that only allows values that can appear in a URL query string.
 * undefined and null values are automatically filtered out before building the URL.
 */
type QueryParams = Record<string, string | number | boolean | undefined | null>;

/**
 * Performs a GET request, appends query params, and deserialises the JSON body.
 * Throws ApiError on non-2xx responses.
 * Redirects to /login on 401 (session expired or missing).
 */
async function get<T>(
  path: string,
  params?: QueryParams,
  opts?: { signal?: AbortSignal },
): Promise<T> {
  const url = buildApiUrl(path, params);

  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    signal: opts?.signal,
  });

  if (res.status === 401) {
    // Session expired or cookie missing — redirect to login
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    // Return a promise that never resolves so the calling code doesn't continue
    return new Promise(() => {});
  }

  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw));
  }

  return res.json() as Promise<T>;
}

/**
 * Performs a PATCH request with a JSON body and deserialises the response.
 * Throws ApiError on non-2xx responses.
 * Redirects to /login on 401.
 */
async function patch<T>(
  path: string,
  body: unknown,
  params?: QueryParams,
): Promise<T> {
  const res = await fetch(buildApiUrl(path, params), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    return new Promise(() => {});
  }

  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw));
  }

  return res.json() as Promise<T>;
}

/**
 * Performs a POST request with a JSON body and deserialises the response.
 * Throws ApiError on non-2xx responses.
 * Redirects to /login on 401.
 */
async function post<T>(
  path: string,
  body: unknown,
  params?: QueryParams,
  opts?: { signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(buildApiUrl(path, params), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
    signal: opts?.signal,
  });

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    return new Promise(() => {});
  }

  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw));
  }

  // 204 No Content has no body — return undefined cast to T
  if (res.status === 204) return undefined as unknown as T;

  return res.json() as Promise<T>;
}

/**
 * Performs a DELETE request.
 * Throws ApiError on non-2xx responses.
 */
async function del(path: string): Promise<void> {
  const res = await fetch(buildApiUrl(path), {
    method: "DELETE",
    credentials: "include",
  });

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    return;
  }

  if (!res.ok && res.status !== 204) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Transaction endpoints  →  /api/transactions
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/transactions
 * Fetches a paginated, filtered list of transactions.
 * Accepts any combination of filters from TransactionFilters.
 */
export function fetchTransactions(
  filters: TransactionFilters = {},
): Promise<PaginatedResponse<Transaction>> {
  return get<PaginatedResponse<Transaction>>(
    "/api/transactions",
    filters as QueryParams,
  );
}

/**
 * GET /api/transactions/:id
 * Fetches a single transaction by its database ID.
 */
export function fetchTransaction(id: number): Promise<Transaction> {
  return get<Transaction>(`/api/transactions/${id}`);
}

/**
 * PATCH /api/transactions/:id
 * Updates user-editable fields on a single transaction.
 * Only send the fields you want to change — the rest are left untouched.
 */
export function updateTransaction(
  id: number,
  update: TransactionUpdate,
): Promise<Transaction> {
  return patch<Transaction>(`/api/transactions/${id}`, update);
}

/**
 * PATCH /api/transactions/bulk
 * Applies the same update to multiple transactions in one request.
 * Useful for "mark all selected as reviewed".
 */
export function bulkUpdateTransactions(
  request: BulkUpdateRequest,
): Promise<BulkUpdateResponse> {
  return patch<BulkUpdateResponse>("/api/transactions/bulk", request);
}

// ─────────────────────────────────────────────────────────────────────────────
// Metrics endpoints  →  /api/metrics  (added in Phase 3b)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/metrics/summary
 * Returns high-level financial totals for a date range.
 * Defaults to the current month if no date range is provided.
 */
export function fetchMetricsSummary(
  dateRange: DateRange = {},
): Promise<MetricsSummary> {
  return get<MetricsSummary>("/api/metrics/summary", dateRange as QueryParams);
}

/**
 * GET /api/metrics/by-category
 * Returns expense (or income) broken down by counterparty_category,
 * sorted by amount descending.
 *
 * @param dateRange  optional date_from / date_to
 * @param direction  "OUTFLOW" (default) or "INFLOW"
 */
export function fetchCategoryBreakdown(
  dateRange: DateRange = {},
  direction: Direction = "OUTFLOW",
): Promise<CategoryBreakdown[]> {
  return get<CategoryBreakdown[]>("/api/metrics/by-category", {
    ...dateRange,
    direction,
  } as QueryParams);
}

/**
 * GET /api/metrics/top-counterparties
 * Returns the top N merchants / payees by total spend.
 *
 * @param dateRange  optional date_from / date_to
 * @param limit      how many to return (default 10)
 */
export function fetchTopCounterparties(
  dateRange: DateRange = {},
  limit = 10,
): Promise<TopCounterparty[]> {
  return get<TopCounterparty[]>("/api/metrics/top-counterparties", {
    ...dateRange,
    limit,
  } as QueryParams);
}

/**
 * GET /api/metrics/monthly-trend
 * Returns month-by-month income / expense / net / savings_rate
 * for the trailing N months.
 *
 * @param months  how many months of history to return (default 12)
 */
export function fetchMonthlyTrend(months = 12): Promise<MonthlyTrend[]> {
  return get<MonthlyTrend[]>("/api/metrics/monthly-trend", {
    months,
  } as QueryParams);
}

/**
 * GET /api/metrics/accounts-summary
 * Returns one row per bank account with totals.
 * No date range filter — always returns lifetime aggregates.
 */
export function fetchAccountsSummary(): Promise<AccountSummary[]> {
  return get<AccountSummary[]>("/api/metrics/accounts-summary");
}

/**
 * GET /api/metrics/negative-surplus-months  (Q11)
 * Returns months where spending exceeded income, plus a deficit total.
 * Default window is 12 months; pass a different value for a longer view.
 */
export function fetchNegativeSurplusMonths(months = 12): Promise<NegativeSurplusResponse> {
  return get<NegativeSurplusResponse>("/api/metrics/negative-surplus-months", {
    months,
  } as QueryParams);
}

// ─────────────────────────────────────────────────────────────────────────────
// Auth endpoints  →  /api/auth
// ─────────────────────────────────────────────────────────────────────────────

/**
 * POST /api/auth/login
 * Sends credentials to FastAPI. On success, FastAPI sets the httpOnly
 * "arth_session" cookie — the browser stores it automatically.
 * Throws ApiError on 401 (wrong credentials).
 */
export async function login(username: string, password: string): Promise<void> {
  const res = await fetch(buildApiUrl("/api/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw) || "Login failed");
  }
}

/**
 * POST /api/auth/logout
 * Tells FastAPI to clear the session cookie. After this, the browser no
 * longer sends the cookie and all API calls will return 401.
 */
export async function logout(): Promise<void> {
  await fetch(buildApiUrl("/api/auth/logout"), {
    method: "POST",
    credentials: "include",
  });
  // Redirect to login page regardless of the response
  window.location.href = "/login";
}

/**
 * GET /api/auth/me
 * Returns who is logged in (username matches ``user_id`` on holdings / goals).
 * Uses the same ``get()`` helper as the rest of the app (401 → redirect to login).
 */
export function fetchAuthMe(): Promise<AuthStatus> {
  return get<AuthStatus>("/api/auth/me");
}

/** GET /api/setup/status — public; used to decide if the setup wizard should run. */
export type SetupStatus = {
  needs_setup: boolean;
  has_users: boolean;
  setup_completed: boolean;
};

/** React Query cache key — invalidate after completing onboarding so the app shell unlocks. */
export const SETUP_STATUS_QUERY_KEY = ["setup-status"] as const;

export function fetchSetupStatus(): Promise<SetupStatus> {
  return get<SetupStatus>("/api/setup/status");
}

/** POST /api/setup/register — first user only (no session required). */
export function registerFirstUser(username: string, password: string): Promise<unknown> {
  return post("/api/setup/register", { username, password });
}

/** POST /api/setup/complete — mark wizard finished (requires session). */
export function completeSetupWizard(): Promise<{ setup_completed: boolean }> {
  return post<{ setup_completed: boolean }>("/api/setup/complete", {});
}

/** POST /api/setup/secrets — store PDF password map (requires session). */
export function saveSetupSecrets(
  secrets_json: Record<string, string>,
): Promise<{ ok: boolean; keys: string[] }> {
  return post<{ ok: boolean; keys: string[] }>("/api/setup/secrets", { secrets_json });
}

/** GET /api/setup/secrets/meta — which keys exist (values never returned). */
export function fetchSetupSecretsMeta(): Promise<{ keys: string[]; has_secrets: boolean }> {
  return get<{ keys: string[]; has_secrets: boolean }>("/api/setup/secrets/meta");
}

// ─────────────────────────────────────────────────────────────────────────────
// Spend category breakdown  →  /api/metrics/by-spend-category  (Phase 4.5c)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/metrics/by-spend-category
 * Returns OUTFLOW spending broken down by NEED / WANT / SAVING / INVESTMENT.
 * Powers the "Spending Breakdown" donut chart on the dashboard.
 */
export function fetchSpendCategoryBreakdown(
  dateRange: DateRange = {},
): Promise<SpendCategoryBreakdown[]> {
  return get<SpendCategoryBreakdown[]>(
    "/api/metrics/by-spend-category",
    dateRange as QueryParams,
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Recurring patterns  →  /api/recurring  (Phase 4.5c)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/recurring/summary
 * Returns aggregate stats: total monthly fixed costs, recurring income, etc.
 */
export function fetchRecurringSummary(): Promise<RecurringSummary> {
  return get<RecurringSummary>("/api/recurring/summary");
}

/**
 * GET /api/recurring
 * Returns a list of recurring patterns, optionally filtered.
 */
export function fetchRecurringPatterns(params?: {
  direction?: "INFLOW" | "OUTFLOW";
  frequency?: string;
  is_active?: boolean;
}): Promise<RecurringPattern[]> {
  return get<RecurringPattern[]>("/api/recurring", params as QueryParams);
}

/**
 * POST /api/recurring/detect
 * Triggers the recurring detection algorithm on the full transaction history.
 */
export function runRecurringDetection(): Promise<{ message: string; created: number; updated: number }> {
  return post<{ message: string; created: number; updated: number }>(
    "/api/recurring/detect",
    {},
  );
}

/**
 * PATCH /api/recurring/{id}
 * Confirm, dismiss, or adjust a recurring pattern.
 */
export function updateRecurringPattern(
  id: number,
  update: { is_confirmed?: boolean; is_active?: boolean; expected_amount?: number },
): Promise<RecurringPattern> {
  return patch<RecurringPattern>(`/api/recurring/${id}`, update);
}

// ─────────────────────────────────────────────────────────────────────────────
// Goals  →  /api/goals  (Phase 4.5d)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/goals
 * Returns all goals for the logged-in user, optionally filtered.
 * (user_id is not a query param — the session determines the user.)
 */
export function fetchGoals(params?: {
  goal_type?: string;
  status?: string;
  tier?: string;
  activation_status?: string;
  funding_mode?: string;
}): Promise<Goal[]> {
  return get<Goal[]>("/api/goals", params as QueryParams);
}

/**
 * GET /api/goals/{id}
 * Returns a single goal with live-computed progress.
 */
export function fetchGoal(id: number): Promise<Goal> {
  return get<Goal>(`/api/goals/${id}`);
}

/**
 * POST /api/goals
 * Create a new financial goal.
 */
export function createGoal(body: GoalCreate): Promise<Goal> {
  return post<Goal>("/api/goals", body);
}

/**
 * PATCH /api/goals/{id}
 * Update mutable fields on a goal (name, target, current_value, status, etc.)
 */
export function updateGoal(id: number, update: GoalUpdate): Promise<Goal> {
  return patch<Goal>(`/api/goals/${id}`, update);
}

/**
 * DELETE /api/goals/{id}
 * Permanently delete a goal.
 */
export function deleteGoal(id: number): Promise<void> {
  return del(`/api/goals/${id}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Simulation sandbox (Sub-Plan H) — /api/simulate, /api/surplus, /api/inflation
// ─────────────────────────────────────────────────────────────────────────────

/** POST /api/simulate — pure projection from JSON params (no DB). */
export function runSimulation(params: SimulationParams): Promise<SimulationResult> {
  return post<SimulationResult>("/api/simulate", params);
}

/** POST /api/simulate/from-current — hydrate params from ACTIVE goals + surplus + inflation. */
export function fetchSimulateFromCurrent(body?: {
  simulation_months?: number;
  surplus_trailing_months?: number;
  as_of_date?: string | null;
}): Promise<FromCurrentResponse> {
  return post<FromCurrentResponse>("/api/simulate/from-current", body ?? {});
}

/** POST /api/simulate/compare — base vs scenario variants. */
export function runSimulationCompare(
  base: SimulationParams,
  variants: SimulationParams[],
): Promise<ScenarioComparison[]> {
  return post<ScenarioComparison[]>("/api/simulate/compare", { base, variants });
}

/** GET /api/surplus — recurring-income-based monthly surplus (Sub-Plan B). */
export function fetchSurplus(params?: {
  user_id?: string;
  months?: number;
}): Promise<SurplusResult> {
  return get<SurplusResult>("/api/surplus", params as QueryParams);
}

/** GET /api/inflation — merged CPI rates + metadata. */
export function fetchInflation(): Promise<Record<string, unknown>> {
  return get<Record<string, unknown>>("/api/inflation");
}

/** GET /api/goals/priorities — system priority scores (optional persist=false to avoid DB writes). */
export function fetchPriorities(persist = true): Promise<PriorityResult> {
  return get<PriorityResult>("/api/goals/priorities", { persist });
}

/** POST /api/goals/reorder — update allocation_priority ranks only. */
export function reorderGoals(goalOrder: GoalReorderItem[]): Promise<unknown> {
  return post<unknown>("/api/goals/reorder", { goal_order: goalOrder });
}

/** GET /api/life-events */
export function fetchLifeEvents(): Promise<LifeEvent[]> {
  return get<LifeEvent[]>("/api/life-events");
}

/** PATCH /api/life-events/{id} — may trigger activation cascade when occurred → true. */
export function updateLifeEvent(
  id: number,
  body: LifeEventUpdate,
): Promise<LifeEvent> {
  return patch<LifeEvent>(`/api/life-events/${id}`, body);
}

// ─────────────────────────────────────────────────────────────────────────────
// Dashboard V2 metrics
// ─────────────────────────────────────────────────────────────────────────────

export function fetchGoalProgress(goalId: number): Promise<GoalProgressResponse> {
  return get<GoalProgressResponse>("/api/metrics/goal-progress", { goal_id: goalId });
}

export function fetchInvestmentTrend(months: number): Promise<InvestmentTrendRow[]> {
  return get<InvestmentTrendRow[]>("/api/metrics/investment-trend", { months });
}

export function fetchExpenseTrendStacked(months: number): Promise<ExpenseStackedRow[]> {
  return get<ExpenseStackedRow[]>("/api/metrics/expense-trend-stacked", { months });
}

export function fetchCategoryTrend(
  series: DashboardCategorySeries,
  months: number,
): Promise<CategoryTrendRow[]> {
  return get<CategoryTrendRow[]>("/api/metrics/category-trend", { series, months });
}

export function fetchTopExpenses(
  threshold = 5000,
  yearMonth?: string,
): Promise<Transaction[]> {
  return get<Transaction[]>("/api/metrics/top-expenses", {
    threshold,
    year_month: yearMonth ?? undefined,
  });
}

export function fetchBarDrilldown(params: {
  chart: BarDrilldownChart;
  month: string;
  series?: DashboardCategorySeries;
}): Promise<Transaction[]> {
  return get<Transaction[]>("/api/metrics/bar-drilldown", {
    chart: params.chart,
    month: params.month,
    series: params.series,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Settings / reminders
// ─────────────────────────────────────────────────────────────────────────────

export function fetchReminders(): Promise<Reminder[]> {
  return get<Reminder[]>("/api/settings/reminders");
}

/** Per-reminder match status for a calendar month (YYYY-MM). */
export function fetchRemindersStatus(
  month: string,
  activeOnly = true,
): Promise<RemindersStatusResponse> {
  return get<RemindersStatusResponse>("/api/settings/reminders/status", {
    month,
    active_only: activeOnly,
  });
}

/** Preview auto-derived description anchors from example transaction IDs. */
export function deriveReminderAnchors(
  transactionIds: number[],
): Promise<DeriveReminderAnchorsResponse> {
  return post<DeriveReminderAnchorsResponse>(
    "/api/settings/reminders/derive-anchors",
    { transaction_ids: transactionIds },
  );
}

export function createReminder(body: ReminderCreate): Promise<Reminder> {
  return post<Reminder>("/api/settings/reminders", body);
}

export function updateReminder(id: number, body: ReminderUpdate): Promise<Reminder> {
  return patch<Reminder>(`/api/settings/reminders/${id}`, body);
}

export function deleteReminder(id: number): Promise<void> {
  return del(`/api/settings/reminders/${id}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Statement upload  →  /api/pipeline/upload  (Phase 4.5d)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * POST /api/pipeline/upload
 * Uploads a bank statement file and triggers the pipeline.
 * Returns a run_id that can be polled via GET /api/pipeline/runs/{id}.
 *
 * @param file      The File object from an <input type="file"> or drag-and-drop
 * @param sourceKey Optional parser key override (e.g. "hdfc_savings")
 */
export async function uploadStatement(
  file: File,
  sourceKey?: string,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const url = buildApiUrl(
    "/api/pipeline/upload",
    sourceKey ? { source_key: sourceKey } : undefined,
  );

  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    body: formData,
    // Don't set Content-Type — let the browser set multipart/form-data with the boundary
  });

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    return new Promise(() => {});
  }

  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw));
  }

  return res.json() as Promise<UploadResponse>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Portfolio / asset layer  →  holdings, investment-transactions, liabilities, prices (F2)
// ─────────────────────────────────────────────────────────────────────────────

/** GET /api/holdings — optional filters match FastAPI list_holdings. */
export function fetchHoldings(
  filters: HoldingsListFilters = {},
): Promise<Holding[]> {
  return get<Holding[]>("/api/holdings", filters as QueryParams);
}

/** GET /api/holdings/summary — net worth, allocation %, concentration. */
export function fetchHoldingsSummary(params?: {
  user_id?: string;
  as_of?: string;
}): Promise<HoldingsSummary> {
  return get<HoldingsSummary>("/api/holdings/summary", params as QueryParams);
}

/** GET /api/holdings/portfolio-value-trend — monthly total portfolio value (holdings only). */
export function fetchPortfolioValueTrend(params?: {
  user_id?: string;
  range?: PortfolioValueTrendRange;
}): Promise<PortfolioValueTrend> {
  return get<PortfolioValueTrend>("/api/holdings/portfolio-value-trend", {
    user_id: params?.user_id,
    range: params?.range ?? "12M",
  } as QueryParams);
}

/** GET /api/holdings/batch-returns — XIRR / return payload for every active holding. */
export function fetchBatchReturns(params?: {
  user_id?: string;
}): Promise<BatchReturnsResponse> {
  return get<BatchReturnsResponse>("/api/holdings/batch-returns", params as QueryParams);
}

/** GET /api/holdings/history — time series for charts (start/end required). */
export function fetchNetWorthHistory(
  startDate: string,
  endDate: string,
  params?: {
    user_id?: string;
    granularity?: NetWorthGranularity;
  },
): Promise<NetWorthHistory> {
  return get<NetWorthHistory>("/api/holdings/history", {
    start_date: startDate,
    end_date: endDate,
    granularity: params?.granularity ?? "monthly",
    user_id: params?.user_id,
  } as QueryParams);
}

/** GET /api/holdings/{id} — single row plus returns dict. */
export function fetchHoldingDetail(
  id: number,
  params?: { user_id?: string },
): Promise<HoldingDetail> {
  return get<HoldingDetail>(`/api/holdings/${id}`, params as QueryParams);
}

/** PATCH /api/holdings/{id} — server allows only MANUAL valuation_method holdings. */
export function updateHoldingValue(
  id: number,
  update: HoldingValueUpdate,
  params?: { user_id?: string },
): Promise<Holding> {
  return patch<Holding>(`/api/holdings/${id}`, update, params as QueryParams);
}

/**
 * GET /api/investment-transactions (paginated).
 * Pass ``user_id`` in filters so results are scoped to that user's holdings (F2.0).
 */
export function fetchInvestmentTransactions(
  filters: InvestmentTransactionFilters = {},
): Promise<PaginatedResponse<InvestmentTxn>> {
  return get<PaginatedResponse<InvestmentTxn>>(
    "/api/investment-transactions",
    filters as QueryParams,
  );
}

/** PATCH /api/investment-transactions/{id} */
export function updateInvestmentTransaction(
  id: number,
  update: InvestmentTransactionUpdate,
): Promise<InvestmentTxn> {
  return patch<InvestmentTxn>(`/api/investment-transactions/${id}`, update);
}

/** PATCH /api/investment-transactions/bulk */
export function bulkUpdateInvestmentTransactions(
  body: BulkInvestmentUpdateRequest,
): Promise<{ updated: number[]; not_found: number[] }> {
  return patch<{ updated: number[]; not_found: number[] }>(
    "/api/investment-transactions/bulk",
    body,
  );
}

/** GET /api/liabilities */
export function fetchLiabilities(params?: {
  user_id?: string;
  is_active?: boolean;
}): Promise<Liability[]> {
  return get<Liability[]>("/api/liabilities", params as QueryParams);
}

/** GET /api/liabilities/summary */
export function fetchLiabilitySummary(params?: {
  user_id?: string;
}): Promise<LiabilitySummary> {
  return get<LiabilitySummary>("/api/liabilities/summary", params as QueryParams);
}

/** POST /api/prices/refresh — optional user_id limits which holdings are refreshed. */
export function refreshPrices(params?: {
  user_id?: string;
}): Promise<RefreshPricesResult> {
  return post<RefreshPricesResult>(
    "/api/prices/refresh",
    {},
    params as QueryParams,
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Onboarding  →  /api/onboarding (Track 2 Phase 4)
// ─────────────────────────────────────────────────────────────────────────────

/** GET /api/onboarding/gaps — month-level coverage holes per source. */
export function fetchOnboardingGaps(): Promise<OnboardingGapsResponse> {
  return get<OnboardingGapsResponse>("/api/onboarding/gaps");
}

/**
 * GET /api/onboarding/goal-templates
 * With ``target_amount`` + ``years`` + ``template_id``, the matching template
 * includes an inflation FV ``preview``; without ``template_id`` the response
 * may include ``headline_preview`` (CPI_GENERAL) instead.
 */
export function fetchOnboardingGoalTemplates(params?: {
  target_amount?: number;
  years?: number;
  template_id?: string;
}): Promise<OnboardingGoalTemplatesResponse> {
  return get<OnboardingGoalTemplatesResponse>("/api/onboarding/goal-templates", params);
}

/** GET /api/onboarding/state */
export function fetchOnboardingState(): Promise<OnboardingStateResponse> {
  return get<OnboardingStateResponse>("/api/onboarding/state");
}

/** GET /api/onboarding/preclassification — raw fields last POSTed (empty until first save). */
export function fetchOnboardingPreclassificationSaved(): Promise<OnboardingPreclassificationSavedResponse> {
  return get<OnboardingPreclassificationSavedResponse>("/api/onboarding/preclassification");
}

/** PATCH /api/onboarding/state */
export function patchOnboardingState(
  body: Partial<{
    current_step: string;
    completed_steps: unknown[];
    discovery_results: Record<string, unknown>;
    backfill_progress: Record<string, unknown>;
  }>,
): Promise<OnboardingStateResponse> {
  return patch<OnboardingStateResponse>("/api/onboarding/state", body);
}

/** One row from ``POST /api/onboarding/discover`` NDJSON ``found`` events. */
export type OnboardingDiscoveryStreamRow = {
  sender_email: string
  display_name: string
  source_type: string
  email_count_estimate: number
  earliest_email_date: string | null
  latest_email_date: string | null
}

/** Parsed NDJSON events from streaming discovery (see ``streamOnboardingDiscover``). */
export type OnboardingDiscoverStreamEvent =
  | { type: "start"; total: number }
  | { type: "found"; index: number; source: OnboardingDiscoveryStreamRow }
  | { type: "done"; discovered_at: string }
  | { type: "error"; detail: string }

/**
 * ``POST /api/onboarding/discover`` returns ``application/x-ndjson``: one JSON object per line
 * (``start`` → many ``found`` → ``done`` or a single ``error``). Calls ``onEvent`` for each line
 * as it arrives so the UI can show per-sender progress.
 *
 * Pass ``signal`` to cancel the HTTP request and stream (e.g. React Strict Mode remount).
 *
 * Throws ``ApiError`` on HTTP failure or when the server sends an ``error`` event.
 * Throws ``DOMException`` with name ``AbortError`` when aborted.
 */
function isAbortLike(e: unknown): boolean {
  if (e == null || typeof e !== "object") return false
  const name = "name" in e ? String((e as { name: unknown }).name) : ""
  return name === "AbortError"
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    throw new DOMException("Aborted", "AbortError")
  }
}

export async function streamOnboardingDiscover(
  onEvent: (event: OnboardingDiscoverStreamEvent) => void,
  options?: { signal?: AbortSignal },
): Promise<void> {
  const signal = options?.signal
  const url = buildApiUrl("/api/onboarding/discover")
  let res: Response
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: "{}",
      signal,
    })
  } catch (e) {
    if (signal?.aborted || isAbortLike(e)) {
      throw new DOMException("Aborted", "AbortError")
    }
    throw e
  }

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`
    return new Promise(() => {})
  }

  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText)
    throw new ApiError(res.status, userMessageFromApiResponseBody(raw))
  }

  const reader = res.body?.getReader()
  if (!reader) {
    throw new ApiError(500, "No response body from discovery.")
  }

  const decoder = new TextDecoder()
  let buffer = ""
  let sawDone = false

  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await reader.read()
      } catch (e) {
        if (signal?.aborted || isAbortLike(e)) {
          throw new DOMException("Aborted", "AbortError")
        }
        throw e
      }
      const { done, value } = chunk
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split("\n")
      buffer = lines.pop() ?? ""
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        let event: OnboardingDiscoverStreamEvent
        try {
          event = JSON.parse(trimmed) as OnboardingDiscoverStreamEvent
        } catch {
          throw new ApiError(500, "Invalid discovery stream from server.")
        }
        onEvent(event)
        if (event.type === "error") {
          throw new ApiError(503, event.detail)
        }
        if (event.type === "done") {
          sawDone = true
        }
      }
    }

    const tail = buffer.trim()
    if (tail) {
      let event: OnboardingDiscoverStreamEvent
      try {
        event = JSON.parse(tail) as OnboardingDiscoverStreamEvent
      } catch {
        throw new ApiError(500, "Invalid discovery stream from server.")
      }
      onEvent(event)
      if (event.type === "error") {
        throw new ApiError(503, event.detail)
      }
      if (event.type === "done") {
        sawDone = true
      }
    }
  } catch (e) {
    if (signal?.aborted || isAbortLike(e)) {
      throw new DOMException("Aborted", "AbortError")
    }
    throw e
  }

  if (!sawDone) {
    throwIfAborted(signal)
    throw new ApiError(500, "Discovery stream ended before completion.")
  }
}

/** GET /api/onboarding/backfill-sources */
export function fetchOnboardingBackfillSources(): Promise<OnboardingBackfillSourceRow[]> {
  return get<OnboardingBackfillSourceRow[]>("/api/onboarding/backfill-sources");
}

/** GET /api/onboarding/unknowns — paged unknown transactions (omit source for all accounts). */
export type OnboardingUnknownTxnBrief = {
  id: number
  source_statement: string | null
  txn_date: string | null
  amount: number
  direction: string
  channel: string | null
  raw_description: string
  txn_type: string | null
  upi_type: string | null
  counterparty: string | null
  counterparty_category: string | null
  spend_category: string | null
}

export type OnboardingUnknownsResponse = {
  source: string | null
  offset: number
  limit: number
  total_transactions: number
  pending_total: number
  transactions: OnboardingUnknownTxnBrief[]
  groups: unknown[]
  unknown_threshold: number
  resume_threshold: number
}

export function fetchOnboardingUnknowns(params: {
  source?: string
  limit?: number
  offset?: number
  signal?: AbortSignal
}): Promise<OnboardingUnknownsResponse> {
  const q = new URLSearchParams()
  if (params.source) q.set("source", params.source)
  if (params.limit != null) q.set("limit", String(params.limit))
  if (params.offset != null) q.set("offset", String(params.offset))
  const qs = q.toString()
  const path = qs ? `/api/onboarding/unknowns?${qs}` : "/api/onboarding/unknowns"
  return get<OnboardingUnknownsResponse>(path, undefined, params.signal ? { signal: params.signal } : undefined)
}

export type OnboardingClassifyItem = {
  txn_id: number
  counterparty: string
  counterparty_category: string
  spend_category?: string | null
  txn_type?: string | null
  upi_type?: string | null
  apply_to_future?: boolean
  merchant_rule_keyword?: string | null
}

export type OnboardingClassifyResponse = {
  status: string
  updated: number
  rules_upserted: number
  contacts_created: number
  remaining_unknowns: number
  resume_threshold: number
  should_resume: boolean
  /** Rows re-tagged in-DB from new merchant keywords (UPI / bank narrations). */
  auto_propagated?: number
}

/** POST /api/onboarding/classify — omit ``source`` to classify rows from mixed ``source_statement`` values. */
export function postOnboardingClassify(body: {
  source?: string | null
  items: OnboardingClassifyItem[]
}): Promise<OnboardingClassifyResponse> {
  return post("/api/onboarding/classify", body)
}

/** POST /api/onboarding/backfill/{source} */
export function postOnboardingBackfillChunk(
  source: string,
  body?: {
    chunk_size?: number;
    resume_after_classification?: boolean;
    resume_from_pause?: boolean;
  },
  opts?: { signal?: AbortSignal },
): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(
    `/api/onboarding/backfill/${encodeURIComponent(source)}`,
    body ?? {},
    undefined,
    opts,
  );
}

/** GET /api/onboarding/backfill/{source}/progress */
export function fetchOnboardingBackfillProgress(
  source: string,
  opts?: { signal?: AbortSignal },
): Promise<{
  source: string;
  status: string;
  emails_found: number;
  emails_processed: number;
  transactions_parsed: number;
  unknowns_pending: number;
  error_message: string | null;
  current_phase: string | null;
}> {
  return get(
    `/api/onboarding/backfill/${encodeURIComponent(source)}/progress`,
    undefined,
    opts,
  );
}

/** POST /api/onboarding/persist-sources — seed scraper DB rows from last discovery scan. */
export function postOnboardingPersistSources(): Promise<{
  ok: boolean;
  senders_processed: number;
  senders_skipped: number;
  accounts_inferred: number;
}> {
  return post("/api/onboarding/persist-sources", {});
}

/** POST /api/onboarding/backfill/{source}/resume — clear paused-only gate. */
export function postOnboardingBackfillResume(source: string): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(
    `/api/onboarding/backfill/${encodeURIComponent(source)}/resume`,
    {},
  );
}

/** POST /api/onboarding/complete */
export function postOnboardingComplete(): Promise<{ ok: boolean; current_step: string }> {
  return post<{ ok: boolean; current_step: string }>("/api/onboarding/complete", {});
}

/** GET /api/onboarding/classifier-status — saved keys only (UserSecrets); ignores server env keys. */
export function fetchOnboardingClassifierStatus(): Promise<{
  llm_model: string;
  has_any_api_key: boolean;
  has_openai_api_key: boolean;
  has_anthropic_api_key: boolean;
  has_google_api_key: boolean;
  unknown_threshold: number;
}> {
  return get("/api/onboarding/classifier-status");
}

/** GET /api/metrics/classification-stats */
export function fetchClassificationStats(): Promise<ClassificationStatsResponse> {
  return get<ClassificationStatsResponse>("/api/metrics/classification-stats");
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent chat  →  /api/chat
// ─────────────────────────────────────────────────────────────────────────────

/**
 * GET /api/chat/ws-ticket
 * Fetches a short-lived token the browser passes as ``?ticket=`` on the
 * WebSocket URL.  The REST call goes through the same-origin proxy (so the
 * httpOnly cookie is sent), but the resulting ticket can be forwarded to
 * the direct FastAPI WebSocket endpoint where the cookie is absent.
 */
export function fetchWsTicket(): Promise<{ ticket: string }> {
  return get<{ ticket: string }>("/api/chat/ws-ticket");
}

/** GET /api/chat/sessions */
export function listChatSessions(params?: {
  limit?: number;
  offset?: number;
}): Promise<ChatSessionSummary[]> {
  return get<ChatSessionSummary[]>("/api/chat/sessions", params as QueryParams);
}

/** GET /api/chat/sessions/{id} */
export function fetchChatSession(sessionId: string): Promise<ChatSessionDetail> {
  return get<ChatSessionDetail>(`/api/chat/sessions/${sessionId}`);
}

/** PATCH /api/chat/sessions/{id} */
export function renameChatSession(
  sessionId: string,
  title: string,
): Promise<ChatSessionSummary> {
  return patch<ChatSessionSummary>(`/api/chat/sessions/${sessionId}`, { title });
}

/** DELETE /api/chat/sessions/{id} — soft archive */
export function archiveChatSession(sessionId: string): Promise<void> {
  return del(`/api/chat/sessions/${sessionId}`);
}
