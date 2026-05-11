"use client";

/**
 * Confirm persisted changes: PATCH goals, optional creates, reorder.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  completionShifts,
  diffGlobalParams,
  diffGoalRow,
  findGoalInParams,
} from "@/lib/simulation-diff";
import { createGoal, reorderGoals, updateGoal, putSimulationSandboxPreferences } from "@/lib/api";
import type {
  GoalCreate,
  GoalUpdate,
  SimulationGoal,
  SimulationParams,
  SimulationResult,
} from "@/lib/types";
import { simulationGoalClassToGoalType } from "@/lib/goal-ui-kind";

/** Only include fields that differ — server uses exclude_unset on PATCH. */
function goalUpdatePatch(bg: SimulationGoal, dg: SimulationGoal): GoalUpdate {
  const p: GoalUpdate = {};
  if (bg.name !== dg.name) p.name = dg.name;
  if ((bg.target_amount ?? null) !== (dg.target_amount ?? null)) p.target_amount = dg.target_amount ?? null;
  if ((bg.target_date ?? null) !== (dg.target_date ?? null)) p.target_date = dg.target_date ?? null;
  if (bg.goal_class !== dg.goal_class) p.goal_class = dg.goal_class;
  if ((bg.allocation_priority ?? 99) !== (dg.allocation_priority ?? 99)) {
    p.allocation_priority = dg.allocation_priority ?? undefined;
  }
  if ((bg.expected_return_rate ?? 10) !== (dg.expected_return_rate ?? 10)) {
    p.expected_return_rate = dg.expected_return_rate ?? null;
  }
  const bi = bg.inflation_rate ?? null;
  const di = dg.inflation_rate ?? null;
  if (bi !== di) p.goal_specific_inflation_rate = di;
  if ((bg.starting_balance ?? 0) !== (dg.starting_balance ?? 0)) {
    p.starting_balance = dg.starting_balance ?? null;
  }
  if ((bg.recurrence_amount ?? null) !== (dg.recurrence_amount ?? null)) {
    p.recurrence_amount = dg.recurrence_amount ?? null;
  }
  if ((bg.recurrence_frequency ?? null) !== (dg.recurrence_frequency ?? null)) {
    p.recurrence_frequency = dg.recurrence_frequency ?? null;
  }
  if ((bg.recurrence_start ?? null) !== (dg.recurrence_start ?? null)) {
    p.recurrence_start = dg.recurrence_start ?? null;
  }
  if ((bg.recurrence_end ?? null) !== (dg.recurrence_end ?? null)) {
    p.recurrence_end = dg.recurrence_end ?? null;
  }
  return p;
}

function simulationGoalToCreate(g: SimulationGoal): GoalCreate {
  return {
    name: g.name,
    goal_type: simulationGoalClassToGoalType(g.goal_class),
    target_amount: g.target_amount ?? undefined,
    target_date: g.target_date ?? undefined,
    goal_class: g.goal_class,
    allocation_priority: g.allocation_priority ?? undefined,
    expected_return_rate: g.expected_return_rate ?? undefined,
    goal_specific_inflation_rate: g.inflation_rate ?? undefined,
    starting_balance: g.starting_balance ?? undefined,
    goal_subtype: g.goal_subtype ?? undefined,
    recurrence_amount: g.recurrence_amount ?? undefined,
    recurrence_frequency: g.recurrence_frequency ?? undefined,
    recurrence_start: g.recurrence_start ?? undefined,
    recurrence_end: g.recurrence_end ?? undefined,
    activation_status: "ACTIVE",
    current_value: g.starting_balance ?? undefined,
  };
}

function prioritySnapshot(params: SimulationParams): string {
  return JSON.stringify(
    [...params.goals]
      .filter((g) => g.id != null)
      .sort((a, b) => (a.allocation_priority ?? 99) - (b.allocation_priority ?? 99))
      .map((g) => ({ id: g.id, ap: g.allocation_priority ?? 99 })),
  );
}

function priorityOrderChanged(base: SimulationParams, draft: SimulationParams): boolean {
  return prioritySnapshot(base) !== prioritySnapshot(draft);
}

