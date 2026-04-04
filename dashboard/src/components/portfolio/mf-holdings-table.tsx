/**
 * mf-holdings-table.tsx — grouped mutual fund grid with XIRR from batch-returns.
 */

"use client";

import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  annualizedReturnPercentPoints,
  formatAnnualizedReturnForDisplay,
  holdingCostBasis,
  weightPercentWithinSleeve,
} from "@/lib/holdings-display";
import type { Holding } from "@/lib/types";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

type HoldingRow = Holding & { id: number };

export interface MfHoldingsTableProps {
  holdings: HoldingRow[];
  /** Map holding id string → compute_returns payload from GET /batch-returns. */
  returnsByHoldingId: Record<string, Record<string, unknown>>;
}

function groupLabel(h: HoldingRow): string {
  return h.fund_category?.trim() || "Unclassified";
}

function gainClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "text-muted-foreground";
}

interface MfGroupBlock {
  key: string;
  rows: HoldingRow[];
  sumInvested: number;
  sumValue: number;
  sumGain: number | null;
}

export function buildMfGroups(rows: HoldingRow[]): MfGroupBlock[] {
  const map = new Map<string, HoldingRow[]>();
  for (const h of rows) {
    const k = groupLabel(h);
    const list = map.get(k) ?? [];
    list.push(h);
    map.set(k, list);
  }
  const blocks: MfGroupBlock[] = [];
  for (const [key, list] of map.entries()) {
    let sumInvested = 0;
    let sumValue = 0;
    const gains: number[] = [];
    for (const h of list) {
      const inv = holdingCostBasis(h) ?? 0;
      sumInvested += inv;
      sumValue += h.current_value ?? 0;
      if (h.overall_gain != null) gains.push(h.overall_gain);
    }
    const sumGain =
      gains.length === list.length ? gains.reduce((a, b) => a + b, 0) : null;
    blocks.push({ key, rows: list, sumInvested, sumValue, sumGain });
  }
  blocks.sort((a, b) => b.sumValue - a.sumValue);
  for (const b of blocks) {
    b.rows.sort((x, y) => (y.current_value ?? 0) - (x.current_value ?? 0));
  }
  return blocks;
}

