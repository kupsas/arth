/**
 * use-portfolio.ts — React Query hooks for the Portfolio page (Phase F2).
 *
 * Wraps the typed API helpers in ``@/lib/api`` so UI components never call
 * fetch() directly. Query keys are grouped under ``portfolioKeys`` so a price
 * refresh can invalidate every portfolio-related cache in one shot.
 */

"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  fetchBatchReturns,
  fetchHoldings,
  fetchHoldingsSummary,
  fetchHoldingDetail,
  fetchInvestmentTransactions,
  fetchLiabilities,
  fetchLiabilitySummary,
  fetchNetWorthHistory,
  fetchPortfolioValueTrend,
  refreshPrices,
  updateHoldingValue,
} from "@/lib/api";
import type {
  BatchReturnsResponse,
  Holding,
  HoldingDetail,
  HoldingValueUpdate,
  HoldingsListFilters,
  HoldingsSummary,
  InvestmentTxn,
  InvestmentTransactionFilters,
  Liability,
  LiabilitySummary,
  NetWorthGranularity,
  NetWorthHistory,
  PortfolioValueTrend,
  PortfolioValueTrendRange,
  RefreshPricesResult,
} from "@/lib/types";

/** Centralised keys — use for targeted invalidation or prefetching. */
export const portfolioKeys = {
  all: ["portfolio"] as const,

  holdings: (filters: HoldingsListFilters) =>
    [...portfolioKeys.all, "holdings", filters] as const,

  summary: (asOf?: string, userId?: string) =>
    [...portfolioKeys.all, "summary", asOf ?? "", userId ?? ""] as const,

  netWorthHistory: (
    start: string,
    end: string,
    granularity: NetWorthGranularity,
    userId?: string,
  ) =>
    [...portfolioKeys.all, "history", start, end, granularity, userId ?? ""] as const,

  holdingDetail: (id: number, userId?: string) =>
    [...portfolioKeys.all, "holding", id, userId ?? ""] as const,

  investmentTxns: (filters: InvestmentTransactionFilters) =>
    [...portfolioKeys.all, "investment-txns", filters] as const,

  liabilities: (userId?: string, isActive?: boolean) =>
    [...portfolioKeys.all, "liabilities", userId ?? "", String(isActive)] as const,

  liabilitySummary: (userId?: string) =>
    [...portfolioKeys.all, "liability-summary", userId ?? ""] as const,

  /** Monthly portfolio value series for the holdings area chart (range + user). */
  valueTrend: (range: PortfolioValueTrendRange, userId?: string) =>
    [...portfolioKeys.all, "value-trend", range, userId ?? ""] as const,

  /** Cached batch XIRR / returns map for all holdings. */
  batchReturns: (userId?: string) =>
    [...portfolioKeys.all, "batch-returns", userId ?? ""] as const,
};

export function useHoldings(
  filters: HoldingsListFilters = {},
  options?: Partial<UseQueryOptions<Holding[]>>,
) {
  return useQuery<Holding[]>({
    queryKey: portfolioKeys.holdings(filters),
    queryFn: () => fetchHoldings(filters),
    staleTime: 60_000,
    ...options,
  });
}

export function useHoldingsSummary(
  params?: { user_id?: string; as_of?: string },
  options?: Partial<UseQueryOptions<HoldingsSummary>>,
) {
  return useQuery<HoldingsSummary>({
    queryKey: portfolioKeys.summary(params?.as_of, params?.user_id),
    queryFn: () => fetchHoldingsSummary(params),
    staleTime: 60_000,
    ...options,
  });
}

/**
 * Same data as useHoldingsSummary — name matches the rebuilt holdings page spec (B3
 * extended summary with asset_class_breakdown). Shares the React Query cache.
 */
export function usePortfolioSummary(
  params?: { user_id?: string; as_of?: string },
  options?: Partial<UseQueryOptions<HoldingsSummary>>,
) {
  return useHoldingsSummary(params, options);
}

/** Area chart: GET /api/holdings/portfolio-value-trend with rolling window. */
export function usePortfolioValueTrend(
  range: PortfolioValueTrendRange,
  params?: { user_id?: string },
  options?: Partial<UseQueryOptions<PortfolioValueTrend>>,
) {
  const uid = params?.user_id;
  return useQuery<PortfolioValueTrend>({
    queryKey: portfolioKeys.valueTrend(range, uid),
    queryFn: () =>
      fetchPortfolioValueTrend({ user_id: uid, range }),
    enabled: Boolean(uid),
    staleTime: 60_000,
    ...options,
  });
}

