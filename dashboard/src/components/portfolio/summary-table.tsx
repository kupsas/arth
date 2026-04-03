/**
 * summary-table.tsx — ICICI-style breakdown: one row per asset class (B3).
 * Rows scroll to the matching section below. No daily gain columns.
 */

"use client";

import * as React from "react";

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
import {
  prettyAssetClassLabel,
  scrollToHoldingsSection,
} from "@/lib/holdings-display";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

export interface SummaryTableProps {
  userId: string;
}

function gainClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "text-muted-foreground";
}

export function SummaryTable({ userId }: SummaryTableProps) {
  const { data, isLoading } = usePortfolioSummary({ user_id: userId });
  const breakdown = data?.asset_class_breakdown;

  const rows = React.useMemo(() => {
    if (!breakdown) return [];
    return Object.entries(breakdown)
      .filter(([, v]) => v.current_value > 0)
      .sort((a, b) => b[1].current_value - a[1].current_value);
  }, [breakdown]);

  return (
    <Card className="h-full">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Summary by product</CardTitle>
        <p className="text-xs text-muted-foreground">
          Click a row to jump to that section
        </p>
      </CardHeader>
      <CardContent className="px-0 sm:px-4">
        {isLoading ? (
          <Skeleton className="mx-4 h-48 w-[calc(100%-2rem)]" />
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground px-4 py-6 text-center">
            No rows yet — holdings may be inactive or values are zero.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead className="text-right">Investments</TableHead>
                <TableHead className="text-right">Current value</TableHead>
                <TableHead className="text-right">Overall gain</TableHead>
                <TableHead className="text-right">Overall gain %</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map(([assetClass, row]) => (
                <TableRow
                  key={assetClass}
                  className="cursor-pointer hover:bg-muted/60"
                  onClick={() => scrollToHoldingsSection(assetClass)}
                >
                  <TableCell className="font-medium">
                    {prettyAssetClassLabel(assetClass)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(row.investment)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(row.current_value)}
                  </TableCell>
                  <TableCell
                    className={cn("text-right tabular-nums", gainClass(row.overall_gain))}
                  >
                    {row.overall_gain != null
                      ? `${row.overall_gain > 0 ? "+" : ""}${formatCurrency(row.overall_gain)}`
                      : "—"}
                  </TableCell>
                  <TableCell
                    className={cn("text-right tabular-nums", gainClass(row.overall_gain_pct))}
                  >
                    {row.overall_gain_pct != null
                      ? `${row.overall_gain_pct > 0 ? "+" : ""}${formatPercent(row.overall_gain_pct, 1)}`
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
