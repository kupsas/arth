/**
 * api.ts — typed HTTP client for the Arth FastAPI backend.
 *
 * Architecture:
 *   - Two low-level helpers: get<T>() and patch<T>()
 *   - Typed functions on top that map to specific backend endpoints
 *   - All functions are async and return typed Promises
 *
 * The React Query hooks in src/hooks/ call these functions.
 * Components never call fetch() directly — they always go through a hook.
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
  GoalLink,
  GoalLinkCreate,
  GoalProgressResponse,
  GoalTree,
  GoalUpdate,
  Holding,
  HoldingDetail,
  HoldingValueUpdate,
  HoldingsListFilters,
  HoldingsSummary,
  InvestmentTxn,
  InvestmentTransactionFilters,
  InvestmentTrendRow,
  Liability,
  LiabilitySummary,
  LifeEvent,
  LifeEventUpdate,
  MetricsSummary,
  MonthlyTrend,
  NetWorthGranularity,
  NetWorthHistory,
  NegativeSurplusResponse,
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
} from "@/lib/types";

import { buildApiUrl } from "@/lib/api-base";

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
async function get<T>(path: string, params?: QueryParams): Promise<T> {
  const url = buildApiUrl(path, params);

  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    // credentials: "include" is required for the browser to send the
    // httpOnly "arth_session" cookie on cross-port requests (3000 → 8000).
    credentials: "include",
  });

  if (res.status === 401) {
    // Session expired or cookie missing — redirect to login
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    // Return a promise that never resolves so the calling code doesn't continue
    return new Promise(() => {});
  }

  if (!res.ok) {
    // Try to extract a human-readable error message from the response body
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
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
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
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
): Promise<T> {
  const res = await fetch(buildApiUrl(path, params), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });

  if (res.status === 401) {
    window.location.href = `/login?from=${encodeURIComponent(window.location.pathname)}`;
    return new Promise(() => {});
  }

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
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
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
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
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, detail.detail ?? "Login failed");
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
// Goal hierarchy (Phase B.5) — /api/goals/tree, /api/goal-links, /api/life-events
// ─────────────────────────────────────────────────────────────────────────────

/** GET /api/goals/tree — tier buckets + links, goals include live progress. */
export function fetchGoalTree(): Promise<GoalTree> {
  return get<GoalTree>("/api/goals/tree");
}

/** GET /api/goal-links — optional filter by parent or child goal id. */
export function fetchGoalLinks(params?: {
  parent_goal_id?: number;
  child_goal_id?: number;
}): Promise<GoalLink[]> {
  return get<GoalLink[]>("/api/goal-links", params as QueryParams);
}

/** POST /api/goal-links — create edge (server runs cycle detection). */
export function createGoalLink(body: GoalLinkCreate): Promise<GoalLink> {
  return post<GoalLink>("/api/goal-links", body);
}

/** DELETE /api/goal-links/{id} */
export function deleteGoalLink(id: number): Promise<void> {
  return del(`/api/goal-links/${id}`);
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
    const detail = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, detail);
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
 * GET /api/investment-transactions
 * Pass ``user_id`` in filters so results are scoped to that user's holdings (F2.0).
 */
export function fetchInvestmentTransactions(
  filters: InvestmentTransactionFilters = {},
): Promise<InvestmentTxn[]> {
  return get<InvestmentTxn[]>(
    "/api/investment-transactions",
    filters as QueryParams,
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
