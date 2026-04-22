"use client";

/**
 * Per-goal cards: status, sliders, reorder (↑↓), add hypothetical.
 */

import * as React from "react";
import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SimulationGoalTargetMoneyHint } from "@/components/goal-target-money-hint";
import { recurrenceAmountToMonthlyInr } from "@/lib/goal-target-money";
import { newSimulationClientRowId } from "@/lib/simulation-goal-identity";
import { formatCurrency } from "@/lib/utils";
import type { GoalProjection, SimulationGoal, SimulationGoalClass } from "@/lib/types";

const CLASSES: SimulationGoalClass[] = [
  "POINT_IN_TIME",
  "RECURRING_CASH_FLOW",
];

/** Normalize API / form values so switches stay reliable. */
function normalizedGoalClass(goal: SimulationGoal): string {
  return String(goal.goal_class ?? "").trim().toUpperCase();
}

const RECURRENCE_FREQUENCIES = ["MONTHLY", "QUARTERLY", "ANNUAL"] as const;

function headlinePct(
  p: GoalProjection,
  goal: SimulationGoal,
): { pct: number; label: string } {
  const gc = normalizedGoalClass(goal);
  if (gc === "RECURRING_CASH_FLOW") {
    const v = p.periods_met_pct;
    if (v == null) {
      return { pct: 0, label: "Recurring" };
    }
    return { pct: v, label: `${v.toFixed(0)}% periods` };
  }
  const v = p.projected_completion_pct;
  if (v == null) {
    return { pct: 0, label: "PIT" };
  }
  return { pct: v, label: `${v.toFixed(0)}%` };
}

function pctVariant(
  pct: number,
): "default" | "secondary" | "destructive" | "outline" {
  if (pct >= 90) {
    return "default";
  }
  if (pct >= 60) {
    return "secondary";
  }
  return "destructive";
}

function projectionFor(
  projections: GoalProjection[],
  name: string,
): GoalProjection | undefined {
  return projections.find((p) => p.goal_name === name);
}

