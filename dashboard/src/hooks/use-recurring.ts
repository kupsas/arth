/**
 * use-recurring.ts — React Query hooks for recurring pattern endpoints.
 *
 * Phase 4.5c: Recurring Transaction Detection
 *
 * Hooks:
 *   - useRecurringSummary()    → aggregate stats for the dashboard card
 *   - useRecurringPatterns()   → filterable list of patterns
 */

"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  fetchRecurringPatterns,
  fetchRecurringSummary,
  runRecurringDetection,
  updateRecurringPattern,
} from "@/lib/api";
import type { RecurringPattern, RecurringSummary } from "@/lib/types";

// ─────────────────────────────────────────────────────────────────────────────
// Query key factory
// ─────────────────────────────────────────────────────────────────────────────

export const recurringKeys = {
  all: ["recurring"] as const,
  summary: () => [...recurringKeys.all, "summary"] as const,
  list: (params?: object) => [...recurringKeys.all, "list", params] as const,
};

// ─────────────────────────────────────────────────────────────────────────────
// useRecurringSummary — aggregate stats
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns total monthly fixed costs, total recurring income, active pattern
 * count, and patterns due this week.
 *
 * Usage:
 *   const { data: summary } = useRecurringSummary();
 */
export function useRecurringSummary(
  options?: Partial<UseQueryOptions<RecurringSummary>>,
) {
  return useQuery<RecurringSummary>({
    queryKey: recurringKeys.summary(),
    queryFn: () => fetchRecurringSummary(),
    // Patterns change infrequently — cache for 5 minutes
    staleTime: 5 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useRecurringPatterns — list of patterns
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns a filtered list of recurring patterns.
 *
 * @param params  optional filters: direction, frequency, is_active
 */
export function useRecurringPatterns(
  params?: { direction?: "INFLOW" | "OUTFLOW"; frequency?: string; is_active?: boolean },
  options?: Partial<UseQueryOptions<RecurringPattern[]>>,
) {
  return useQuery<RecurringPattern[]>({
    queryKey: recurringKeys.list(params),
    queryFn: () => fetchRecurringPatterns(params),
    staleTime: 5 * 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useRunDetection — trigger detection (mutation)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Mutation that triggers the full recurring detection algorithm.
 * Invalidates all recurring queries on success so the UI refreshes.
 *
 * Usage:
 *   const { mutate: detect, isPending } = useRunDetection();
 *   detect(); // fires POST /api/recurring/detect
 */
export function useRunDetection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runRecurringDetection(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: recurringKeys.all });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useUpdateRecurringPattern — confirm / dismiss a pattern (mutation)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Mutation to update a recurring pattern (confirm, dismiss, adjust amount).
 * Invalidates all recurring queries on success.
 */
export function useUpdateRecurringPattern() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      update,
    }: {
      id: number;
      update: { is_confirmed?: boolean; is_active?: boolean; expected_amount?: number };
    }) => updateRecurringPattern(id, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: recurringKeys.all });
    },
  });
}
