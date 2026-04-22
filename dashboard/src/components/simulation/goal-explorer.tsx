"use client";

/**
 * Goal Explorer — combines goal list + per-goal simulation readout.
 *
 * Left: draggable list of goal names (priority order). Drag to reorder → updates
 * allocation_priority via onReorderList (same as legacy ↑↓ buttons).
 * Right: read-only metrics + glide vs simulated chart for POINT_IN_TIME;
 * recurring goals show period funding stats (no chart — recurring “corpus” line is misleading).
 */

import * as React from "react";
import {
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { GripVertical, Plus } from "lucide-react";

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { CHART_GOAL_LINE, CHART_SERIES_COLORS } from "@/lib/chart-colors";
import {
  monthsBetweenCalendar,
  nominalTargetFromTodaysRupees,
  parseISODateLocal,
  pickEffectiveInflationPct,
  recurrenceAmountToMonthlyInr,
} from "@/lib/goal-target-money";
import { formatCurrency, formatInrChartAxis } from "@/lib/utils";
import type {
  GoalProjection,
  MonthlySnapshot,
  SimulationGoal,
} from "@/lib/types";

const CHART_SAMPLE = 3;

/** Stable React / DnD id: DB id, client_row_id for hypotheticals, else name (last resort). */
function stableGoalKey(g: SimulationGoal): string {
  if (g.id != null) return `id-${g.id}`;
  if (g.client_row_id) return g.client_row_id;
  return `name-${g.name}`;
}

function normalizedGoalClass(goal: SimulationGoal): string {
  return String(goal.goal_class ?? "").trim().toUpperCase();
}

function projectionFor(
  projections: GoalProjection[],
  goal: SimulationGoal,
): GoalProjection | undefined {
  return projections.find(
    (p) =>
      (goal.id != null &&
        p.goal_id != null &&
        Number(p.goal_id) === Number(goal.id)) ||
      p.goal_name === goal.name,
  );
}

/** Map simulation headline % to badge color (display thresholds only). */
function pctHeadline(
  p: GoalProjection,
  goalClass: string,
): { pct: number; label: string } {
  const gc = goalClass.toUpperCase();
  if (gc === "RECURRING_CASH_FLOW") {
    const v = p.periods_met_pct;
    if (v == null) {
      return { pct: 0, label: "Recurring" };
    }
    return { pct: v, label: `${v.toFixed(0)}% periods met` };
  }
  const v = p.projected_completion_pct;
  if (v == null) {
    return { pct: 0, label: "PIT" };
  }
  return { pct: v, label: `${v.toFixed(0)}% at deadline` };
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

/** PIT: scope chart to target month when deadline is set (same as RunRateChart). */
function targetDateForLumpSumChart(goal: SimulationGoal): string | null {
  const gc = normalizedGoalClass(goal);
  if (gc !== "POINT_IN_TIME") return null;
  const td = goal.target_date?.trim();
  return td || null;
}

/** Build downsampled rows for glide vs simulated (same idea as RunRateChart). */
function lumpSumChartRows(p: GoalProjection, goal: SimulationGoal) {
  const rows: { m: string; expected: number | null; actual: number }[] = [];
  const cutoff = targetDateForLumpSumChart(goal);
  let tr = p.monthly_trajectory ?? [];
  if (cutoff) {
    const ym = cutoff.slice(0, 7);
    tr = tr.filter((s) => s.month.slice(0, 7) <= ym);
  }
  for (let i = 0; i < tr.length; i += CHART_SAMPLE) {
    const s = tr[i];
    if (!s) continue;
    rows.push({
      m: s.month.slice(0, 7),
      expected: s.target_at_month ?? null,
      actual: s.cumulative_value,
    });
  }
  return rows;
}

/** Months per billing period for worst-period heuristic (aligns with engine chunking). */
function recurrencePeriodMonths(freq: string | undefined | null): number {
  const f = (freq ?? "MONTHLY").trim().toUpperCase();
  if (f === "QUARTERLY") return 3;
  if (f === "ANNUAL" || f === "YEARLY") return 12;
  return 1;
}

/**
 * Same idea as `_compute_recurring_funding_stats` in simulation.py: only the months from
 * first positive `monthly_need` through last — so QUARTERLY/ANNUAL chunks line up with when
 * the obligation actually runs (not from simulation month 0).
 */
function recurringBillableSegment(trajectory: MonthlySnapshot[]): MonthlySnapshot[] {
  let start = -1;
  let end = -1;
  for (let i = 0; i < trajectory.length; i++) {
    if ((trajectory[i]?.monthly_need ?? 0) > 1e-6) {
      start = i;
      break;
    }
  }
  if (start < 0) return [];
  for (let i = trajectory.length - 1; i >= start; i--) {
    if ((trajectory[i]?.monthly_need ?? 0) > 1e-6) {
      end = i;
      break;
    }
  }
  return trajectory.slice(start, end + 1);
}

/**
 * Simulated pot at the goal deadline: last trajectory month with month ≤ deadline YYYY-MM.
 * If the run ends before the deadline month, `truncated` is true (corpus is last simulated month).
 */
function corpusAtOrBeforeDeadline(
  trajectory: MonthlySnapshot[],
  deadlineYm: string,
): { value: number; monthYm: string; truncated: boolean } | null {
  if (!trajectory.length) return null;
  let best: MonthlySnapshot | null = null;
  for (const s of trajectory) {
    const mk = s.month.slice(0, 7);
    if (mk <= deadlineYm) {
      best = s;
    }
  }
  if (!best) return null;
  const lastYm = trajectory[trajectory.length - 1]!.month.slice(0, 7);
  return {
    value: best.cumulative_value,
    monthYm: best.month.slice(0, 7),
    truncated: lastYm < deadlineYm,
  };
}

/** Largest (need − contribution) within any billing chunk on the billable segment (recurring). */
function worstPeriodDeficitInr(
  trajectory: MonthlySnapshot[],
  freq: string | null | undefined,
): number | null {
  const pm = recurrencePeriodMonths(freq);
  const segment = recurringBillableSegment(trajectory);
  if (!segment.length) return null;
  let worst = 0;
  for (let i = 0; i < segment.length; i += pm) {
    const chunk = segment.slice(i, i + pm);
    const need = chunk.reduce((a, s) => a + (s.monthly_need ?? 0), 0);
    const contrib = chunk.reduce((a, s) => a + s.monthly_contribution, 0);
    if (need <= 1e-6) continue;
    worst = Math.max(worst, need - contrib);
  }
  return worst > 1e-6 ? worst : null;
}

function SortableGoalRow({
  goal,
  selected,
  onSelect,
  projection,
}: {
  goal: SimulationGoal;
  selected: boolean;
  onSelect: () => void;
  projection?: GoalProjection;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: stableGoalKey(goal) });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.85 : 1,
  };

  const head =
    projection != null
      ? pctHeadline(projection, normalizedGoalClass(goal))
      : null;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`flex min-w-0 items-center gap-1 rounded-md border px-1.5 py-1.5 text-sm ${
        selected
          ? "border-primary bg-primary/10"
          : "border-transparent bg-muted/40 hover:bg-muted/70"
      }`}
    >
      <button
        type="button"
        className="touch-none text-muted-foreground hover:text-foreground"
        aria-label="Drag to reorder priority"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4 shrink-0" />
      </button>
      <button
        type="button"
        onClick={onSelect}
        className="min-w-0 flex-1 truncate text-left font-medium"
      >
        {goal.name}
      </button>
      {head != null ? (
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${
            head.pct >= 90
              ? "bg-emerald-500"
              : head.pct >= 60
                ? "bg-amber-500"
                : "bg-red-500"
          }`}
          title={head.label}
        />
      ) : null}
    </div>
  );
}

export function GoalExplorer({
  goals,
  projections,
  generalInflationRate,
  asOfDate,
  onReorderList,
  onAddHypothetical,
}: {
  goals: SimulationGoal[];
  projections: GoalProjection[];
  generalInflationRate: number;
  /** Simulation anchor — used for “months to deadline” and nominal target; falls back to today. */
  asOfDate?: string | null;
  onReorderList: (ordered: SimulationGoal[]) => void;
  onAddHypothetical: () => void;
}) {
  const sorted = React.useMemo(
    () =>
      [...goals].sort(
        (a, b) => (a.allocation_priority ?? 99) - (b.allocation_priority ?? 99),
      ),
    [goals],
  );

  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);

  React.useEffect(() => {
    const keys = sorted.map(stableGoalKey);
    if (keys.length === 0) {
      setSelectedKey(null);
      return;
    }
    setSelectedKey((prev) => (prev && keys.includes(prev) ? prev : keys[0]!));
  }, [sorted]);

  const selectedGoal = sorted.find((g) => stableGoalKey(g) === selectedKey);
  const selectedProjection = selectedGoal
    ? projectionFor(projections, selectedGoal)
    : undefined;

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const anchorDate = React.useMemo(() => {
    if (asOfDate?.trim()) {
      const d = parseISODateLocal(asOfDate.trim());
      if (d) return d;
    }
    const n = new Date();
    return new Date(n.getFullYear(), n.getMonth(), n.getDate());
  }, [asOfDate]);

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = sorted.findIndex((g) => stableGoalKey(g) === active.id);
    const newIndex = sorted.findIndex((g) => stableGoalKey(g) === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const next = arrayMove(sorted, oldIndex, newIndex);
    onReorderList(next);
  }

  const detailBody = (() => {
    if (!selectedGoal || !selectedProjection) {
      return (
        <p className="text-sm text-muted-foreground">
          Select a goal to see simulation details.
        </p>
      );
    }

    const g = selectedGoal;
    const p = selectedProjection;
    const gc = normalizedGoalClass(g);
    const isRecurring = gc === "RECURRING_CASH_FLOW";

    const freq = (g.recurrence_frequency ?? "MONTHLY").trim().toUpperCase();
    const recAmt = g.recurrence_amount ?? 0;
    const monthlyEq =
      recAmt > 0 ? recurrenceAmountToMonthlyInr(recAmt, freq) : 0;

    if (isRecurring) {
      const worst =
        p.worst_period_deficit ??
        worstPeriodDeficitInr(
          p.monthly_trajectory ?? [],
          g.recurrence_frequency,
        );
      const fr = p.funding_rate;
      const h = pctHeadline(p, gc);
      return (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={pctVariant(h.pct)}>{h.label}</Badge>
            {g.goal_subtype ? (
              <span className="text-xs text-muted-foreground">
                {String(g.goal_subtype).replace(/_/g, " ")}
              </span>
            ) : null}
          </div>

          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">Payment</dt>
              <dd className="font-medium tabular-nums">
                {recAmt > 0
                  ? `${formatCurrency(recAmt)} / ${freq.toLowerCase()} (≈${formatCurrency(monthlyEq)}/mo)`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Active window</dt>
              <dd>
                {g.recurrence_start ?? "—"}
                {g.recurrence_end ? ` → ${g.recurrence_end}` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Periods funded</dt>
              <dd className="tabular-nums">
                {p.periods_funded != null && p.periods_total != null
                  ? `${p.periods_funded} / ${p.periods_total}`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Funding rate</dt>
              <dd className="tabular-nums">
                {fr != null ? `${(fr * 100).toFixed(1)}%` : "—"}
                {fr != null ? (
                  <Progress className="mt-1 h-2" value={Math.min(100, fr * 100)} />
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Total contributed</dt>
              <dd className="tabular-nums">
                {p.total_contributed != null
                  ? formatCurrency(p.total_contributed)
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Total needed (sim)</dt>
              <dd className="tabular-nums">
                {p.total_needed != null ? formatCurrency(p.total_needed) : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Gap (needed − contributed)</dt>
              <dd className="tabular-nums">
                {p.total_needed != null && p.total_contributed != null
                  ? formatCurrency(
                      Math.max(0, p.total_needed - p.total_contributed),
                    )
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Avg allocation / month</dt>
              <dd className="tabular-nums">
                {formatCurrency(p.monthly_allocation)}
              </dd>
            </div>
            {worst != null ? (
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground">
                  Worst period shortfall (engine periods)
                </dt>
                <dd className="tabular-nums text-amber-600 dark:text-amber-400">
                  {formatCurrency(worst)}
                </dd>
              </div>
            ) : null}
          </dl>

        </div>
      );
    }

    // POINT_IN_TIME or other non-recurring — lump-sum style readout + chart
    const tgt = g.target_amount ?? 0;
    const td = g.target_date?.trim();
    const end = td ? parseISODateLocal(td) : null;
    const monthsToDeadline =
      end && anchorDate ? monthsBetweenCalendar(anchorDate, end) : null;
    const effInfl = pickEffectiveInflationPct({
      goalSpecific: g.inflation_rate ?? null,
      headlinePct: generalInflationRate,
    });
    const nominalAtDeadline =
      tgt > 0 && monthsToDeadline != null && monthsToDeadline > 0
        ? nominalTargetFromTodaysRupees(tgt, monthsToDeadline, effInfl)
        : null;

    const lumpGc = normalizedGoalClass(g);
    const hasDeadlineYm = Boolean(td && td.length >= 7);
    const deadlineYm = hasDeadlineYm ? td!.slice(0, 7) : "";
    const atDeadlineMonth =
      lumpGc === "POINT_IN_TIME" && hasDeadlineYm
        ? corpusAtOrBeforeDeadline(p.monthly_trajectory ?? [], deadlineYm)
        : null;

    /** For one-time / growth goals with a deadline: corpus and gap at that month — not end of full simulation horizon. */
    let corpusLabel = "Simulated corpus (end of run)";
    let corpusValue = p.projected_final_amount;
    let shortfallLabel = "Shortfall vs end target";
    let shortfallValue = p.shortfall;
    let deadlineCorpusNote: string | null = null;

    if (
      p.corpus_at_deadline != null
      && p.inflation_adjusted_target_at_deadline != null
    ) {
      corpusLabel = "Simulated corpus (deadline month)";
      corpusValue = p.corpus_at_deadline;
      shortfallLabel = "Shortfall vs inflation-adjusted target (at deadline)";
      shortfallValue = p.shortfall_at_deadline ?? 0;
      if (
        p.monthly_trajectory?.length &&
        deadlineYm &&
        p.monthly_trajectory.at(-1)!.month.slice(0, 7) < deadlineYm
      ) {
        deadlineCorpusNote =
          `Simulation horizon ends before your deadline (${deadlineYm}). ` +
          `Corpus is the balance as of the last simulated month.`;
      }
    } else if (atDeadlineMonth) {
      corpusLabel = "Simulated corpus (deadline month)";
      corpusValue = atDeadlineMonth.value;
      if (atDeadlineMonth.truncated) {
        deadlineCorpusNote =
          `Simulation horizon ends before your deadline (${deadlineYm}). ` +
          `Corpus is the balance as of the last simulated month (${atDeadlineMonth.monthYm}).`;
      }
      if (nominalAtDeadline != null) {
        shortfallLabel = "Shortfall vs inflation-adjusted target (at deadline)";
        shortfallValue = Math.max(0, nominalAtDeadline - atDeadlineMonth.value);
      }
    }

    /** Gap as % of inflation-adjusted target — only when shortfall is vs that same nominal (deadline month). */
    const shortfallPctOfInflationAdjustedTarget =
      atDeadlineMonth != null &&
      nominalAtDeadline != null &&
      nominalAtDeadline > 0
        ? (shortfallValue / nominalAtDeadline) * 100
        : null;

    const chartData = lumpSumChartRows(p, g);
    const hPit = pctHeadline(p, lumpGc);

    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={pctVariant(hPit.pct)}>{hPit.label}</Badge>
          {g.id == null ? (
            <Badge variant="outline">Hypothetical</Badge>
          ) : null}
        </div>

        <dl className="grid gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Target (today&apos;s rupees)</dt>
            <dd className="font-medium tabular-nums">{formatCurrency(tgt)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Deadline</dt>
            <dd>{td ?? "—"}</dd>
          </div>
          {nominalAtDeadline != null ? (
            <div>
              <dt className="text-muted-foreground">
                Inflation-adjusted target (~{effInfl}%/yr)
              </dt>
              <dd className="tabular-nums">{formatCurrency(nominalAtDeadline)}</dd>
            </div>
          ) : null}
          <div>
            <dt className="text-muted-foreground">{corpusLabel}</dt>
            <dd className="tabular-nums">{formatCurrency(corpusValue)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{shortfallLabel}</dt>
            <dd
              className={
                shortfallValue > 0.01
                  ? "tabular-nums text-destructive"
                  : "tabular-nums"
              }
            >
              {formatCurrency(shortfallValue)}
              {shortfallPctOfInflationAdjustedTarget != null
                ? ` (${shortfallPctOfInflationAdjustedTarget.toFixed(1)}%)`
                : null}
            </dd>
          </div>
          {deadlineCorpusNote ? (
            <p className="text-xs text-amber-600 dark:text-amber-400 sm:col-span-2">
              {deadlineCorpusNote}
            </p>
          ) : null}
          <div>
            <dt className="text-muted-foreground">Projected completion</dt>
            <dd>{p.projected_completion_date ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Months to deadline (from run start)</dt>
            <dd className="tabular-nums">
              {monthsToDeadline != null ? monthsToDeadline : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Avg allocation / month</dt>
            <dd className="tabular-nums">
              {formatCurrency(p.monthly_allocation)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Starting balance</dt>
            <dd className="tabular-nums">
              {formatCurrency(g.starting_balance ?? 0)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Expected return</dt>
            <dd className="tabular-nums">{g.expected_return_rate ?? 10}%</dd>
          </div>
        </dl>

        {chartData.length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              Glide path (steady funding) vs simulated corpus
              {td ? (
                <span className="block">
                  Chart scoped to target month when a deadline is set.
                </span>
              ) : null}
            </p>
            <div className="h-[220px] w-full min-w-0 min-h-0">
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
                  <XAxis dataKey="m" tick={{ fontSize: 10 }} minTickGap={24} />
                  <YAxis
                    tickFormatter={formatInrChartAxis}
                    width={52}
                    tick={{ fontSize: 10 }}
                  />
                  <Tooltip
                    content={({ active, label, payload }) => {
                      if (!active || !payload?.length) return null;
                      return (
                        <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
                          <p className="font-medium">{label}</p>
                          <ul className="mt-1 space-y-0.5 text-xs">
                            {payload.map((item) => (
                              <li
                                key={String(item.name)}
                                className="flex justify-between gap-4 tabular-nums"
                              >
                                <span className="text-muted-foreground">{item.name}</span>
                                <span>
                                  {item.value != null
                                    ? formatInrChartAxis(Number(item.value))
                                    : "—"}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      );
                    }}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="expected"
                    name="Glide path (steady funding)"
                    stroke={CHART_GOAL_LINE}
                    dot={false}
                    strokeWidth={2}
                    connectNulls
                  />
                  <Line
                    type="monotone"
                    dataKey="actual"
                    name="Simulated corpus"
                    stroke={CHART_SERIES_COLORS[0]}
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No trajectory data for chart.</p>
        )}
      </div>
    );
  })();

  return (
    <Card>
      <CardHeader className="space-y-1 pb-2">
        <CardTitle className="text-base">Goal explorer</CardTitle>
        <CardDescription>
          Drag goals in the list to change priority (lower rank = funded first). Select a
          goal for read-only simulation metrics.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4 md:flex-row md:items-stretch">
          <div className="flex w-full flex-col border-border md:w-[24%] md:min-w-[160px] md:border-r md:pr-3">
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={sorted.map((g) => stableGoalKey(g))}
                strategy={verticalListSortingStrategy}
              >
                <div className="flex max-h-[420px] flex-col gap-1 overflow-y-auto pr-1">
                  {sorted.map((g) => (
                    <SortableGoalRow
                      key={stableGoalKey(g)}
                      goal={g}
                      selected={stableGoalKey(g) === selectedKey}
                      onSelect={() => setSelectedKey(stableGoalKey(g))}
                      projection={projectionFor(projections, g)}
                    />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-3 w-full shrink-0"
              onClick={onAddHypothetical}
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              Add hypothetical
            </Button>
          </div>

          <div className="min-w-0 flex-1 md:pl-2">{detailBody}</div>
        </div>
      </CardContent>
    </Card>
  );
}
