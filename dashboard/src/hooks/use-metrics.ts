/**
 * use-metrics.ts — React Query hooks for dashboard metrics.
 *
 * These hooks power the Dashboard page (Phase 3d):
 *   - useMetricsSummary()           → 4 summary cards at the top
 *   - useCategoryBreakdown()        → horizontal bar / donut chart
 *   - useTopCounterparties()        → top merchants table
 *   - useMonthlyTrend()             → income vs expense area chart
 *   - useAccountsSummary()          → per-account breakdown (sidebar / future use)
 *   - useNegativeSurplusMonths()    → deficit months callout (Q11)
 *
 * All hooks accept an optional `dateRange` so the dashboard's date range
 * picker can control every widget from a single piece of state.
 */

"use client";

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import {
  fetchAccountsSummary,
  fetchCategoryBreakdown,
  fetchMetricsSummary,
  fetchMonthlyTrend,
  fetchNegativeSurplusMonths,
  fetchSpendCategoryBreakdown,
  fetchTopCounterparties,
} from "@/lib/api";
import type {
  AccountSummary,
  CategoryBreakdown,
  DateRange,
  Direction,
  MetricsSummary,
  MonthlyTrend,
  NegativeSurplusResponse,
  SpendCategoryBreakdown,
  TopCounterparty,
} from "@/lib/types";

// ─────────────────────────────────────────────────────────────────────────────
// Query key factory
// ─────────────────────────────────────────────────────────────────────────────

/**
 * All metrics query keys live here for easy invalidation.
 * After a bulk-update mutation we can call:
 *   queryClient.invalidateQueries({ queryKey: metricsKeys.all })
 * to force every dashboard widget to refresh.
 */
export const metricsKeys = {
  /** Matches every metrics cache entry */
  all: ["metrics"] as const,

  summary: (dateRange: DateRange) =>
    [...metricsKeys.all, "summary", dateRange] as const,

  categories: (dateRange: DateRange, direction: Direction) =>
    [...metricsKeys.all, "categories", dateRange, direction] as const,

  counterparties: (dateRange: DateRange, limit: number) =>
    [...metricsKeys.all, "counterparties", dateRange, limit] as const,

  trend: (months: number) =>
    [...metricsKeys.all, "trend", months] as const,

  accounts: () =>
    [...metricsKeys.all, "accounts"] as const,

  negativeSurplus: (months: number) =>
    [...metricsKeys.all, "negative-surplus", months] as const,

  spendCategory: (dateRange: DateRange) =>
    [...metricsKeys.all, "spend-category", dateRange] as const,
};

// ─────────────────────────────────────────────────────────────────────────────
// useMetricsSummary — top-level financial totals
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns { total_income, total_expense, total_savings, net, savings_rate, txn_count }
 * for the given date range. savings_rate = invested % of income (Asset Markets outflows).
 *
 * Defaults to the current month when no date range is provided
 * (the backend handles this default).
 *
 * Usage:
 *   const { data: summary, isLoading } = useMetricsSummary(dateRange);
 */