export function MfHoldingsTable({
  holdings,
  returnsByHoldingId,
}: MfHoldingsTableProps) {
  const [expanded, setExpanded] = React.useState<Set<number>>(() => new Set());

  const groups = React.useMemo(
    () => buildMfGroups(holdings),
    [holdings],
  );

  const grand = React.useMemo(() => {
    let sumInvested = 0;
    let sumValue = 0;
    const gains: number[] = [];
    for (const h of holdings) {
      sumInvested += holdingCostBasis(h) ?? 0;
      sumValue += h.current_value ?? 0;
      if (h.overall_gain != null) gains.push(h.overall_gain);
    }
    return {
      sumInvested,
      sumValue,
      sumGain:
        gains.length === holdings.length
          ? gains.reduce((a, b) => a + b, 0)
          : null,
    };
  }, [holdings]);

  const grandGainPct =
    grand.sumGain != null && grand.sumInvested > 0
      ? (100 * grand.sumGain) / grand.sumInvested
      : null;

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Mutual fund holdings</CardTitle>
        <p className="text-xs text-muted-foreground">
          XIRR comes from ledger cash flows (batch-returns). Enrich funds for
          category / AMC labels.
        </p>
      </CardHeader>
      <CardContent className="px-0 sm:px-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Fund</TableHead>
              <TableHead className="text-right">Invested</TableHead>
              <TableHead className="text-right">Current</TableHead>
              <TableHead className="text-right">XIRR</TableHead>
              <TableHead className="text-right">Overall gain</TableHead>
              <TableHead className="text-right">Gain %</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.map((g) => (
              <React.Fragment key={g.key}>
                <TableRow className="bg-muted/50 hover:bg-muted/50">
                  <TableCell />
                  <TableCell className="font-semibold">{g.key}</TableCell>
                  <TableCell className="text-right tabular-nums font-medium">
                    {formatCurrency(g.sumInvested)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums font-medium">
                    {formatCurrency(g.sumValue)}
                  </TableCell>
                  <TableCell
                    className={cn("text-right font-medium", gainClass(null))}
                  >
                    —
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums font-medium",
                      gainClass(g.sumGain),
                    )}
                  >
                    {g.sumGain != null
                      ? `${g.sumGain > 0 ? "+" : ""}${formatCurrency(g.sumGain)}`
                      : "—"}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums font-medium",
                      gainClass(
                        g.sumGain != null && g.sumInvested > 0
                          ? (100 * g.sumGain) / g.sumInvested
                          : null,
                      ),
                    )}
                  >
                    {g.sumGain != null && g.sumInvested > 0
                      ? `${g.sumGain > 0 ? "+" : ""}${formatPercent((100 * g.sumGain) / g.sumInvested, 1)}`
                      : "—"}
                  </TableCell>
                </TableRow>
                {g.rows.map((h) => {
                  const invested = holdingCostBasis(h);
                  const cv = h.current_value;
                  const ret = returnsByHoldingId[String(h.id)] ?? {};
                  const xirrPct = annualizedReturnPercentPoints(
                    ret.annualized_return,
                  );
                  const xirr = formatAnnualizedReturnForDisplay(
                    ret.annualized_return,
                  );
                  const wtSleeve = weightPercentWithinSleeve(
                    h.current_value,
                    grand.sumValue,
                  );
                  const open = expanded.has(h.id);
                  return (
                    <React.Fragment key={h.id}>
                      <TableRow>
                        <TableCell className="p-1">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-xs"
                            className="size-7"
                            aria-expanded={open}
                            onClick={() => toggle(h.id)}
                          >
                            {open ? (
                              <ChevronDown className="size-4" />
                            ) : (
                              <ChevronRight className="size-4" />
                            )}
                          </Button>
                        </TableCell>
                        <TableCell className="font-medium max-w-[200px] truncate">
                          {h.name}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {invested != null ? formatCurrency(invested) : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cv != null ? formatCurrency(cv) : "—"}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right tabular-nums",
                            gainClass(xirrPct),
                          )}
                        >
                          {xirr != null
                            ? `${xirrPct != null && xirrPct > 0 ? "+" : ""}${xirr}`
                            : "—"}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right tabular-nums",
                            gainClass(h.overall_gain),
                          )}
                        >
                          {h.overall_gain != null
                            ? `${h.overall_gain > 0 ? "+" : ""}${formatCurrency(h.overall_gain)}`
                            : "—"}
                        </TableCell>
                        <TableCell
                          className={cn(
                            "text-right tabular-nums",
                            gainClass(h.overall_gain_pct),
                          )}
                        >
                          {h.overall_gain_pct != null
                            ? `${h.overall_gain_pct > 0 ? "+" : ""}${formatPercent(h.overall_gain_pct, 1)}`
                            : "—"}
                        </TableCell>
                      </TableRow>
                      {open ? (
                        <TableRow className="bg-muted/30">
                          <TableCell />
                          <TableCell
                            colSpan={6}
                            className="text-xs text-muted-foreground py-2"
                          >
                            <div className="space-y-1">
                              <p>
                                AMC: {h.fund_house?.trim() || "—"}
                              </p>
                              <p>
                                Weight:{" "}
                                {h.weight_pct != null
                                  ? formatPercent(h.weight_pct, 1)
                                  : "—"}{" "}
                                of total portfolio
                              </p>
                              <p>
                                Weight:{" "}
                                {wtSleeve != null
                                  ? formatPercent(wtSleeve, 1)
                                  : "—"}{" "}
                                of total mutual fund portfolio
                              </p>
                            </div>
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </React.Fragment>
            ))}
            <TableRow className="border-t-2 font-semibold">
              <TableCell />
              <TableCell>Total</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCurrency(grand.sumInvested)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCurrency(grand.sumValue)}
              </TableCell>
              <TableCell
                className={cn("text-right tabular-nums", gainClass(null))}
              >
                —
              </TableCell>
              <TableCell
                className={cn("text-right tabular-nums", gainClass(grand.sumGain))}
              >
                {grand.sumGain != null
                  ? `${grand.sumGain > 0 ? "+" : ""}${formatCurrency(grand.sumGain)}`
                  : "—"}
              </TableCell>
              <TableCell
                className={cn(
                  "text-right tabular-nums",
                  gainClass(grandGainPct),
                )}
              >
                {grandGainPct != null
                  ? `${grandGainPct > 0 ? "+" : ""}${formatPercent(grandGainPct, 1)}`
                  : "—"}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
