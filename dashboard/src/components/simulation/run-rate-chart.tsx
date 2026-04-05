"use client";

/**
 * For a POINT_IN_TIME goal: “glide path” vs simulated pot balance.
 *
 * - **Glide path** (`target_at_month` from the engine): if you funded the *steady*
 *   monthly amount the engine assumed for that goal, where your corpus would be each
 *   month (compound growth + those contributions). It is only defined for one-time
 *   targets, not for recurring cash-flow goals.
 * - **Simulated corpus**: actual month-by-month balance after the real allocation
 *   (surplus may be short, priorities may skip funding). Aggregate view sums all goal pots.
 */

import * as React from "react";
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

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CHART_GOAL_LINE, CHART_SERIES_COLORS } from "@/lib/chart-colors";
import { formatInrChartAxis } from "@/lib/utils";
import type { GoalProjection, MonthlyNetWorth, SimulationGoal } from "@/lib/types";

const SAMPLE = 3;

/** Match projection row to draft goal so we can read `target_date` (not on GoalProjection). */
function targetDateForProjection(
  p: GoalProjection,
  goals: SimulationGoal[] | undefined,
): string | null {
  if (!goals?.length) return null;
  const g = goals.find(
    (x) =>
      (p.goal_id != null && x.id != null && Number(x.id) === Number(p.goal_id)) ||
      x.name === p.goal_name,
  );
  if (!g || String(g.goal_class).toUpperCase() !== "POINT_IN_TIME") return null;
  const td = g.target_date?.trim();
  return td || null;
}

/**
 * One-time goals: only plot through the **target month** so the x-axis matches “when I need
 * the money,” not the full global simulation horizon (e.g. 20y). The engine still runs the
 * full horizon internally; this chart is scoped to the goal’s deadline.
 */