export function useMetricsSummary(
  dateRange: DateRange = {},
  options?: Partial<UseQueryOptions<MetricsSummary>>,
) {
  return useQuery<MetricsSummary>({
    queryKey: metricsKeys.summary(dateRange),
    queryFn: () => fetchMetricsSummary(dateRange),
    // Metrics are a bit more expensive to compute; cache for 2 minutes
    staleTime: 2 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useCategoryBreakdown — per-category totals for charts
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns spending (or income) broken down by category, sorted by amount.
 *
 * @param dateRange   date_from / date_to — controls chart time window
 * @param direction   "OUTFLOW" (default) for expenses, "INFLOW" for income
 *
 * Usage:
 *   const { data: categories } = useCategoryBreakdown(dateRange);
 *   // data is CategoryBreakdown[] | undefined
 */
export function useCategoryBreakdown(
  dateRange: DateRange = {},
  direction: Direction = "OUTFLOW",
  options?: Partial<UseQueryOptions<CategoryBreakdown[]>>,
) {
  return useQuery<CategoryBreakdown[]>({
    queryKey: metricsKeys.categories(dateRange, direction),
    queryFn: () => fetchCategoryBreakdown(dateRange, direction),
    staleTime: 2 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useTopCounterparties — top merchants / payees
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns the top N merchants or payees by total spend.
 *
 * @param dateRange  optional date filter
 * @param limit      how many rows to return (default 10)
 *
 * Usage:
 *   const { data: merchants } = useTopCounterparties(dateRange, 10);
 */
export function useTopCounterparties(
  dateRange: DateRange = {},
  limit = 10,
  options?: Partial<UseQueryOptions<TopCounterparty[]>>,
) {
  return useQuery<TopCounterparty[]>({
    queryKey: metricsKeys.counterparties(dateRange, limit),
    queryFn: () => fetchTopCounterparties(dateRange, limit),
    staleTime: 2 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useMonthlyTrend — trailing N months of income / expense / savings
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns month-by-month totals for the area/line chart on the dashboard.
 * Each item has: { month, income, expense, net, savings_rate }
 *
 * @param months  number of trailing months (default 12)
 *
 * Usage:
 *   const { data: trend } = useMonthlyTrend(12);
 *   // data is MonthlyTrend[] | undefined, sorted oldest → newest
 */
export function useMonthlyTrend(
  months = 12,
  options?: Partial<UseQueryOptions<MonthlyTrend[]>>,
) {
  return useQuery<MonthlyTrend[]>({
    queryKey: metricsKeys.trend(months),
    queryFn: () => fetchMonthlyTrend(months),
    staleTime: 2 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useAccountsSummary — per-account lifetime totals
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns one row per bank account with lifetime inflow/outflow totals.
 * No date range filter — always returns the full history.
 *
 * Used in the filter bar (account_id dropdown) and potentially an
 * accounts summary panel.
 *
 * Usage:
 *   const { data: accounts } = useAccountsSummary();
 */
export function useAccountsSummary(
  options?: Partial<UseQueryOptions<AccountSummary[]>>,
) {
  return useQuery<AccountSummary[]>({
    queryKey: metricsKeys.accounts(),
    queryFn: () => fetchAccountsSummary(),
    // Account list rarely changes; cache aggressively
    staleTime: 10 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useNegativeSurplusMonths — deficit months callout (Q11)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns how many of the last N months had spending > income, plus the list
 * of those specific months and the total cumulative shortfall.
 *
 * Answers: "How many bad months did I have recently — and how bad were they?"
 *
 * @param months  trailing months to scan (default 12)
 *
 * Usage:
 *   const { data: deficit } = useNegativeSurplusMonths(12);
 *   // data.months_with_deficit === 2 → "2 of last 12 months had a deficit"
 */
export function useNegativeSurplusMonths(
  months = 12,
  options?: Partial<UseQueryOptions<NegativeSurplusResponse>>,
) {
  return useQuery<NegativeSurplusResponse>({
    queryKey: metricsKeys.negativeSurplus(months),
    queryFn: () => fetchNegativeSurplusMonths(months),
    staleTime: 5 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useSpendCategoryBreakdown — NEED / WANT / SAVING / INVESTMENT donut
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns OUTFLOW spending broken down by macro category.
 * Feeds the "Spending Breakdown" donut chart.
 *
 * @param dateRange  optional date_from / date_to filter
 */
export function useSpendCategoryBreakdown(
  dateRange: DateRange = {},
  options?: Partial<UseQueryOptions<SpendCategoryBreakdown[]>>,
) {
  return useQuery<SpendCategoryBreakdown[]>({
    queryKey: metricsKeys.spendCategory(dateRange),
    queryFn: () => fetchSpendCategoryBreakdown(dateRange),
    staleTime: 2 * 60 * 1_000,
    ...options,
  });
}
