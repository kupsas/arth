"use client";

/**
 * Stacked area chart: monthly contribution per goal over time (sampled).
 */

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CHART_SERIES_COLORS } from "@/lib/chart-colors";
import { formatInrChartAxis } from "@/lib/utils";
import type { CascadeEvent, GoalProjection } from "@/lib/types";

const SAMPLE_EVERY = 3;

function buildRows(projections: GoalProjection[]) {
  if (!projections.length) return [];
  const tr = projections[0].monthly_trajectory;
  if (!tr.length) return [];

  const rows: Record<string, number | string>[] = [];
  for (let i = 0; i < tr.length; i += SAMPLE_EVERY) {
    const month = tr[i]?.month;
    if (!month) continue;
    const row: Record<string, number | string> = {
      monthLabel: month.slice(0, 7),
      monthIso: month,
    };
    for (const p of projections) {
      const snap = p.monthly_trajectory[i];
      row[p.goal_name] = snap?.monthly_contribution ?? 0;
    }
    rows.push(row);
  }
  return rows;
}

export function SurplusWaterfall({
  projections,
  cascadeEvents,
}: {
  projections: GoalProjection[];
  cascadeEvents: CascadeEvent[];
}) {
  const data = buildRows(projections);
  const names = projections.map((p) => p.goal_name);

  if (data.length === 0) {
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
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Surplus allocation over time</CardTitle>
        <CardDescription>
          Stacked monthly contributions (sampled every {SAMPLE_EVERY} months). Bands shrink
          when goals complete and cash flows elsewhere.
        </CardDescription>
      </CardHeader>
      {/* min-w-0: flex child can shrink; numeric height avoids % height issues inside flex Card */}
      <CardContent className="h-[320px] w-full min-w-0 min-h-0">
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
            <XAxis dataKey="monthLabel" tick={{ fontSize: 10 }} minTickGap={24} />
            <YAxis
              tickFormatter={formatInrChartAxis}
              width={56}
              tick={{ fontSize: 10 }}
            />
            <Tooltip
              content={({ active, label, payload }) => {
                if (!active || !payload?.length) return null;
                return (
                  <div className={RECHARTS_TOOLTIP_CARD_CLASS}>
                    <p className="font-medium">{label}</p>
                    <ul className="mt-1 space-y-0.5 text-xs tabular-nums">
                      {payload.map((item) => (
                        <li key={String(item.name)} className="flex justify-between gap-4">
                          <span className="text-muted-foreground">{item.name}</span>
                          <span>{formatInrChartAxis(Number(item.value))}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {cascadeEvents.slice(0, 8).map((ev, i) => {
              const m = ev.month?.slice(0, 7);
              const x = data.find((r) => String(r.monthLabel) === m)?.monthLabel;
              if (!x) return null;
              return (
                <ReferenceLine
                  key={`cascade-${i}`}
                  x={x}
                  stroke="var(--muted-foreground)"
                  strokeDasharray="4 4"
                  label={{ value: "✓", position: "top", fontSize: 10 }}
                />
              );
            })}
            {names.map((name, i) => (
              <Area
                key={name}
                type="monotone"
                dataKey={name}
                stackId="1"
                stroke={CHART_SERIES_COLORS[i % CHART_SERIES_COLORS.length]}
                fill={CHART_SERIES_COLORS[i % CHART_SERIES_COLORS.length]}
                fillOpacity={0.55}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
