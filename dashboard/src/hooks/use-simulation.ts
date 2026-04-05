/**
 * use-simulation.ts — sandbox state for the Goals simulation page (Sub-Plan H).
 *
 * Concepts:
 *   - **baseParams** — last saved / server-hydrated snapshot (immutable until reset or save).
 *   - **draftParams** — working copy the user edits; sliders and goal cards mutate this.
 *   - **result** — output of POST /api/simulate for the current draft.
 *
 * Draft state is mirrored to sessionStorage (`arth:simulation:draft`) so navigating
 * away and back keeps unsaved work. Surplus re-simulation is debounced (300ms) to
 * avoid hammering the API while dragging sliders.
 */

"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchSimulateFromCurrent, runSimulation } from "@/lib/api";
import { goalKeys, goalTreeKeys, goalLinkKeys, lifeEventKeys } from "@/hooks/use-goals";
import type { SimulationGoal, SimulationParams, SimulationResult } from "@/lib/types";

/** sessionStorage key — must stay in sync with save/reset flows */
export const SIMULATION_DRAFT_STORAGE_KEY = "arth:simulation:draft";

function cloneParams(p: SimulationParams): SimulationParams {
  return JSON.parse(JSON.stringify(p)) as SimulationParams;
}

/** Stable JSON for dirty-checking (goal order preserved; dates are strings). */
function paramsSignature(p: SimulationParams): string {
  return JSON.stringify({
    monthly_surplus: p.monthly_surplus,
    salary_growth_rate: p.salary_growth_rate ?? 5,
    general_inflation_rate: p.general_inflation_rate ?? 6,
    simulation_months: p.simulation_months ?? 240,
    as_of_date: p.as_of_date ?? null,
    one_time_inflows: p.one_time_inflows ?? [],
    one_time_outflows: p.one_time_outflows ?? [],
    goals: (p.goals ?? []).map((g) => ({
      id: g.id ?? null,
      name: g.name,
      goal_class: g.goal_class,
      target_amount: g.target_amount ?? null,
      target_date: g.target_date ?? null,
      starting_balance: g.starting_balance ?? 0,
      allocation_priority: g.allocation_priority ?? 99,
      expected_return_rate: g.expected_return_rate ?? 10,
      inflation_rate: g.inflation_rate ?? null,
      recurrence_amount: g.recurrence_amount ?? null,
      recurrence_frequency: g.recurrence_frequency ?? null,
      recurrence_start: g.recurrence_start ?? null,
      recurrence_end: g.recurrence_end ?? null,
      goal_subtype: g.goal_subtype ?? null,
    })),
  });
}

