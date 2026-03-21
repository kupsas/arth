/**
 * use-transactions.ts — React Query hooks for transaction data.
 *
 * What is React Query?
 *   It's a data-fetching library that handles the boring but hard parts:
 *   loading/error states, caching, background re-fetching, and keeping your
 *   UI in sync with server state. Instead of writing useEffect + useState
 *   yourself, you call a hook and get { data, isLoading, error } back.
 *
 * Query keys:
 *   Every query has a "key" — an array that uniquely identifies the data.
 *   When a key changes (e.g. page number changes), React Query automatically
 *   refetches. Keys are also used to invalidate cached data after mutations.
 *
 * File structure:
 *   - useTransactions(filters)    → paginated list, used by Transactions page
 *   - useTransaction(id)          → single transaction, used by edit panel
 *   - useUpdateTransaction()      → PATCH mutation for single transaction
 *   - useBulkUpdate()             → PATCH mutation for multiple transactions
 */

"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  bulkUpdateTransactions,
  fetchTransaction,
  fetchTransactions,
  updateTransaction,
} from "@/lib/api";
import type {
  BulkUpdateRequest,
  BulkUpdateResponse,
  PaginatedResponse,
  Transaction,
  TransactionFilters,
  TransactionUpdate,
} from "@/lib/types";

// ─────────────────────────────────────────────────────────────────────────────
// Query key factory
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Centralised key factory — all transaction query keys live here.
 * Benefit: if we ever rename a key, there's exactly one place to change it.
 * The nested structure also lets us invalidate "all transaction queries" by
 * calling queryClient.invalidateQueries({ queryKey: txnKeys.all })
 */
export const txnKeys = {
  /** Matches every transaction-related cache entry */
  all: ["transactions"] as const,

  /** Matches any list query (regardless of filters) */
  lists: () => [...txnKeys.all, "list"] as const,

  /** Matches one specific list with these exact filters */
  list: (filters: TransactionFilters) =>
    [...txnKeys.lists(), filters] as const,

  /** Matches any single-transaction query */
  details: () => [...txnKeys.all, "detail"] as const,

  /** Matches one specific transaction by ID */
  detail: (id: number) => [...txnKeys.details(), id] as const,
};

// ─────────────────────────────────────────────────────────────────────────────
// useTransactions — paginated list
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetches a paginated, filtered list of transactions.
 *
 * Usage:
 *   const { data, isLoading, error } = useTransactions({ page: 1, page_size: 50 });
 *   // data is PaginatedResponse<Transaction> | undefined
 *
 * React Query re-fetches automatically whenever `filters` changes,
 * so you just update the filters state and the table updates itself.
 */
export function useTransactions(
  filters: TransactionFilters = {},
  options?: Partial<UseQueryOptions<PaginatedResponse<Transaction>>>,
) {
  return useQuery<PaginatedResponse<Transaction>>({
    queryKey: txnKeys.list(filters),
    queryFn: () => fetchTransactions(filters),
    // Keep previous page data visible while the next page loads (avoids flash)
    placeholderData: (prev) => prev,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useTransaction — single transaction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetches a single transaction by its database ID.
 *
 * Usage:
 *   const { data: txn } = useTransaction(42);
 *
 * The query is disabled if id is undefined/null (useful when a row isn't
 * selected yet — you don't want to fire a request to /api/transactions/undefined).
 */
export function useTransaction(
  id: number | null | undefined,
  options?: Partial<UseQueryOptions<Transaction>>,
) {
  return useQuery<Transaction>({
    queryKey: txnKeys.detail(id ?? 0),
    queryFn: () => fetchTransaction(id!),
    // Don't fire a request if no ID is selected
    enabled: id != null,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useUpdateTransaction — PATCH single transaction
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns a mutation for updating a single transaction.
 *
 * Usage:
 *   const { mutateAsync, isPending } = useUpdateTransaction();
 *   await mutateAsync({ id: 42, update: { is_reviewed: true } });
 *
 * After a successful update, the mutation automatically:
 *   1. Updates the single-transaction cache entry for this ID
 *   2. Invalidates all list queries so the table reflects the change
 */
export function useUpdateTransaction() {
  const queryClient = useQueryClient();

  return useMutation<
    Transaction,
    Error,
    { id: number; update: TransactionUpdate }
  >({
    mutationFn: ({ id, update }) => updateTransaction(id, update),

    onSuccess: (updatedTxn) => {
      // Update the single-transaction cache immediately (no extra network call)
      queryClient.setQueryData<Transaction>(
        txnKeys.detail(updatedTxn.id),
        updatedTxn,
      );

      // Invalidate all list queries — they'll refetch in the background
      void queryClient.invalidateQueries({ queryKey: txnKeys.lists() });
      // Dashboard metrics (charts, goal progress, top expenses, drill-down)
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
      void queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useBulkUpdate — PATCH multiple transactions at once
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns a mutation for updating multiple transactions at once.
 * Primarily used for "Mark all selected as reviewed" in the Transactions page.
 *
 * Usage:
 *   const { mutateAsync, isPending } = useBulkUpdate();
 *   await mutateAsync({ ids: [1, 2, 3], update: { is_reviewed: true } });
 *
 * After success, all list queries are invalidated so the table refreshes.
 */
export function useBulkUpdate() {
  const queryClient = useQueryClient();

  return useMutation<BulkUpdateResponse, Error, BulkUpdateRequest>({
    mutationFn: (request) => bulkUpdateTransactions(request),

    onSuccess: () => {
      // Invalidate all transaction queries — lists AND individual detail caches
      void queryClient.invalidateQueries({ queryKey: txnKeys.all });
    },
  });
}