export function GoalCards({
  goals,
  projections,
  generalInflationRate = 6,
  onUpdateGoal,
  onRemoveGoal,
  onReorderList,
  onAddHypothetical,
}: {
  goals: SimulationGoal[];
  projections: GoalProjection[];
  /** Headline CPI when a goal's inflation field is empty — must match SliderPanel. */
  generalInflationRate?: number;
  onUpdateGoal: (goalId: number | null, index: number, patch: Partial<SimulationGoal>) => void;
  onRemoveGoal: (goalId: number | null, index: number) => void;
  onReorderList: (ordered: SimulationGoal[]) => void;
  onAddHypothetical: () => void;
}) {
  const sorted = [...goals].sort(
    (a, b) => (a.allocation_priority ?? 99) - (b.allocation_priority ?? 99),
  );

  const move = (from: number, dir: -1 | 1) => {
    const arr = [...sorted];
    const to = from + dir;
    if (to < 0 || to >= arr.length) return;
    const tmp = arr[from];
    arr[from] = arr[to];
    arr[to] = tmp;
    onReorderList(arr);
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <div>
          <CardTitle className="text-base">Goals in this run</CardTitle>
          <CardDescription> Edit targets and priority.<br />
            <ul className="list-disc list-inside space-y-1">
              <li>Lower priority number = funded first.</li>
              <li>
                After each goal&apos;s monthly minimum, extra surplus goes entirely to the
                lowest priority number among open point-in-time goals (deadline not reached).
                Recurring goals never take more than their monthly need.
              </li>
              <li>Point-in-time targets are in today&apos;s rupees; the engine inflates them using goal or general inflation.</li>
              <li>Recurring goals keep the base amount for the first 24 months from the start, then escalate each year by the goal&apos;s inflation rate.</li>
            </ul>
          </CardDescription>
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={onAddHypothetical}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add hypothetical
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {sorted.map((g, idx) => (
          <GoalCardRow
            key={
              g.id != null
                ? `id-${g.id}`
                : g.client_row_id ?? `hyp-fallback-${idx}`
            }
            goal={g}
            generalInflationRate={generalInflationRate}
            projection={projectionFor(projections, g.name)}
            onUpdate={(patch) => onUpdateGoal(g.id ?? null, goals.indexOf(g), patch)}
            onRemove={() => onRemoveGoal(g.id ?? null, goals.indexOf(g))}
            onMoveUp={() => move(idx, -1)}
            onMoveDown={() => move(idx, 1)}
            canUp={idx > 0}
            canDown={idx < sorted.length - 1}
          />
        ))}
        {sorted.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No goals loaded. Add a hypothetical goal or create goals on the Goals page.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * One row in “Goals in this run”: collapsed summary + expandable editors.
 *
 * The simulation engine uses different inputs per `goal_class`:
 * - **POINT_IN_TIME** — lump-sum target in today’s rupees, deadline, starting balance, returns, inflation.
 * - **RECURRING_CASH_FLOW** — payment amount, frequency, active window; inflation escalates the need over time.
 *
 * We hide lump-sum-only fields for recurring rows (and vice versa) so the form matches what the backend reads.
 */
function GoalCardRow({
  goal,
  generalInflationRate,
  projection,
  onUpdate,
  onRemove,
  onMoveUp,
  onMoveDown,
  canUp,
  canDown,
}: {
  goal: SimulationGoal;
  generalInflationRate: number;
  projection?: GoalProjection;
  onUpdate: (patch: Partial<SimulationGoal>) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canUp: boolean;
  canDown: boolean;
}) {
  const [open, setOpen] = React.useState(false);

  const gc = normalizedGoalClass(goal);
  const isRecurring = gc === "RECURRING_CASH_FLOW";
  /** Lump-sum style: PIT or legacy unknown class (treat like PIT in the sandbox). */
  const isPointInTime = gc === "POINT_IN_TIME" || (!isRecurring && gc !== "RECURRING_CASH_FLOW");

  const tgt = goal.target_amount ?? 0;
  const saved = goal.starting_balance ?? 0;
  const pct =
    tgt > 0 ? Math.min(100, Math.round((saved / tgt) * 100)) : 0;

  const freq = (goal.recurrence_frequency ?? "MONTHLY").trim().toUpperCase();
  const recAmt = goal.recurrence_amount ?? 0;
  const monthlyFromRecurrence =
    recAmt > 0 ? recurrenceAmountToMonthlyInr(recAmt, freq) : 0;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        className="flex w-full items-start justify-between gap-2 p-3 text-left"
        onClick={() => setOpen(!open)}
      >
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-medium">{goal.name}</span>
            {projection != null
              ? (() => {
                  const h = headlinePct(projection, goal);
                  return (
                    <Badge variant={pctVariant(h.pct)}>{h.label}</Badge>
                  );
                })()
              : null}
            {goal.id == null && (
              <Badge variant="outline">Hypothetical</Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Priority {goal.allocation_priority ?? "—"} ·{" "}
            {formatCurrency(projection?.monthly_allocation ?? 0)}/mo avg ·{" "}
            {goal.goal_class.replace(/_/g, " ")}
          </p>
          {/* Summary line: what matters depends on goal class (PIT vs growth vs recurring). */}
          {isRecurring ? (
            <p className="text-[11px] text-muted-foreground leading-snug">
              {recAmt > 0 ? (
                <>
                  {formatCurrency(recAmt)} per {freq.toLowerCase()} (
                  ≈{formatCurrency(monthlyFromRecurrence)}/mo base) ·{" "}
                </>
              ) : (
                <>Set recurrence amount · </>
              )}
              {goal.recurrence_start
                ? `from ${goal.recurrence_start}`
                : "start date unset"}
              {goal.recurrence_end ? ` → ${goal.recurrence_end}` : ""}
            </p>
          ) : (
            <p className="text-[11px] text-muted-foreground leading-snug">
              Target {formatCurrency(tgt || 0)}
              {goal.target_date ? ` by ${goal.target_date}` : ""} · saved{" "}
              {formatCurrency(saved)}
              {isPointInTime && (goal.inflation_rate ?? null) != null
                ? ` · ${goal.inflation_rate}% inflation`
                : ""}
            </p>
          )}
          {goal.goal_subtype ? (
            <p className="text-[11px] text-muted-foreground/90 italic">
              Subtype: {goal.goal_subtype.replace(/_/g, " ")}
            </p>
          ) : null}
          {/* Progress toward a lump-sum target only makes sense for PIT / growth. */}
          {isRecurring ? null : <Progress className="h-1.5" value={pct} />}
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
      </button>

      {open && (
        <div className="space-y-3 border-t border-border p-3 pt-2">
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canUp}
              onClick={(e) => {
                e.stopPropagation();
                onMoveUp();
              }}
            >
              ↑ Higher priority
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!canDown}
              onClick={(e) => {
                e.stopPropagation();
                onMoveDown();
              }}
            >
              ↓ Lower priority
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                onRemove();
              }}
            >
              <Trash2 className="mr-1 h-3.5 w-3.5" />
              Remove
            </Button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">Name</Label>
              <Input
                value={goal.name}
                onChange={(e) => onUpdate({ name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Class</Label>
              <Select
                value={
                  CLASSES.includes(gc as SimulationGoalClass)
                    ? gc
                    : "POINT_IN_TIME"
                }
                onValueChange={(v) => onUpdate({ goal_class: v as SimulationGoalClass })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLASSES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c.replace(/_/g, " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* ── Recurring (EMI, rent, …): amount per period + window; engine escalates after grace. ── */}
            {isRecurring ? (
              <>
                <div className="space-y-1 sm:col-span-2">
                  <Label className="text-xs">
                    Payment amount (₹ per period below)
                  </Label>
                  <p className="text-[11px] text-muted-foreground leading-snug">
                    The engine converts this to an average monthly need while the goal is active,
                    then raises it each year using the inflation field (after the first 24 months).
                  </p>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Amount (per period)</Label>
                  <Input
                    type="number"
                    value={goal.recurrence_amount ?? ""}
                    onChange={(e) =>
                      onUpdate({
                        recurrence_amount:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Frequency</Label>
                  <Select
                    value={
                      RECURRENCE_FREQUENCIES.includes(
                        freq as (typeof RECURRENCE_FREQUENCIES)[number],
                      )
                        ? freq
                        : "MONTHLY"
                    }
                    onValueChange={(v) =>
                      onUpdate({ recurrence_frequency: v })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {RECURRENCE_FREQUENCIES.map((f) => (
                        <SelectItem key={f} value={f}>
                          {f.charAt(0) + f.slice(1).toLowerCase()}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Recurrence start</Label>
                  <Input
                    type="date"
                    value={goal.recurrence_start ?? ""}
                    onChange={(e) =>
                      onUpdate({ recurrence_start: e.target.value || null })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Recurrence end (optional)</Label>
                  <Input
                    type="date"
                    value={goal.recurrence_end ?? ""}
                    onChange={(e) =>
                      onUpdate({ recurrence_end: e.target.value || null })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    Inflation % (empty = headline slider)
                  </Label>
                  <Input
                    type="number"
                    step="0.1"
                    placeholder="headline"
                    value={goal.inflation_rate ?? ""}
                    onChange={(e) =>
                      onUpdate({
                        inflation_rate:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">
                    Expected return % (annual, on balance)
                  </Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={goal.expected_return_rate ?? 10}
                    onChange={(e) =>
                      onUpdate({ expected_return_rate: Number(e.target.value) })
                    }
                  />
                </div>
              </>
            ) : null}

            {/* ── Point-in-time: lump sum by date in today&apos;s rupees. ── */}
            {isPointInTime ? (
              <>
                <div className="space-y-1">
                  <Label className="text-xs">Target (₹, today&apos;s money)</Label>
                  <Input
                    type="number"
                    value={goal.target_amount ?? ""}
                    onChange={(e) =>
                      onUpdate({
                        target_amount: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Target date</Label>
                  <Input
                    type="date"
                    value={goal.target_date ?? ""}
                    onChange={(e) =>
                      onUpdate({ target_date: e.target.value || null })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Starting balance</Label>
                  <Input
                    type="number"
                    value={goal.starting_balance ?? 0}
                    onChange={(e) =>
                      onUpdate({ starting_balance: Number(e.target.value) })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Expected return % (annual)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    value={goal.expected_return_rate ?? 10}
                    onChange={(e) =>
                      onUpdate({ expected_return_rate: Number(e.target.value) })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Inflation % (empty = headline)</Label>
                  <Input
                    type="number"
                    step="0.1"
                    placeholder="headline"
                    value={goal.inflation_rate ?? ""}
                    onChange={(e) =>
                      onUpdate({
                        inflation_rate:
                          e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                  />
                </div>
              </>
            ) : null}
          </div>

          {/* Today&apos;s rupees explainer for lump-sum (PIT) goals. */}
          {isPointInTime ? (
            <SimulationGoalTargetMoneyHint
              goal={goal}
              generalInflationRate={generalInflationRate}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}

export function defaultHypotheticalGoal(): SimulationGoal {
  return {
    id: null,
    client_row_id: newSimulationClientRowId(),
    name: "New hypothetical goal",
    goal_class: "POINT_IN_TIME",
    target_amount: 500000,
    target_date: new Date(Date.now() + 86400 * 365 * 3).toISOString().slice(0, 10),
    starting_balance: 0,
    allocation_priority: 50,
    expected_return_rate: 10,
    inflation_rate: null,
    goal_subtype: "CUSTOM",
  };
}