/** One round-trip XIRR / return dict for every active holding (server-cached). */
export function useBatchReturns(
  params?: { user_id?: string },
  options?: Partial<UseQueryOptions<BatchReturnsResponse>>,
) {
  const uid = params?.user_id;
  return useQuery<BatchReturnsResponse>({
    queryKey: portfolioKeys.batchReturns(uid),
    queryFn: () => fetchBatchReturns({ user_id: uid }),
    enabled: Boolean(uid),
    staleTime: 60_000,
    ...options,
  });
}

export function useNetWorthHistory(
  startDate: string,
  endDate: string,
  granularity: NetWorthGranularity,
  params?: { user_id?: string },
  options?: Partial<UseQueryOptions<NetWorthHistory>>,
) {
  return useQuery<NetWorthHistory>({
    queryKey: portfolioKeys.netWorthHistory(
      startDate,
      endDate,
      granularity,
      params?.user_id,
    ),
    queryFn: () =>
      fetchNetWorthHistory(startDate, endDate, {
        granularity,
        user_id: params?.user_id,
      }),
    enabled: Boolean(startDate && endDate),
    staleTime: 60_000,
    ...options,
  });
}

export function useHoldingDetail(
  id: number | null,
  params?: { user_id?: string },
  options?: Partial<UseQueryOptions<HoldingDetail>>,
) {
  return useQuery<HoldingDetail>({
    queryKey: portfolioKeys.holdingDetail(id ?? 0, params?.user_id),
    queryFn: () => fetchHoldingDetail(id!, params),
    enabled: id != null,
    staleTime: 60_000,
    ...options,
  });
}

export function useInvestmentTransactions(
  filters: InvestmentTransactionFilters = {},
  options?: Partial<UseQueryOptions<InvestmentTxn[]>>,
) {
  return useQuery<InvestmentTxn[]>({
    queryKey: portfolioKeys.investmentTxns(filters),
    queryFn: () => fetchInvestmentTransactions(filters),
    staleTime: 60_000,
    ...options,
  });
}

export function useLiabilities(
  params?: { user_id?: string; is_active?: boolean },
  options?: Partial<UseQueryOptions<Liability[]>>,
) {
  return useQuery<Liability[]>({
    queryKey: portfolioKeys.liabilities(params?.user_id, params?.is_active),
    queryFn: () => fetchLiabilities(params),
    staleTime: 60_000,
    ...options,
  });
}

export function useLiabilitySummary(
  params?: { user_id?: string },
  options?: Partial<UseQueryOptions<LiabilitySummary>>,
) {
  return useQuery<LiabilitySummary>({
    queryKey: portfolioKeys.liabilitySummary(params?.user_id),
    queryFn: () => fetchLiabilitySummary(params),
    staleTime: 60_000,
    ...options,
  });
}

type RefreshPricesVars = { user_id?: string } | undefined;

type UpdateHoldingVars = {
  id: number;
  body: HoldingValueUpdate;
  user_id?: string;
};

/**
 * PATCH /api/holdings/{id} — server only applies to MANUAL valuation rows.
 * Invalidates portfolio cache on success.
 */
export function useUpdateHoldingValue(
  options?: UseMutationOptions<Holding, Error, UpdateHoldingVars>,
) {
  const queryClient = useQueryClient();
  const { onSuccess: userOnSuccess, ...rest } = options ?? {};
  return useMutation({
    ...rest,
    mutationFn: ({ id, body, user_id }) =>
      updateHoldingValue(id, body, user_id ? { user_id } : undefined),
    onSuccess: async (data, variables, onMutateResult, context) => {
      await queryClient.invalidateQueries({ queryKey: portfolioKeys.all });
      await userOnSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

/**
 * Triggers POST /api/prices/refresh; on success invalidates all ``portfolioKeys``
 * queries so holdings / summary pick up new marks.
 */
export function useRefreshPrices(
  options?: UseMutationOptions<
    RefreshPricesResult,
    Error,
    RefreshPricesVars
  >,
) {
  const queryClient = useQueryClient();
  const { onSuccess: userOnSuccess, ...rest } = options ?? {};
  return useMutation({
    ...rest,
    mutationFn: (vars?: RefreshPricesVars) => refreshPrices(vars),
    onSuccess: async (data, variables, onMutateResult, context) => {
      await queryClient.invalidateQueries({ queryKey: portfolioKeys.all });
      await userOnSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
