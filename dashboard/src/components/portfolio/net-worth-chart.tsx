/**
 * net-worth-chart.tsx — net worth through time (F2.5.5).
 *
 * Calls GET /api/holdings/history with start/end + granularity. You can switch
 * between daily / weekly / monthly buckets and drag the date window with the
 * native date inputs (simple and accessible — no extra popover state).
 */

"use client";

import * as React from "react";
import { format, subMonths } from "date-fns";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useNetWorthHistory } from "@/hooks/use-portfolio";
import type { NetWorthGranularity } from "@/lib/types";
import {
  formatCurrency,
  formatCurrencyCompact,
  formatDate,
} from "@/lib/utils";

export interface NetWorthChartProps {
  userId: string;
}

function todayYmd() {
  return format(new Date(), "yyyy-MM-dd");
}

function monthsAgoYmd(months: number) {
  return format(subMonths(new Date(), months), "yyyy-MM-dd");
}

export function NetWorthChart({ userId }: NetWorthChartProps) {
  const [startDate, setStartDate] = React.useState(() => monthsAgoYmd(24));
  const [endDate, setEndDate] = React.useState(todayYmd);
  const [granularity, setGranularity] =
    React.useState<NetWorthGranularity>("monthly");

  const { data, isLoading, isFetching } = useNetWorthHistory(
    startDate,
    endDate,
    granularity,
    { user_id: userId },
  );

  const points = data?.points ?? [];
  const invalidRange = startDate > endDate;

  return (
    <Card>
      <CardHeader className="pb-2 space-y-3">
        <div>
          <CardTitle className="text-sm font-medium">Net worth trend</CardTitle>
          <p className="text-xs text-muted-foreground">
            Assets, liabilities, and net worth over the window you pick
          </p>
        </div>

        <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-end">
          <div className="grid gap-2 sm:grid-cols-2 lg:flex lg:gap-3">
            <div className="space-y-1">
              <Label htmlFor="nw-start" className="text-xs">
                Start
              </Label>
              <Input
                id="nw-start"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full sm:w-[11rem]"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="nw-end" className="text-xs">
                End
              </Label>
              <Input
                id="nw-end"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full sm:w-[11rem]"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-1">
            {(
              [
                ["daily", "Daily"],
                ["weekly", "Weekly"],
                ["monthly", "Monthly"],
              ] as const
            ).map(([g, label]) => (
              <Button
                key={g}
                type="button"
                size="sm"
                variant={granularity === g ? "default" : "outline"}
                className="h-8 text-xs"
                onClick={() => setGranularity(g)}
              >
                {label}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {invalidRange ? (
          <p className="text-sm text-destructive">Start date must be on or before end date.</p>
        ) : isLoading ? (
          <Skeleton className="h-[320px] w-full" />
        ) : points.length === 0 ? (
          <p className="text-sm text-muted-foreground py-16 text-center">
            No history points in this range — widen the dates or import prices.
          </p>
        ) : (
          <div className="relative h-[320px] w-full">
            {isFetching && !isLoading && (
              <div className="absolute right-2 top-0 z-10 rounded bg-muted/80 px-2 py-0.5 text-[10px] text-muted-foreground">
                Updating…
              </div>
            )}
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11 }}
                  tickFormatter={(d) => formatDate(String(d))}
                  minTickGap={24}
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  tickFormatter={(v) => formatCurrencyCompact(Number(v))}
                  width={56}
                />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (!active || !payload?.length) return null;
                    const row = payload[0]?.payload as {
                      date?: string;
                      net_worth?: number;
                      total_assets?: number;
                      total_liabilities?: number;
                    };
                    return (
                      <div className="rounded-lg border bg-card px-3 py-2 text-xs shadow-md">
                        <p className="font-medium mb-1">{formatDate(String(label))}</p>
                        <p className="text-muted-foreground">
                          Net:{" "}
                          <span className="text-foreground font-mono">
                            {formatCurrency(row.net_worth ?? 0)}
                          </span>
                        </p>
                        <p className="text-muted-foreground">
                          Assets:{" "}
                          <span className="text-foreground font-mono">
                            {formatCurrency(row.total_assets ?? 0)}
                          </span>
                        </p>
                        <p className="text-muted-foreground">
                          Liabilities:{" "}
                          <span className="text-foreground font-mono">
                            {formatCurrency(row.total_liabilities ?? 0)}
                          </span>
                        </p>
                      </div>
                    );
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  type="monotone"
                  dataKey="total_assets"
                  name="Assets"
                  stroke="var(--chart-2)"
                  dot={false}
                  strokeWidth={1}
                  strokeOpacity={0.65}
                />
                <Line
                  type="monotone"
                  dataKey="total_liabilities"
                  name="Liabilities"
                  stroke="var(--chart-sale)"
                  dot={false}
                  strokeWidth={1}
                  strokeOpacity={0.65}
                />
                <Area
                  type="monotone"
                  dataKey="net_worth"
                  name="Net worth"
                  stroke="var(--chart-1)"
                  fill="var(--chart-1)"
                  fillOpacity={0.2}
                  strokeWidth={2}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