function goalSeries(p: GoalProjection, goals: SimulationGoal[] | undefined) {
  const rows: { m: string; expected: number | null; actual: number }[] = [];
  const cutoff = targetDateForProjection(p, goals);
  let tr = p.monthly_trajectory ?? [];
  if (cutoff) {
    const ym = cutoff.slice(0, 7);
    tr = tr.filter((s) => s.month.slice(0, 7) <= ym);
  }
  for (let i = 0; i < tr.length; i += SAMPLE) {
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

function aggregateSeries(nw: MonthlyNetWorth[]) {
  const rows: { m: string; expected: number | null; actual: number }[] = [];
  for (let i = 0; i < nw.length; i += SAMPLE) {
    const s = nw[i];
    if (!s) continue;
    rows.push({
      m: s.month.slice(0, 7),
      expected: null,
      actual: s.total_value,
    });
  }
  return rows;
}

export function RunRateChart({
  projections,
  netWorthProjection,
  goals,
}: {
  projections: GoalProjection[];
  netWorthProjection: MonthlyNetWorth[];
  /** Draft goals — used to scope one-time goals to `target_date` on the chart. */
  goals?: SimulationGoal[];
}) {
  const [selection, setSelection] = React.useState<string>("aggregate");

  const indexed = projections.filter((p) => p.monthly_trajectory?.length);

  const data = React.useMemo(() => {
    if (selection === "aggregate") {
      return aggregateSeries(netWorthProjection);
    }
    const idx = Number.parseInt(selection, 10);
    if (!Number.isFinite(idx) || !indexed[idx]) return [];
    return goalSeries(indexed[idx], goals);
  }, [selection, netWorthProjection, indexed, goals]);

  const selectedProjection =
    selection !== "aggregate" && Number.isFinite(Number.parseInt(selection, 10))
      ? indexed[Number.parseInt(selection, 10)]
      : undefined;

  const title =
    selection === "aggregate"
      ? "Total across goal pots (simulation). Glide path is hidden in aggregate view."
      : selectedProjection?.goal_name ?? "Goal";

  const matchedDraftGoal = React.useMemo(() => {
    if (!selectedProjection || !goals?.length) return undefined;
    return goals.find(
      (x) =>
        (selectedProjection.goal_id != null &&
          x.id != null &&
          Number(x.id) === Number(selectedProjection.goal_id)) ||
        x.name === selectedProjection.goal_name,
    );
  }, [selectedProjection, goals]);

  const targetCutoffLabel = React.useMemo(() => {
    if (!selectedProjection) return null;
    const td = targetDateForProjection(selectedProjection, goals);
    if (!td) return null;
    try {
      const d = new Date(td + (td.length <= 10 ? "T12:00:00" : ""));
      if (Number.isNaN(d.getTime())) return td.slice(0, 7);
      return d.toLocaleString(undefined, { month: "short", year: "numeric" });
    } catch {
      return td.slice(0, 7);
    }
  }, [selectedProjection, goals]);

  if (!data.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Run rate — glide path vs simulated balance</CardTitle>
          <CardDescription>No data.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-2 space-y-0 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-1">
          <CardTitle className="text-base">Run rate — glide path vs simulated balance</CardTitle>
          <CardDescription className="text-xs leading-relaxed">
            {selection === "aggregate" ? (
              <>
                <span className="font-medium text-foreground">{title}</span>
                <span className="block text-muted-foreground">
                  One line: total corpus across goals each month (same as summing each goal’s
                  simulated balance).
                </span>
              </>
            ) : (
              <>
                <span className="font-medium text-foreground">{title}</span>
                <span className="block text-muted-foreground">
                  Glide path: hypothetical balance if the steady monthly amount were fully
                  invested each month (one-time targets). Simulated balance: what you actually
                  accumulated after surplus limits and priority. A gap means you are behind
                  that ideal funding path; compare simulated balance at the target month to
                  your inflated target to see if the down payment is funded.
                  {targetCutoffLabel ? (
                    <span className="mt-1 block">
                      Chart ends at your target ({targetCutoffLabel}), not the full simulation
                      horizon.
                    </span>
                  ) : String(matchedDraftGoal?.goal_class ?? "").toUpperCase() ===
                    "RECURRING_CASH_FLOW" ? (
                    <span className="mt-1 block">
                      Recurring goals only show the simulated line — no glide path.
                    </span>
                  ) : (
                    <span className="mt-1 block">
                      One-time goal without a target date: the chart still uses the full
                      simulation horizon (set a target date to align the x-axis with your
                      deadline).
                    </span>
                  )}
                </span>
              </>
            )}
          </CardDescription>
        </div>
        <Select
          value={selection}
          onValueChange={(v) => {
            if (v != null) setSelection(v);
          }}
        >
          <SelectTrigger className="w-[220px]">
            <SelectValue>
              {selection === "aggregate"
                ? "All goals (aggregate)"
                : selectedProjection?.goal_name ?? "Select goal"}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="aggregate">All goals (aggregate)</SelectItem>
            {indexed.map((p, i) => (
              <SelectItem key={p.goal_name} value={String(i)}>
                {p.goal_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      {/* min-w-0: flex child can shrink; numeric height: ResponsiveContainer avoids % inside flex column */}
      <CardContent className="h-[300px] w-full min-w-0 min-h-0">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
            <XAxis dataKey="m" tick={{ fontSize: 10 }} minTickGap={20} />
            <YAxis tickFormatter={formatInrChartAxis} width={56} tick={{ fontSize: 10 }} />
            <Tooltip
              content={({ active, label, payload }) => {
                if (!active || !payload?.length) return null;
                return (
                  <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
                    <p className="font-medium">{label}</p>
                    <ul className="mt-1 space-y-0.5 text-xs">
                      {payload.map((item) => (
                        <li key={String(item.name)} className="flex justify-between gap-4 tabular-nums">
                          <span className="text-muted-foreground">{item.name}</span>
                          <span>
                            {item.value != null ? formatInrChartAxis(Number(item.value)) : "—"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              }}
            />
            <Legend />
            {selection !== "aggregate" && (
              <Line
                type="monotone"
                dataKey="expected"
                name="Glide path (steady funding)"
                stroke={CHART_GOAL_LINE}
                dot={false}
                strokeWidth={2}
                connectNulls
              />
            )}
            <Line
              type="monotone"
              dataKey="actual"
              name={selection === "aggregate" ? "Total simulated corpus" : "Simulated corpus"}
              stroke={CHART_SERIES_COLORS[0]}
              dot={false}
              strokeWidth={2}
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
