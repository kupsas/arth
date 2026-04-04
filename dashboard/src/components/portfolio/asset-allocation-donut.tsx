/**
 * asset-allocation-donut.tsx — single donut by asset class (rupee values from B3
 * asset_class_breakdown). Replaces the older multi-tab asset-allocation card.
 */

"use client";

import * as React from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { RechartsPieSliceTooltip } from "@/components/dashboard/recharts-tooltip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePortfolioSummary } from "@/hooks/use-portfolio";
import { CHART_SERIES_COLORS } from "@/lib/chart-colors";
import { prettyAssetClassLabel } from "@/lib/holdings-display";
import { formatCurrency, formatPercent } from "@/lib/utils";

export interface AssetAllocationDonutProps {
  userId: string;
}

export function AssetAllocationDonut({ userId }: AssetAllocationDonutProps) {
  const { data, isLoading } = usePortfolioSummary({ user_id: userId });
  const breakdown = data?.asset_class_breakdown;

  const pieData = React.useMemo(() => {
    if (!breakdown) return [];
    const total = Object.values(breakdown).reduce(
      (s, row) => s + (row.current_value ?? 0),
      0,
    );
    if (total <= 0) return [];
    return Object.entries(breakdown)
      .map(([key, row]) => ({
        name: prettyAssetClassLabel(key),
        rawKey: key,
        value: row.current_value,
        pct: (100 * row.current_value) / total,
      }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value);
  }, [breakdown]);

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Asset allocation</CardTitle>
        <p className="text-xs text-muted-foreground">
          Share of portfolio by asset class (current value)
        </p>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
            <Skeleton className="size-[148px] shrink-0 rounded-full" />
            <div className="w-full min-w-0 flex-1 space-y-2 sm:pt-1">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          </div>
        ) : pieData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No allocation data yet.
          </p>
        ) : (
          <div className="flex w-full flex-col items-center gap-4 sm:flex-row sm:items-start sm:gap-5">
            {/* Left: compact donut so the card stays short; legend uses the rest. */}
            <div className="h-[148px] w-[148px] shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={40}
                    outerRadius={62}
                    paddingAngle={1}
                  >
                    {pieData.map((_, i) => (
                      <Cell
                        key={i}
                        fill={
                          CHART_SERIES_COLORS[
                            i % CHART_SERIES_COLORS.length
                          ]
                        }
                        stroke="transparent"
                      />
                    ))}
                  </Pie>
                  <Tooltip
                    content={(props) => <RechartsPieSliceTooltip {...props} />}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="min-w-0 w-full flex-1 overflow-auto sm:max-h-[200px]">
              <Table
                className="table-fixed text-xs"
                aria-label="Allocation by asset class"
              >
                <colgroup>
                  <col className="w-8" />
                  <col />
                  <col className="w-21" />
                  <col className="w-13" />
                </colgroup>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8 w-8 px-1.5">
                      <span className="sr-only">Color</span>
                    </TableHead>
                    <TableHead className="h-8 px-1.5 text-muted-foreground">
                      Class
                    </TableHead>
                    <TableHead className="h-8 px-1.5 text-right text-muted-foreground">
                      Value
                    </TableHead>
                    <TableHead className="h-8 px-1.5 text-right text-muted-foreground">
                      Share
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pieData.map((d, i) => (
                    <TableRow key={d.rawKey}>
                      <TableCell className="w-8 px-1.5 py-1.5">
                        <span
                          className="mx-auto block h-2.5 w-2.5 rounded-full"
                          style={{
                            backgroundColor:
                              CHART_SERIES_COLORS[
                                i % CHART_SERIES_COLORS.length
                              ],
                          }}
                          aria-hidden
                        />
                      </TableCell>
                      <TableCell className="min-w-0 py-1.5 pl-0 pr-1.5">
                        <div className="truncate font-medium" title={d.name}>
                          {d.name}
                        </div>
                      </TableCell>
                      <TableCell className="py-1.5 px-1.5 text-right tabular-nums text-muted-foreground">
                        {formatCurrency(d.value)}
                      </TableCell>
                      <TableCell className="py-1.5 px-1.5 text-right tabular-nums text-muted-foreground">
                        {formatPercent(d.pct, 1)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
