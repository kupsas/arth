/**
 * portfolio-value-trend.tsx — Recharts area + gradient; data from B3 trend API.
 * Pill range selector: 3M / 6M / 12M / All (no 1M — monthly points only).
 */

"use client";

import * as React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePortfolioValueTrend } from "@/hooks/use-portfolio";
import type { PortfolioValueTrendRange } from "@/lib/types";
import {
  cn,
  formatCurrency,
  formatInrChartAxis,
  formatMonthShort,
  formatPercent,
} from "@/lib/utils";

const RANGES: { value: PortfolioValueTrendRange; label: string }[] = [
  { value: "3M", label: "3M" },
  { value: "6M", label: "6M" },
  { value: "12M", label: "12M" },
  { value: "all", label: "All" },
];

export interface PortfolioValueTrendProps {
  userId: string;
}

export function PortfolioValueTrend({ userId }: PortfolioValueTrendProps) {
  const gradId = React.useId().replace(/:/g, "");
  const [range, setRange] = React.useState<PortfolioValueTrendRange>("12M");
  const { data, isLoading } = usePortfolioValueTrend(range, {
    user_id: userId,
  });

  const chartData = React.useMemo(
    () =>
      (data?.points ?? []).map((p) => ({
        ...p,
        month: formatMonthShort(p.date),
      })),
    [data?.points],
  );

  /** "MUTUAL_FUND" → "Mutual fund" — matches asset-allocation donut labels. */
  const labelPretty = React.useCallback((s: string) => {
    return s
      .split(/[_\s]+/)
      .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
      .join(" ");
  }, []);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between space-y-0">
        <div>
          <CardTitle className="text-sm font-medium">
            Portfolio value trend
          </CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            Monthly total (holdings only). Hover a point for % change vs prior
            month.
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {RANGES.map((r) => (
            <Button
              key={r.value}
              type="button"
              size="sm"
              variant={range === r.value ? "default" : "outline"}
              className="rounded-full h-7 px-2.5 text-xs"
              onClick={() => setRange(r.value)}
            >
              {r.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {isLoading ? (
          <Skeleton className="h-[280px] w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-12 text-center">
            No history points yet — add holdings or widen the range.
          </p>
        ) : (
          <div className="h-[280px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={chartData}
                margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient
                    id={`pvFill-${gradId}`}
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop
                      offset="0%"
                      stopColor="var(--chart-1)"
                      stopOpacity={0.35}
                    />
                    <stop
                      offset="100%"
                      stopColor="var(--chart-1)"
                      stopOpacity={0}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  tickFormatter={formatInrChartAxis}
                  width={48}
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.[0]) return null;
                    const row = payload[0].payload as {
                      month: string;
                      total_portfolio_value: number;
                      pct_change_vs_prior_month: number | null;
                      by_asset_class?: Record<string, number>;
                    };
                    const ch = row.pct_change_vs_prior_month;
                    const breakdown = Object.entries(row.by_asset_class ?? {})
                      .filter(([, amt]) => amt > 0)
                      .sort((a, b) => b[1] - a[1]);
                    return (
                      <div className="rounded-lg border bg-card px-3 py-2 text-xs shadow-md max-w-[240px]">
                        <p className="font-medium">{row.month}</p>
                        <p className="text-muted-foreground">
                          {formatCurrency(row.total_portfolio_value)}
                        </p>
                        {breakdown.length > 0 && (
                          <ul className="mt-2 space-y-1 border-t border-border pt-2">
                            {breakdown.map(([ac, amt]) => (
                              <li
                                key={ac}
                                className="flex justify-between gap-3 text-muted-foreground"
                              >
                                <span className="truncate" title={ac}>
                                  {labelPretty(ac)}
                                </span>
                                <span className="font-mono text-foreground shrink-0">
                                  {formatCurrency(amt)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {ch != null ? (
                          <p
                            className={cn(
                              breakdown.length > 0 && "mt-2",
                              ch >= 0
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-red-600 dark:text-red-400",
                            )}
                          >
                            {ch >= 0 ? "+" : ""}
                            {formatPercent(ch, 2)} vs prior month
                          </p>
                        ) : (
                          <p
                            className={cn(
                              breakdown.length > 0 && "mt-2",
                              "text-muted-foreground",
                            )}
                          >
                            First month
                          </p>
                        )}
                      </div>
                    );
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="total_portfolio_value"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  fill={`url(#pvFill-${gradId})`}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
