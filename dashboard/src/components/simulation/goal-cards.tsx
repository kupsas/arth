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
import { formatCurrency } from "@/lib/utils";
import type { GoalProjection, SimulationGoal, SimulationGoalClass } from "@/lib/types";

const CLASSES: SimulationGoalClass[] = [
  "POINT_IN_TIME",
  "RECURRING_CASH_FLOW",
  "GROWTH",
];

function statusVariant(
  s: string,
): "default" | "secondary" | "destructive" | "outline" {
  if (s === "ON_TRACK" || s === "ACHIEVED") return "default";
  if (s === "AT_RISK") return "secondary";
  if (s === "BEHIND" || s === "IMPOSSIBLE") return "destructive";
  return "outline";
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
  onUpdateGoal,
  onRemoveGoal,
  onReorderList,
  onAddHypothetical,
}: {
  goals: SimulationGoal[];
  projections: GoalProjection[];
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
          <CardDescription>
            Edit targets and priority. Lower priority number = funded first (after non-growth
            ordering rules in the engine).
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
            key={g.id != null ? `id-${g.id}` : `idx-${idx}-${g.name}`}
            goal={g}
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

function GoalCardRow({
  goal,
  projection,
  onUpdate,
  onRemove,
  onMoveUp,
  onMoveDown,
  canUp,
  canDown,
}: {
  goal: SimulationGoal;
  projection?: GoalProjection;
  onUpdate: (patch: Partial<SimulationGoal>) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  canUp: boolean;
  canDown: boolean;
}) {
  const [open, setOpen] = React.useState(false);

  const tgt = goal.target_amount ?? 0;
  const saved = goal.starting_balance ?? 0;
  const pct =
    tgt > 0 ? Math.min(100, Math.round((saved / tgt) * 100)) : 0;

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
            {projection && (
              <Badge variant={statusVariant(projection.status)}>
                {projection.status.replace(/_/g, " ")}
              </Badge>
            )}
            {goal.id == null && (
              <Badge variant="outline">Hypothetical</Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Priority {goal.allocation_priority ?? "—"} ·{" "}
            {formatCurrency(projection?.monthly_allocation ?? 0)}/mo avg ·{" "}
            {goal.goal_class.replace(/_/g, " ")}
          </p>
          <Progress className="h-1.5" value={pct} />
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
                value={goal.goal_class}
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
            <div className="space-y-1">
              <Label className="text-xs">Target (INR)</Label>
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
          </div>
        </div>
      )}
    </div>
  );
}

export function defaultHypotheticalGoal(): SimulationGoal {
  return {
    id: null,
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
