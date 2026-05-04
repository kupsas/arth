/**
 * use-goals.ts — React Query hooks for the Goals endpoints.
 *
 * Phase 4.5d: Goals Table + API
 * Phase B.5: Life events
 *
 * Hooks:
 *   - useGoals()          → list of goals with computed progress
 *   - useGoal(id)         → single goal
 *   - useLifeEvents()     → life-event milestones (GET /api/life-events)
 *   - useCreateGoal()     → create mutation
 *   - useUpdateGoal()     → update mutation
 *   - useDeleteGoal()     → delete mutation
 *   - useUpdateLifeEvent()→ patch life event (may activate pending goals)
 */

"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import {
  createGoal,
  deleteGoal,
  fetchGoal,
  fetchGoals,
  fetchLifeEvents,
  updateGoal,
  updateLifeEvent,
} from "@/lib/api";
import type {
  Goal,
  GoalCreate,
  GoalUpdate,
  LifeEvent,
  LifeEventUpdate,
} from "@/lib/types";

// ─────────────────────────────────────────────────────────────────────────────
// Query key factory
// ─────────────────────────────────────────────────────────────────────────────

export const goalKeys = {
  all: ["goals"] as const,
  list: (params?: object) => [...goalKeys.all, "list", params] as const,
  detail: (id: number) => [...goalKeys.all, "detail", id] as const,
};

export const lifeEventKeys = {
  all: ["life-events"] as const,
};

function invalidateGoalRelatedCaches(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: goalKeys.all });
  queryClient.invalidateQueries({ queryKey: lifeEventKeys.all });
}

// ─────────────────────────────────────────────────────────────────────────────
// useGoals — list all goals
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Fetches all goals for the session user, optionally filtered by type, status, tier, etc.
 *
 * Each goal includes computed progress (current_value, percentage, status)
 * calculated live from the transaction DB on the backend.
 *
 * Usage:
 *   const { data: goals } = useGoals();
 */
export function useGoals(
  params?: {
    goal_type?: string;
    status?: string;
    tier?: string;
    activation_status?: string;
    funding_mode?: string;
  },
  options?: Partial<UseQueryOptions<Goal[]>>,
) {
  return useQuery<Goal[]>({
    queryKey: goalKeys.list(params),
    queryFn: () => fetchGoals(params),
    // Goals progress changes daily — 1 minute stale time for freshness
    staleTime: 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useLifeEvents — activation DSL milestones
// ─────────────────────────────────────────────────────────────────────────────

export function useLifeEvents(options?: Partial<UseQueryOptions<LifeEvent[]>>) {
  return useQuery<LifeEvent[]>({
    queryKey: lifeEventKeys.all,
    queryFn: () => fetchLifeEvents(),
    staleTime: 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useGoal — single goal
// ─────────────────────────────────────────────────────────────────────────────

export function useGoal(
  id: number,
  options?: Partial<UseQueryOptions<Goal>>,
) {
  return useQuery<Goal>({
    queryKey: goalKeys.detail(id),
    queryFn: () => fetchGoal(id),
    staleTime: 60 * 1_000,
    ...options,
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useCreateGoal — create mutation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Creates a new goal and invalidates the goals list on success.
 *
 * Usage:
 *   const { mutate: create, isPending } = useCreateGoal();
 *   create({ name: "Save 50k", goal_type: "SAVINGS", target_amount: 50000 });
 */
export function useCreateGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalCreate) => createGoal(body),
    onSuccess: () => {
      invalidateGoalRelatedCaches(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useUpdateGoal — update mutation
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Updates an existing goal and refreshes both the list and the detail cache.
 *
 * Usage:
 *   const { mutate: update } = useUpdateGoal();
 *   update({ id: 3, update: { current_value: 25000 } });
 */
export function useUpdateGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, update }: { id: number; update: GoalUpdate }) =>
      updateGoal(id, update),
    onSuccess: (_, { id }) => {
      invalidateGoalRelatedCaches(queryClient);
      queryClient.invalidateQueries({ queryKey: goalKeys.detail(id) });
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useDeleteGoal — delete mutation
// ─────────────────────────────────────────────────────────────────────────────

export function useDeleteGoal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteGoal(id),
    onSuccess: () => {
      invalidateGoalRelatedCaches(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["metrics"] });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// useUpdateLifeEvent — PATCH /api/life-events/{id}
// ─────────────────────────────────────────────────────────────────────────────

export function useUpdateLifeEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, update }: { id: number; update: LifeEventUpdate }) =>
      updateLifeEvent(id, update),
    onSuccess: () => {
      invalidateGoalRelatedCaches(queryClient);
    },
  });
}
