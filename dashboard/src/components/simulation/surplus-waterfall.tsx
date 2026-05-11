"use client";

/**
 * Stacked area chart: surplus allocation per goal over time.
 *
 * - Cadence: monthly (one point per simulated month) or yearly (sums per calendar year).
 * - Stack: absolute (₹) or 100% (share of total allocation in each period; tooltip still shows ₹).
 */

import { useEffect, useMemo, useState } from "react";

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CHART_SERIES_COLORS } from "@/lib/chart-colors";
import { formatInrChartAxis, formatPercent } from "@/lib/utils";
import type { GoalProjection, MonthlyNetWorth } from "@/lib/types";

/** Muted fill/stroke for non-focused series when one legend goal is selected. */
const DIM_SERIES_COLOR = "var(--muted-foreground)";

/**
 * Stacked-area series for surplus that was not assigned to any goal (`unallocated_surplus` from API).
 * Shown as the top band in grey so it is visible in the chart, not only in the tooltip.
 */
const UNALLOCATED_SURPLUS_SERIES_NAME = "Unallocated";

/** Grey stack segment for unallocated surplus (distinct from goal palette in CHART_SERIES_COLORS). */
const UNALLOCATED_FILL = "var(--muted)";
const UNALLOCATED_STROKE = "var(--muted-foreground)";

/**
 * Raw INR per goal for one chart row (before optional % transform).
 * `_surplusLeft` / `_totalMonthlySurplus` mirror the simulation engine (from `net_worth_projection`);
 * prefixed so they never collide with goal names.
 * `[UNALLOCATED_SURPLUS_SERIES_NAME]` duplicates unallocated INR for the stack (same value as `_surplusLeft` when present).
 */
type InrRow = {
  monthLabel: string;
  _surplusLeft?: number;
  _totalMonthlySurplus?: number;
  [goalName: string]: string | number | undefined;
};

/** After normalising to 100% per row; `_inr` holds original rupee amounts for the tooltip. */
type PercentRow = {
  monthLabel: string;
  _inr: Record<string, number>;
  _surplusLeft?: number;
  _totalMonthlySurplus?: number;
  [goalName: string]: string | number | Record<string, number> | undefined;
};

function buildMonthlyInrRows(
  projections: GoalProjection[],
  netWorth: MonthlyNetWorth[],
): InrRow[] {
  if (!projections.length) return [];
  const tr = projections[0].monthly_trajectory;
  if (!tr.length) return [];

  const rows: InrRow[] = [];
  for (let i = 0; i < tr.length; i++) {
    const month = tr[i]?.month;
    if (!month) continue;
    const row: InrRow = { monthLabel: month.slice(0, 7) };
    const nw = netWorth[i];
    if (nw) {
      const left = nw.unallocated_surplus ?? 0;
      row._surplusLeft = left;
      row[UNALLOCATED_SURPLUS_SERIES_NAME] = left;
      row._totalMonthlySurplus = nw.monthly_surplus_pool ?? 0;
    } else {
      row[UNALLOCATED_SURPLUS_SERIES_NAME] = 0;
    }
    for (const p of projections) {
      const snap = p.monthly_trajectory[i];
      row[p.goal_name] = snap?.monthly_contribution ?? 0;
    }
    rows.push(row);
  }
  return rows;
}

