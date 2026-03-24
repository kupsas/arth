/**
 * asset-allocation.tsx — three ways to slice the same portfolio (F2.5.3).
 *
 * The API returns percentage weights (0–100) keyed by class / platform name.
 * We render:
 *   1. Donut — asset class
 *   2. Horizontal stacked bar — liquidity class (one bar, segments sum to ~100%)
 *   3. Donut — account platform
 */

"use client";

import * as React from "react";
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useHoldingsSummary } from "@/hooks/use-portfolio";
import { formatPercent } from "@/lib/utils";

const PIE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

export interface AssetAllocationProps {
  userId: string;
}

function recordToPieData(rec: Record<string, number> | undefined) {
  if (!rec) return [];
  return Object.entries(rec)
    .map(([name, value]) => ({ name, value }))
    .filter((d) => d.value > 0.001)
    .sort((a, b) => b.value - a.value);
}

/** Turn "MUTUAL_FUND" into "Mutual Fund" for chart labels. */
function labelPretty(s: string) {
  return s
    .split(/[_\s]+/)
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

export function AssetAllocation({ userId }: AssetAllocationProps) {
  const { data, isLoading } = useHoldingsSummary({ user_id: userId });
  const allocation = data?.allocation;

  const byClass = React.useMemo(
    () => recordToPieData(allocation?.by_asset_class),
    [allocation?.by_asset_class],
  );
  const byLiq = React.useMemo(
    () => allocation?.by_liquidity_class ?? {},
    [allocation?.by_liquidity_class],
  );
  const byPlatform = React.useMemo(
    () => recordToPieData(allocation?.by_account_platform),
    [allocation?.by_account_platform],
  );

  // Single row for a stacked horizontal bar: one category "Portfolio", keys = liquidity buckets
  const liquidityBarData = React.useMemo(() => {
    const keys = Object.keys(byLiq).filter((k) => (byLiq[k] ?? 0) > 0.001);
    if (keys.length === 0) return [];
    const row: Record<string, string | number> = { bucket: "All holdings" };
    for (const k of keys) {
      row[k] = byLiq[k] ?? 0;
    }
    return [row];
  }, [byLiq]);

  const liquidityKeys = React.useMemo(
    () => Object.keys(byLiq).filter((k) => (byLiq[k] ?? 0) > 0.001),
    [byLiq],
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Asset allocation</CardTitle>
        <p className="text-xs text-muted-foreground">
          Percent of gross assets — same data, different cuts
        </p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-[280px] w-full" />
        ) : (
          <Tabs defaultValue="class" className="w-full">
            <TabsList variant="line" className="mb-3 h-8 w-full min-w-0 justify-start">
              <TabsTrigger value="class" className="text-xs">
                Asset class
              </TabsTrigger>
              <TabsTrigger value="liquidity" className="text-xs">
                Liquidity
              </TabsTrigger>
              <TabsTrigger value="platform" className="text-xs">
                Platform
              </TabsTrigger>
            </TabsList>

            <TabsContent value="class" className="mt-0">
              {byClass.length === 0 ? (
                <Empty />
              ) : (
                <div className="h-[260px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={byClass}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={56}
                        outerRadius={88}
                        paddingAngle={1}
                      >
                        {byClass.map((_, i) => (
                          <Cell
                            key={i}
                            fill={PIE_COLORS[i % PIE_COLORS.length]}
                            stroke="transparent"
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v: number) => [formatPercent(v), "Weight"]}
                        labelFormatter={(name) => labelPretty(String(name))}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </TabsContent>

            <TabsContent value="liquidity" className="mt-0">
              {liquidityBarData.length === 0 ? (
                <Empty />
              ) : (
                <div className="h-[260px] w-full pr-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      layout="vertical"
                      data={liquidityBarData}
                      margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
                    >
                      <XAxis type="number" domain={[0, 100]} tickFormatter={(x) => `${x}%`} />
                      <YAxis type="category" dataKey="bucket" width={100} hide />
                      {liquidityKeys.map((key, i) => (
                        <Bar
                          key={key}
                          dataKey={key}
                          stackId="a"
                          fill={PIE_COLORS[i % PIE_COLORS.length]}
                          name={labelPretty(key)}
                        />
                      ))}
                      <Tooltip formatter={(v: number) => formatPercent(v)} />
                    </BarChart>
                  </ResponsiveContainer>
                  <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {liquidityKeys.map((key, i) => (
                      <li key={key} className="flex items-center gap-1.5">
                        <span
                          className="size-2 rounded-sm"
                          style={{
                            backgroundColor: PIE_COLORS[i % PIE_COLORS.length],
                          }}
                        />
                        {labelPretty(key)} ({formatPercent(byLiq[key] ?? 0)})
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </TabsContent>

            <TabsContent value="platform" className="mt-0">
              {byPlatform.length === 0 ? (
                <Empty />
              ) : (
                <div className="h-[260px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={byPlatform}
                        dataKey="value"
                        nameKey="name"
                        cx="50%"
                        cy="50%"
                        innerRadius={56}
                        outerRadius={88}
                        paddingAngle={1}
                      >
                        {byPlatform.map((_, i) => (
                          <Cell
                            key={i}
                            fill={PIE_COLORS[i % PIE_COLORS.length]}
                            stroke="transparent"
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v: number) => [formatPercent(v), "Weight"]}
                        labelFormatter={(name) => String(name)}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </TabsContent>
          </Tabs>
        )}
      </CardContent>
    </Card>
  );
}

function Empty() {
  return (
    <p className="text-sm text-muted-foreground py-12 text-center">
      No allocation data yet — add holdings or check filters.
    </p>
  );
}