export function SaveSimulationDialog({
  open,
  onOpenChange,
  baseParams,
  draftParams,
  baseResult,
  draftResult,
  onSuccess,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  baseParams: SimulationParams | null;
  draftParams: SimulationParams | null;
  baseResult: SimulationResult | null;
  draftResult: SimulationResult | null;
  onSuccess: () => Promise<void>;
}) {
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  const summary = React.useMemo(() => {
    if (!baseParams || !draftParams) return null;
    const globalChanges = diffGlobalParams(baseParams, draftParams);
    const goalRows: { title: string; changes: { label: string; from: string; to: string }[] }[] =
      [];
    for (const dg of draftParams.goals) {
      if (dg.id == null) continue;
      const bg = findGoalInParams(baseParams, dg.id);
      const ch = diffGoalRow(bg, dg);
      if (ch.length) goalRows.push({ title: dg.name, changes: ch });
    }
    const newHypos = draftParams.goals.filter((g) => g.id == null);
    const shifts =
      baseResult && draftResult
        ? completionShifts(baseResult.projections, draftResult.projections)
        : [];
    const lowProgressGoals = (draftResult?.projections ?? []).filter((p) => {
      if (p.projected_completion_pct != null) {
        return p.projected_completion_pct < 30;
      }
      if (p.periods_met_pct != null) {
        return p.periods_met_pct < 30;
      }
      return false;
    });
    return { globalChanges, goalRows, newHypos, shifts, lowProgressGoals };
  }, [baseParams, draftParams, baseResult, draftResult]);

  const runSave = async () => {
    if (!baseParams || !draftParams) return;
    setErr(null);
    setSaving(true);
    try {
      for (const dg of draftParams.goals) {
        if (dg.id == null) continue;
        const bg = findGoalInParams(baseParams, dg.id);
        const changes = diffGoalRow(bg, dg);
        if (changes.length === 0) continue;
        if (!bg) continue;
        await updateGoal(dg.id, goalUpdatePatch(bg, dg));
      }

      for (const g of draftParams.goals) {
        if (g.id != null) continue;
        await createGoal(simulationGoalToCreate(g));
      }

      if (priorityOrderChanged(baseParams, draftParams)) {
        const order = [...draftParams.goals]
          .filter((g) => g.id != null)
          .sort((a, b) => (a.allocation_priority ?? 99) - (b.allocation_priority ?? 99))
          .map((g) => ({
            goal_id: g.id!,
            allocation_priority: g.allocation_priority ?? 99,
          }));
        if (order.length >= 1) await reorderGoals(order);
      }

      await putSimulationSandboxPreferences({
        monthly_surplus: draftParams.monthly_surplus,
        salary_growth_rate: draftParams.salary_growth_rate ?? 5,
        general_inflation_rate: draftParams.general_inflation_rate ?? 6,
      });

      await onSuccess();
      onOpenChange(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save. Try again?");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Save simulation changes?</DialogTitle>
          <DialogDescription>
            This updates your stored goals and your saved simulation sliders (surplus, salary
            growth, headline inflation) to match the sandbox. Review the diff below.
          </DialogDescription>
        </DialogHeader>

        {summary && (
          <div className="space-y-3 text-sm">
            {summary.lowProgressGoals.length > 0 && (
              <div className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-destructive">
                <p className="font-medium">Very low projected progress (&lt;30%)</p>
                <ul className="list-inside list-disc">
                  {summary.lowProgressGoals.map((p) => (
                    <li key={p.goal_name}>{p.goal_name}</li>
                  ))}
                </ul>
              </div>
            )}

            {summary.globalChanges.length > 0 && (
              <div>
                <p className="mb-1 font-medium text-foreground">Global simulation settings</p>
                <p className="text-xs text-muted-foreground">
                  Monthly surplus, salary growth, and general inflation are saved when you confirm.
                  Horizon length still follows your goal dates (not stored as a fixed number).
                </p>
                <ul className="mt-1 list-inside list-disc text-muted-foreground">
                  {summary.globalChanges.map((c) => (
                    <li key={c.label}>
                      {c.label}: {c.from} → {c.to}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {summary.goalRows.map((row) => (
              <div key={row.title}>
                <p className="font-medium">{row.title}</p>
                <ul className="list-inside list-disc text-muted-foreground">
                  {row.changes.map((c) => (
                    <li key={c.label}>
                      {c.label}: {c.from} → {c.to}
                    </li>
                  ))}
                </ul>
              </div>
            ))}

            {summary.newHypos.length > 0 && (
              <div>
                <p className="font-medium">New goals to create</p>
                <ul className="list-inside list-disc">
                  {summary.newHypos.map((g) => (
                    <li key={g.name}>{g.name}</li>
                  ))}
                </ul>
              </div>
            )}

            {summary.shifts.length > 0 && (
              <div>
                <p className="font-medium">Projected completion shifts</p>
                <ul className="list-inside list-disc text-muted-foreground">
                  {summary.shifts.map((s) => (
                    <li key={s.name}>
                      {s.name}: {s.from} → {s.to}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {err && <p className="text-sm text-destructive">{err}</p>}

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={saving} onClick={() => void runSave()}>
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Save to goals
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