function buildYearlyInrRows(
  projections: GoalProjection[],
  netWorth: MonthlyNetWorth[],
): InrRow[] {
  if (!projections.length) return [];
  const tr = projections[0].monthly_trajectory;
  if (!tr.length) return [];

  /** year -> goal -> sum of monthly_contribution */
  const yearMap = new Map<string, Record<string, number>>();
  /** year -> summed surplus metadata (12 months aggregated for yearly view) */
  const yearSurplus = new Map<string, { unalloc: number; pool: number }>();

  for (let i = 0; i < tr.length; i++) {
    const month = tr[i]?.month;
    if (!month) continue;
    const year = month.slice(0, 4);
    if (!yearMap.has(year)) {
      yearMap.set(year, {});
    }
    if (!yearSurplus.has(year)) {
      yearSurplus.set(year, { unalloc: 0, pool: 0 });
    }
    const bucket = yearMap.get(year)!;
    const sb = yearSurplus.get(year)!;
    const nw = netWorth[i];
    if (nw) {
      sb.unalloc += nw.unallocated_surplus ?? 0;
      sb.pool += nw.monthly_surplus_pool ?? 0;
    }
    for (const p of projections) {
      const v = p.monthly_trajectory[i]?.monthly_contribution ?? 0;
      bucket[p.goal_name] = (bucket[p.goal_name] ?? 0) + v;
    }
  }

  const years = [...yearMap.keys()].sort((a, b) => a.localeCompare(b));
  return years.map((y) => {
    const bucket = yearMap.get(y)!;
    const row: InrRow = { monthLabel: y };
    const s = yearSurplus.get(y);
    if (s) {
      row._surplusLeft = s.unalloc;
      row[UNALLOCATED_SURPLUS_SERIES_NAME] = s.unalloc;
      row._totalMonthlySurplus = s.pool;
    } else {
      row[UNALLOCATED_SURPLUS_SERIES_NAME] = 0;
    }
    for (const p of projections) {
      row[p.goal_name] = bucket[p.goal_name] ?? 0;
    }
    return row;
  });
}

function toPercentRows(rows: InrRow[], goalNames: string[]): PercentRow[] {
  return rows.map((row) => {
    const unalloc = Number(row[UNALLOCATED_SURPLUS_SERIES_NAME]) || 0;
    const goalsTotal = goalNames.reduce(
      (s, n) => s + (Number(row[n]) || 0),
      0,
    );
    // Engine invariant: allocated + unallocated = pool — include unallocated so the stack hits 100%.
    const total = goalsTotal + unalloc;
    const out: PercentRow = {
      monthLabel: row.monthLabel,
      _inr: {},
      _surplusLeft: row._surplusLeft,
      _totalMonthlySurplus: row._totalMonthlySurplus,
    };
    for (const n of goalNames) {
      const v = Number(row[n]) || 0;
      out._inr[n] = v;
      out[n] = total > 0 ? (v / total) * 100 : 0;
    }
    out._inr[UNALLOCATED_SURPLUS_SERIES_NAME] = unalloc;
    out[UNALLOCATED_SURPLUS_SERIES_NAME] = total > 0 ? (unalloc / total) * 100 : 0;
    return out;
  });
}

type LegendPayloadEntry = {
  value?: string;
  dataKey?: string | number;
  color?: string;
};

type Cadence = "monthly" | "yearly";
type StackMode = "absolute" | "percent";

