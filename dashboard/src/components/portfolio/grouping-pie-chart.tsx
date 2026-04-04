/**
 * grouping-pie-chart.tsx — donut that splits a sleeve (e.g. equities) by a
 * grouping dimension. Values are rupees; hover shows ₹ + %; legend is a small
 * table (`legendLayout` = beside vs below).
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
import { CHART_SERIES_COLORS } from "@/lib/chart-colors";
import { cn, formatPercent } from "@/lib/utils";

export interface GroupingPieSlice {
  name: string;
  value: number;
}

export interface GroupingPieChartProps {
  title: string;
  description?: string;
  data: GroupingPieSlice[];
  isLoading?: boolean;
  emptyMessage?: string;
  /** Passed to the outer Card (e.g. `h-full` when sitting in a grid). */
  className?: string;
  /**
   * `beside` — donut left, legend right from `sm` up (default, e.g. equities).
   * `below` — donut centered, legend full width underneath (e.g. mutual fund mix in a narrow column).
   */
  legendLayout?: "beside" | "below";
}

export function GroupingPieChart({
  title,
  description,
  data,
  isLoading,
  emptyMessage = "Nothing to chart for this grouping.",
  className,
  legendLayout = "beside",
}: GroupingPieChartProps) {
  const legendBelow = legendLayout === "below";
  const total = React.useMemo(
    () => data.reduce((s, d) => s + d.value, 0),
    [data],
  );

  const pieData = React.useMemo(() => {
    if (total <= 0) return [];
    return data
      .filter((d) => d.value > 0)
      .map((d) => ({
        ...d,
        pct: (100 * d.value) / total,
      }))
      .sort((a, b) => b.value - a.value);
  }, [data, total]);

  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {description ? (
          <p className="text-xs text-muted-foreground">{description}</p>
        ) : null}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div
            className={cn(
              "flex flex-col items-center gap-4",
              !legendBelow && "sm:flex-row sm:items-start",
            )}
          >
            <Skeleton className="size-[148px] shrink-0 rounded-full" />
            <div className="w-full min-w-0 flex-1 space-y-2 sm:pt-1">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-4/5" />
            </div>
          </div>
        ) : pieData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            {emptyMessage}
          </p>
        ) : (
          <div
            className={cn(
              "flex w-full flex-col items-center gap-4",
              !legendBelow && "sm:flex-row sm:items-start sm:gap-5",
            )}
          >
            <div className="h-[148px] w-[148px] shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={pieData}
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    innerRadius={42}
                    outerRadius={64}
                    paddingAngle={1}
                  >
                    {pieData.map((d, i) => (
                      <Cell
                        key={`${d.name}-${i}`}
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
            <div
              className={cn(
                "min-w-0 w-full flex-1 overflow-auto",
                legendBelow
                  ? "max-h-[min(40vh,280px)]"
                  : "sm:max-h-[200px]",
              )}
            >
              <Table
                className={cn("text-xs", !legendBelow && "table-fixed")}
                aria-label={`${title} breakdown`}
              >
                <colgroup>
                  <col className="w-8" />
                  <col />
                  <col className="w-14" />
                </colgroup>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8 w-8 px-1.5">
                      <span className="sr-only">Color</span>
                    </TableHead>
                    <TableHead className="h-8 px-1.5 text-muted-foreground">
                      Category
                    </TableHead>
                    <TableHead className="h-8 px-1.5 text-right text-muted-foreground">
                      Share
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pieData.map((d, i) => (
                    <TableRow key={`${d.name}-${i}`}>
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
