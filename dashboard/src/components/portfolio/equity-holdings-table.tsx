/**
 * equity-holdings-table.tsx — grouped equity grid with subtotals + expand row
 * (weight %, market cap). No daily change columns (rebuild spec).
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
  holdingCostBasis,
  prettyMarketCapClass,
  weightPercentWithinSleeve,
} from "@/lib/holdings-display";
import type { Holding } from "@/lib/types";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

export type EquityGroupMode = "sector" | "market_cap" | "holding_period";

type HoldingRow = Holding & { id: number };

export interface EquityHoldingsTableProps {
  holdings: HoldingRow[];
  groupMode: EquityGroupMode;
}

function groupLabel(h: HoldingRow, mode: EquityGroupMode): string {
  if (mode === "sector") return h.sector?.trim() || "Unclassified";
  if (mode === "market_cap") return prettyMarketCapClass(h.market_cap_class);
  return "All scripts";
}

/** CMP value split from investment_transactions (API); zeros if missing. */
function equityPeriodPart(h: HoldingRow) {
  const p = h.equity_holding_period;
  return {
    lt: p?.long_term_value_inr ?? 0,
    st: p?.short_term_value_inr ?? 0,
    u: p?.unallocated_value_inr ?? 0,
    note: p?.basis_note ?? null,
  };
}

function gainClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "text-muted-foreground";
}

interface GroupBlock {
  key: string;
  rows: HoldingRow[];
  sumCost: number;
  sumValue: number;
  sumGain: number | null;
}

/** Exposed for the equities section donut (same buckets as the table). */
export function buildEquityGroups(
  rows: HoldingRow[],
  mode: EquityGroupMode,
): GroupBlock[] {
  const map = new Map<string, HoldingRow[]>();
  for (const h of rows) {
    const k = groupLabel(h, mode);
    const list = map.get(k) ?? [];
    list.push(h);
    map.set(k, list);
  }
  const blocks: GroupBlock[] = [];
  for (const [key, list] of map.entries()) {
    let sumCost = 0;
    let sumValue = 0;
    const gainParts: number[] = [];
    for (const h of list) {
      const c = holdingCostBasis(h);
      const v = h.current_value ?? 0;
      if (c != null) sumCost += c;
      sumValue += v;
      if (h.overall_gain != null) gainParts.push(h.overall_gain);
    }
    const sumGain =
      gainParts.length === list.length
        ? gainParts.reduce((a, b) => a + b, 0)
        : null;
    blocks.push({ key, rows: list, sumCost, sumValue, sumGain });
  }
  blocks.sort((a, b) => b.sumValue - a.sumValue);
  for (const b of blocks) {
    b.rows.sort((x, y) => (y.current_value ?? 0) - (x.current_value ?? 0));
  }
  return blocks;
}