export function SurplusWaterfall({
  projections,
  netWorthProjection,
  focusedGoal,
  onFocusedGoalChange,
}: {
  projections: GoalProjection[];
  /** Same length/order as each goal’s `monthly_trajectory` — supplies per-month surplus pool + unallocated. */
  netWorthProjection: MonthlyNetWorth[];
  /**
   * Which stack keeps full color on the chart (null = all vivid). Controlled by the parent so the
   * goal list and legend can stay in sync.
   */
  focusedGoal: string | null;
  onFocusedGoalChange: (goalName: string | null) => void;
}) {
  const names = projections.map((p) => p.goal_name);

  const [cadence, setCadence] = useState<Cadence>("monthly");
  const [stackMode, setStackMode] = useState<StackMode>("absolute");

  /** Goal names plus the unallocated stack — legend focus can isolate any of these. */
  const validFocusKeys = useMemo(
    () =>
      new Set([...projections.map((p) => p.goal_name), UNALLOCATED_SURPLUS_SERIES_NAME]),
    [projections],
  );

  useEffect(() => {
    if (focusedGoal && !validFocusKeys.has(focusedGoal)) {
      onFocusedGoalChange(null);
    }
  }, [focusedGoal, validFocusKeys, onFocusedGoalChange]);

  const inrRows = useMemo(() => {
    if (!projections.length) return [];
    return cadence === "monthly"
      ? buildMonthlyInrRows(projections, netWorthProjection)
      : buildYearlyInrRows(projections, netWorthProjection);
  }, [cadence, projections, netWorthProjection]);

  /** Recharts row type is permissive; we attach `_inr` only in 100% mode for tooltips. */
  const chartData = useMemo((): Record<string, unknown>[] => {
    if (!inrRows.length) return [];
    if (stackMode === "percent") {
      return toPercentRows(inrRows, names) as Record<string, unknown>[];
    }
    return inrRows as Record<string, unknown>[];
  }, [inrRows, names, stackMode]);

  const description = useMemo(() => {
    if (stackMode === "percent") {
      return cadence === "monthly"
        ? "Share of monthly surplus: goals + unallocated (always sums to 100%). Tooltip shows ₹."
        : "Share of each calendar year’s surplus: goals + unallocated (100% per year). Tooltip shows ₹.";
    }
    return cadence === "monthly"
      ? "Monthly allocation in ₹. Bands shrink when goals complete and cash flows elsewhere."
      : "Total surplus allocated per calendar year (₹), summed from monthly contributions.";
  }, [cadence, stackMode]);

  if (chartData.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Surplus allocation over time</CardTitle>
          <CardDescription>No trajectory data yet.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0 pb-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base">Surplus allocation over time</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex gap-1" role="group" aria-label="Time granularity">
            <Button
              type="button"
              size="sm"
              variant={cadence === "monthly" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setCadence("monthly")}
            >
              Monthly
            </Button>
            <Button
              type="button"
              size="sm"
              variant={cadence === "yearly" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setCadence("yearly")}
            >
              Yearly
            </Button>
          </div>
          <div className="flex gap-1" role="group" aria-label="Stack scale">
            <Button
              type="button"
              size="sm"
              variant={stackMode === "absolute" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setStackMode("absolute")}
            >
              Amount
            </Button>
            <Button
              type="button"
              size="sm"
              variant={stackMode === "percent" ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setStackMode("percent")}
            >
              100%
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="h-[320px] w-full min-w-0 min-h-0">
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart
            key={`${cadence}-${stackMode}`}
            data={chartData}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          >
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
            <XAxis dataKey="monthLabel" tick={{ fontSize: 10 }} minTickGap={24} />
            <YAxis
              tickFormatter={(v) =>
                stackMode === "percent"
                  ? formatPercent(Number(v), 0)
                  : formatInrChartAxis(Number(v))
              }
              domain={stackMode === "percent" ? [0, 100] : undefined}
              width={56}
              tick={{ fontSize: 10 }}
            />
            <Tooltip
              content={({ active, label, payload }) => {
                if (!active || !payload?.length) return null;
                const point = payload[0]?.payload as
                  | Record<string, unknown>
                  | undefined;
                const inrLookup =
                  stackMode === "percent" &&
                  point &&
                  typeof point._inr === "object" &&
                  point._inr !== null
                    ? (point._inr as Record<string, number>)
                    : null;

                const totalMonthlySurplus =
                  typeof point?._totalMonthlySurplus === "number"
                    ? point._totalMonthlySurplus
                    : undefined;

                return (
                  <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
                    <p className="font-medium">{label}</p>
                    <ul className="mt-1 space-y-0.5 text-xs tabular-nums">
                      {payload.map((item) => {
                        const name = String(item.name);
                        const num = Number(item.value);
                        const inr =
                          inrLookup?.[name] ??
                          (stackMode === "absolute"
                            ? num
                            : undefined);
                        return (
                          <li key={name} className="flex justify-between gap-4">
                            <span className="text-muted-foreground">{name}</span>
                            <span>
                              {stackMode === "percent" ? (
                                <>
                                  {formatPercent(num, 1)}
                                  {inr !== undefined && (
                                    <span className="text-muted-foreground">
                                      {" "}
                                      ({formatInrChartAxis(inr)})
                                    </span>
                                  )}
                                </>
                              ) : (
                                formatInrChartAxis(num)
                              )}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                    {totalMonthlySurplus !== undefined && (
                      <ul className="mt-2 space-y-0.5 border-t border-border pt-2 text-xs tabular-nums">
                        <li className="flex justify-between gap-4">
                          <span className="text-muted-foreground">
                            {cadence === "yearly"
                              ? "Total surplus (year)"
                              : "Total surplus pool"}
                          </span>
                          <span>{formatInrChartAxis(totalMonthlySurplus)}</span>
                        </li>
                      </ul>
                    )}
                  </div>
                );
              }}
            />
            <Legend
              verticalAlign="bottom"
              height={56}
              wrapperStyle={{ fontSize: 11 }}
              content={({ payload }) => (
                <ul className="flex flex-wrap justify-center gap-x-3 gap-y-1 pt-2">
                  {(payload as LegendPayloadEntry[] | undefined)?.map((entry) => {
                    const key = String(entry.dataKey ?? entry.value ?? "");
                    if (!key) return null;
                    const isDimmed = focusedGoal !== null && focusedGoal !== key;
                    return (
                      <li key={key}>
                        <button
                          type="button"
                          className="inline-flex max-w-[200px] items-center gap-1.5 rounded-sm px-1 py-0.5 text-left transition-colors hover:bg-muted/50"
                          style={{ opacity: isDimmed ? 0.55 : 1 }}
                          onClick={() =>
                            onFocusedGoalChange(focusedGoal === key ? null : key)
                          }
                          title={
                            focusedGoal === key
                              ? "Click to show all goals"
                              : "Isolate this goal on the chart"
                          }
                        >
                          <span
                            className="inline-block h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: entry.color }}
                            aria-hidden
                          />
                          <span
                            className={
                              isDimmed ? "text-muted-foreground truncate" : "truncate"
                            }
                          >
                            {entry.value ?? key}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            />
            {names.map((name, i) => {
              const seriesColor = CHART_SERIES_COLORS[i % CHART_SERIES_COLORS.length];
              const isFocused =
                focusedGoal === null ? true : focusedGoal === name;
              const stroke = isFocused ? seriesColor : DIM_SERIES_COLOR;
              const fill = isFocused ? seriesColor : DIM_SERIES_COLOR;
              const fillOpacity = isFocused ? 0.55 : 0.28;
              return (
                <Area
                  key={name}
                  type="monotone"
                  dataKey={name}
                  stackId="1"
                  stroke={stroke}
                  fill={fill}
                  fillOpacity={fillOpacity}
                />
              );
            })}
            {/* Top of stack: surplus not assigned to goals (same series as tooltip / engine `unallocated_surplus`). */}
            <Area
              key={UNALLOCATED_SURPLUS_SERIES_NAME}
              type="monotone"
              name={UNALLOCATED_SURPLUS_SERIES_NAME}
              dataKey={UNALLOCATED_SURPLUS_SERIES_NAME}
              stackId="1"
              stroke={
                focusedGoal === null || focusedGoal === UNALLOCATED_SURPLUS_SERIES_NAME
                  ? UNALLOCATED_STROKE
                  : DIM_SERIES_COLOR
              }
              fill={
                focusedGoal === null || focusedGoal === UNALLOCATED_SURPLUS_SERIES_NAME
                  ? UNALLOCATED_FILL
                  : DIM_SERIES_COLOR
              }
              fillOpacity={
                focusedGoal === null || focusedGoal === UNALLOCATED_SURPLUS_SERIES_NAME
                  ? 0.65
                  : 0.28
              }
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