export function useSimulation() {
  const queryClient = useQueryClient();
  const [baseParams, setBaseParams] = React.useState<SimulationParams | null>(null);
  /** Last simulation output for baseParams (for save-dialog vs draft deltas). */
  const [baseResult, setBaseResult] = React.useState<SimulationResult | null>(null);
  const [draftParams, setDraftParams] = React.useState<SimulationParams | null>(null);
  const [meta, setMeta] = React.useState<Record<string, unknown>>({});
  const [result, setResult] = React.useState<SimulationResult | null>(null);

  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Skip one debounced run after programmatic draft set (hydrate / reset / restore). */
  const skipNextDebounceRef = React.useRef(false);
  const hydratedRef = React.useRef(false);

  const hydrateQuery = useQuery({
    queryKey: ["simulation", "from-current"],
    queryFn: () => fetchSimulateFromCurrent(),
    staleTime: 30_000,
  });

  // Only destructure `mutate` for the debounce effect — the full `useMutation()` return
  // is a new object every render; putting it in a useEffect dependency array causes an
  // infinite loop (each POST -> setResult -> re-render -> "new" mutation -> effect -> POST).
  const {
    mutate: runSimulateMutation,
    isPending: isSimulatingMutation,
    error: simulateMutationError,
  } = useMutation({
    mutationFn: (p: SimulationParams) => runSimulation(p),
    onSuccess: (data) => setResult(data),
  });

  // One-shot: hydrate from server + optional sessionStorage draft
  React.useEffect(() => {
    if (!hydrateQuery.data || hydratedRef.current) return;
    hydratedRef.current = true;

    const server = hydrateQuery.data;
    const bp = server.params as SimulationParams;
    setBaseParams(bp);
    setBaseResult(server.result as SimulationResult);
    setMeta(server.meta ?? {});

    let restored: SimulationParams | null = null;
    try {
      const raw = sessionStorage.getItem(SIMULATION_DRAFT_STORAGE_KEY);
      if (raw) restored = JSON.parse(raw) as SimulationParams;
    } catch {
      restored = null;
    }

    if (restored && Array.isArray(restored.goals)) {
      skipNextDebounceRef.current = true;
      setDraftParams(restored);
      void runSimulation(restored)
        .then(setResult)
        .catch(() => {});
    } else {
      skipNextDebounceRef.current = true;
      const d = cloneParams(bp);
      setDraftParams(d);
      setResult(server.result as SimulationResult);
    }
  }, [hydrateQuery.data]);

  // Debounced re-simulation when draft changes (user edits)
  React.useEffect(() => {
    if (!draftParams) return;

    if (skipNextDebounceRef.current) {
      skipNextDebounceRef.current = false;
      return;
    }

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      runSimulateMutation(draftParams);
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // `runSimulateMutation` is referentially stable from TanStack Query; do not add the
    // full mutation result object here (see comment above useMutation).
  }, [draftParams, runSimulateMutation]);

  // Persist draft for navigation-away / refresh within tab
  React.useEffect(() => {
    if (!draftParams) return;
    try {
      sessionStorage.setItem(SIMULATION_DRAFT_STORAGE_KEY, JSON.stringify(draftParams));
    } catch {
      /* quota / private mode */
    }
  }, [draftParams]);

  const isDirty = React.useMemo(() => {
    if (!baseParams || !draftParams) return false;
    return paramsSignature(baseParams) !== paramsSignature(draftParams);
  }, [baseParams, draftParams]);

  const updateGlobalParam = React.useCallback(
    <K extends keyof SimulationParams>(key: K, value: SimulationParams[K]) => {
      setDraftParams((prev) => {
        if (!prev) return prev;
        return { ...prev, [key]: value };
      });
    },
    [],
  );

  const updateGoal = React.useCallback(
    (goalId: number | null, index: number | null, patch: Partial<SimulationGoal>) => {
      setDraftParams((prev) => {
        if (!prev) return prev;
        const goals = [...prev.goals];
        let i = -1;
        if (goalId != null) i = goals.findIndex((g) => g.id === goalId);
        else if (index != null) i = index;
        if (i < 0 || i >= goals.length) return prev;
        goals[i] = { ...goals[i], ...patch };
        return { ...prev, goals };
      });
    },
    [],
  );

  const addHypotheticalGoal = React.useCallback((goal: SimulationGoal) => {
    setDraftParams((prev) => {
      if (!prev) return prev;
      const g = { ...goal, id: goal.id ?? null };
      return { ...prev, goals: [...prev.goals, g] };
    });
  }, []);

  const removeGoal = React.useCallback((goalId: number | null, index: number | null) => {
    setDraftParams((prev) => {
      if (!prev) return prev;
      if (goalId != null) {
        return { ...prev, goals: prev.goals.filter((g) => g.id !== goalId) };
      }
      if (index != null && index >= 0 && index < prev.goals.length) {
        const goals = [...prev.goals];
        goals.splice(index, 1);
        return { ...prev, goals };
      }
      return prev;
    });
  }, []);

  /** Replace goals list with a new order; reassigns allocation_priority 1..n. */
  const reorderGoalsByList = React.useCallback((ordered: SimulationGoal[]) => {
    setDraftParams((prev) => {
      if (!prev) return prev;
      const next = ordered.map((g, i) => ({
        ...g,
        allocation_priority: i + 1,
      }));
      return { ...prev, goals: next };
    });
  }, []);

  const reset = React.useCallback(() => {
    if (!baseParams) return;
    sessionStorage.removeItem(SIMULATION_DRAFT_STORAGE_KEY);
    skipNextDebounceRef.current = true;
    const d = cloneParams(baseParams);
    setDraftParams(d);
    void runSimulation(d)
      .then(setResult)
      .catch(() => {});
  }, [baseParams]);

  /** After successful PATCH/create/reorder — reload canonical params from DB. */
  const commitDraftAsBase = React.useCallback(async () => {
    const data = await fetchSimulateFromCurrent();
    const bp = data.params as SimulationParams;
    const res = data.result as SimulationResult;
    setBaseParams(bp);
    setBaseResult(res);
    setMeta(data.meta ?? {});
    sessionStorage.removeItem(SIMULATION_DRAFT_STORAGE_KEY);
    skipNextDebounceRef.current = true;
    const d = cloneParams(bp);
    setDraftParams(d);
    setResult(res);
    hydratedRef.current = true;
    void queryClient.invalidateQueries({ queryKey: goalKeys.all });
    void queryClient.invalidateQueries({ queryKey: goalTreeKeys.all });
    void queryClient.invalidateQueries({ queryKey: goalLinkKeys.all });
    void queryClient.invalidateQueries({ queryKey: lifeEventKeys.all });
  }, [queryClient]);

  return {
    baseParams,
    baseResult,
    draftParams,
    meta,
    result,
    isLoading: hydrateQuery.isLoading,
    isSimulating: isSimulatingMutation,
    isDirty,
    error:
      (hydrateQuery.error as Error | null)?.message ??
      (simulateMutationError as Error | null)?.message ??
      null,
    updateGlobalParam,
    updateGoal,
    addHypotheticalGoal,
    removeGoal,
    reorderGoalsByList,
    reset,
    commitDraftAsBase,
    setDraftParams,
  };
}