export function EquityHoldingsTable({
  holdings,
  groupMode,
}: EquityHoldingsTableProps) {
  const [expanded, setExpanded] = React.useState<Set<number>>(() => new Set());
  const hpMode = groupMode === "holding_period";

  const groups = React.useMemo(
    () => buildEquityGroups(holdings, groupMode),
    [holdings, groupMode],
  );

  const grand = React.useMemo(() => {
    let sumCost = 0;
    let sumValue = 0;
    let sumLt = 0;
    let sumSt = 0;
    let sumUn = 0;
    const gainParts: number[] = [];
    for (const h of holdings) {
      const c = holdingCostBasis(h);
      const v = h.current_value ?? 0;
      if (c != null) sumCost += c;
      sumValue += v;
      if (h.overall_gain != null) gainParts.push(h.overall_gain);
      if (hpMode) {
        const q = equityPeriodPart(h);
        sumLt += q.lt;
        sumSt += q.st;
        sumUn += q.u;
      }
    }
    return {
      sumCost,
      sumValue,
      sumLt,
      sumSt,
      sumUn,
      sumGain:
        gainParts.length === holdings.length
          ? gainParts.reduce((a, b) => a + b, 0)
          : null,
    };
  }, [holdings, hpMode]);

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const grandGainPct =
    grand.sumGain != null && grand.sumCost > 0
      ? (100 * grand.sumGain) / grand.sumCost
      : null;

  const detailColSpan = hpMode ? 11 : 8;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">Equity holdings</CardTitle>
        {hpMode ? (
          <p className="text-xs text-muted-foreground">
            Each row is one script. Long-term / short-term columns split that row&apos;s
            CMP value using buy dates from linked investment transactions (FIFO).
            Unallocated means missing ledger rows or quantity mismatch vs the holding.
          </p>
        ) : null}
      </CardHeader>
      <CardContent className="px-0 sm:px-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>Symbol</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Avg cost</TableHead>
              <TableHead className="text-right">CMP</TableHead>
              <TableHead className="text-right">Value at cost</TableHead>
              <TableHead className="text-right">Value at CMP</TableHead>
              {hpMode ? (
                <>
                  <TableHead className="text-right">CMP — LT</TableHead>
                  <TableHead className="text-right">CMP — ST</TableHead>
                  <TableHead className="text-right">Unallocated</TableHead>
                </>
              ) : null}
              <TableHead className="text-right">Unrealized P&amp;L</TableHead>
              <TableHead className="text-right">P/L %</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.map((g) => (
              <React.Fragment key={g.key}>
                <TableRow className="bg-muted/50 hover:bg-muted/50">
                  <TableCell />
                  <TableCell colSpan={4} className="font-semibold">
                    {g.key}
                  </TableCell>
                  <TableCell className="text-right tabular-nums font-medium">
                    {formatCurrency(g.sumCost)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums font-medium">
                    {formatCurrency(g.sumValue)}
                  </TableCell>
                  {hpMode ? (
                    <>
                      <TableCell className="text-right tabular-nums font-medium">
                        {formatCurrency(
                          g.rows.reduce((s, x) => s + equityPeriodPart(x).lt, 0),
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium">
                        {formatCurrency(
                          g.rows.reduce((s, x) => s + equityPeriodPart(x).st, 0),
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums font-medium">
                        {formatCurrency(
                          g.rows.reduce((s, x) => s + equityPeriodPart(x).u, 0),
                        )}
                      </TableCell>
                    </>
                  ) : null}
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
                        g.sumGain != null && g.sumCost > 0
                          ? (100 * g.sumGain) / g.sumCost
                          : null,
                      ),
                    )}
                  >
                    {g.sumGain != null && g.sumCost > 0
                      ? `${g.sumGain > 0 ? "+" : ""}${formatPercent((100 * g.sumGain) / g.sumCost, 1)}`
                      : "—"}
                  </TableCell>
                </TableRow>
                {g.rows.map((h) => {
                  const cost = holdingCostBasis(h);
                  const cv = h.current_value;
                  const cmp = h.current_price_per_unit;
                  const open = expanded.has(h.id);
                  const ep = equityPeriodPart(h);
                  const wtSleeve = weightPercentWithinSleeve(
                    h.current_value,
                    grand.sumValue,
                  );
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
                        <TableCell className="font-medium">
                          {h.symbol ?? "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {h.quantity != null
                            ? h.quantity.toLocaleString("en-IN", {
                                maximumFractionDigits: 4,
                              })
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {h.average_cost_per_unit != null
                            ? formatCurrency(h.average_cost_per_unit, 2)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cmp != null ? formatCurrency(cmp, 2) : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cost != null ? formatCurrency(cost) : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {cv != null ? formatCurrency(cv) : "—"}
                        </TableCell>
                        {hpMode ? (
                          <>
                            <TableCell className="text-right tabular-nums">
                              {ep.lt > 0 ? formatCurrency(ep.lt) : "—"}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {ep.st > 0 ? formatCurrency(ep.st) : "—"}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {ep.u > 0 ? formatCurrency(ep.u) : "—"}
                            </TableCell>
                          </>
                        ) : null}
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
                            colSpan={detailColSpan}
                            className="text-xs text-muted-foreground py-2"
                          >
                            <div className="space-y-1">
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
                                of total equity portfolio
                              </p>
                              <p>
                                <span className="mr-4">
                                  Market cap:{" "}
                                  {prettyMarketCapClass(h.market_cap_class)}
                                </span>
                              </p>
                              {hpMode && ep.note ? (
                                <p className="text-[11px]">
                                  Holding-period basis: {ep.note}
                                </p>
                              ) : null}
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
              <TableCell colSpan={4}>Total</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCurrency(grand.sumCost)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCurrency(grand.sumValue)}
              </TableCell>
              {hpMode ? (
                <>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(grand.sumLt)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(grand.sumSt)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(grand.sumUn)}
                  </TableCell>
                </>
              ) : null}
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
