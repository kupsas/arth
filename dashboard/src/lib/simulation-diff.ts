/**
 * Pure helpers to diff sandbox SimulationParams for the save-confirmation dialog.
 */

import type { GoalProjection, SimulationGoal, SimulationParams } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

export interface NamedChange {
  label: string;
  from: string;
  to: string;
}

function fmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return formatCurrency(n);
}

function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function goalSig(g: SimulationGoal): string {
  return JSON.stringify({
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
  });
}

/** Field-level changes for one persisted goal (id set). */
export function diffGoalRow(
  base: SimulationGoal | undefined,
  draft: SimulationGoal,
): NamedChange[] {
  const changes: NamedChange[] = [];
  if (!base) return changes;

  if (base.name !== draft.name) {
    changes.push({ label: "Name", from: base.name, to: draft.name });
  }
  if (base.goal_class !== draft.goal_class) {
    changes.push({ label: "Goal class", from: String(base.goal_class), to: String(draft.goal_class) });
  }
  if ((base.target_amount ?? null) !== (draft.target_amount ?? null)) {
    changes.push({
      label: "Target amount",
      from: fmt(base.target_amount ?? null),
      to: fmt(draft.target_amount ?? null),
    });
  }
  if ((base.target_date ?? null) !== (draft.target_date ?? null)) {
    changes.push({
      label: "Target date",
      from: base.target_date ?? "—",
      to: draft.target_date ?? "—",
    });
  }
  if ((base.starting_balance ?? 0) !== (draft.starting_balance ?? 0)) {
    changes.push({
      label: "Starting balance",
      from: fmt(base.starting_balance ?? 0),
      to: fmt(draft.starting_balance ?? 0),
    });
  }
  if ((base.allocation_priority ?? 99) !== (draft.allocation_priority ?? 99)) {
    changes.push({
      label: "Allocation priority",
      from: String(base.allocation_priority ?? 99),
      to: String(draft.allocation_priority ?? 99),
    });
  }
  if ((base.expected_return_rate ?? 10) !== (draft.expected_return_rate ?? 10)) {
    changes.push({
      label: "Expected return",
      from: fmtPct(base.expected_return_rate ?? 10),
      to: fmtPct(draft.expected_return_rate ?? 10),
    });
  }
  const bi = base.inflation_rate ?? null;
  const di = draft.inflation_rate ?? null;
  if (bi !== di) {
    changes.push({
      label: "Inflation (goal)",
      from: bi == null ? "(headline)" : fmtPct(bi),
      to: di == null ? "(headline)" : fmtPct(di),
    });
  }
  if ((base.goal_subtype ?? null) !== (draft.goal_subtype ?? null)) {
    changes.push({
      label: "Subtype",
      from: base.goal_subtype ?? "—",
      to: draft.goal_subtype ?? "—",
    });
  }
  return changes;
}

export function diffGlobalParams(base: SimulationParams, draft: SimulationParams): NamedChange[] {
  const out: NamedChange[] = [];
  if (base.monthly_surplus !== draft.monthly_surplus) {
    out.push({
      label: "Monthly surplus (sandbox)",
      from: fmt(base.monthly_surplus),
      to: fmt(draft.monthly_surplus),
    });
  }
  if ((base.salary_growth_rate ?? 5) !== (draft.salary_growth_rate ?? 5)) {
    out.push({
      label: "Salary growth",
      from: fmtPct(base.salary_growth_rate ?? 5),
      to: fmtPct(draft.salary_growth_rate ?? 5),
    });
  }
  if ((base.general_inflation_rate ?? 6) !== (draft.general_inflation_rate ?? 6)) {
    out.push({
      label: "General inflation",
      from: fmtPct(base.general_inflation_rate ?? 6),
      to: fmtPct(draft.general_inflation_rate ?? 6),
    });
  }
  if ((base.simulation_months ?? 240) !== (draft.simulation_months ?? 240)) {
    out.push({
      label: "Horizon (months)",
      from: String(base.simulation_months ?? 240),
      to: String(draft.simulation_months ?? 240),
    });
  }
  return out;
}

/** Completion date shifts between two simulation results (same goal name). */
export function completionShifts(
  baseProj: GoalProjection[],
  draftProj: GoalProjection[],
): { name: string; from: string; to: string }[] {
  const bm = new Map(baseProj.map((p) => [p.goal_name, p]));
  const shifts: { name: string; from: string; to: string }[] = [];
  for (const p of draftProj) {
    const b = bm.get(p.goal_name);
    if (!b) continue;
    const bd = b.projected_completion_date;
    const dd = p.projected_completion_date;
    if (bd !== dd) {
      shifts.push({
        name: p.goal_name,
        from: bd ?? "—",
        to: dd ?? "—",
      });
    }
  }
  return shifts;
}

export function findGoalInParams(params: SimulationParams, id: number): SimulationGoal | undefined {
  return params.goals.find((g) => g.id === id);
}
