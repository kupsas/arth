/**
 * holdings-table.tsx — sortable holdings grid + expand for returns / manual marks (F2.5.2).
 *
 * - Client-side sort on numeric / text columns.
 * - Dropdown filters for asset class and broker/platform (unique values from rows).
 * - Gain/loss uses quantity × average cost vs current_value when all three exist.
 * - Weight = current_value / total assets from the summary endpoint.
 * - Return column prefetches GET /api/holdings/{id} for each row (small N in this household).
 * - Refresh prices → POST /api/prices/refresh scoped to this user.
 */

"use client";

import * as React from "react";
import { useQueries } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getExpandedRowModel,
  getSortedRowModel,
  useReactTable,
  type ExpandedState,
  type SortingState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
  RefreshCw,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { portfolioKeys, useHoldings, useHoldingsSummary, useRefreshPrices, useUpdateHoldingValue } from "@/hooks/use-portfolio";
import { fetchHoldingDetail } from "@/lib/api";
import { sanitizeHtmlDateInputValue } from "@/lib/onboarding-input-validation";
import type { Holding, HoldingDetail, HoldingValueUpdate } from "@/lib/types";
import { cn, formatCurrency, formatInrMoneyInput, formatPercent, parseInrMoneyInput, reformatInrMoneyTyping } from "@/lib/utils";

export interface HoldingsTableProps {
  userId: string;
}

type HoldingRow = Holding & { id: number };

function gainLoss(h: HoldingRow) {
  const q = h.quantity;
  const avg = h.average_cost_per_unit;
  const cur = h.current_value;
  if (q == null || avg == null || cur == null) return null;
  const cost = q * avg;
  const abs = cur - cost;
  const pct = cost !== 0 ? (abs / cost) * 100 : null;
  return { abs, pct };
}

function prettyAssetClass(s: string) {
  return s
    .split(/_/g)
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

/** Module-scoped so column defs can sit in useMemo without exhaustive-deps noise. */
const holdingCol = createColumnHelper<HoldingRow>();

function SortHeader({
  label,
  columnId,
  sorting,
  onSort,
}: {
  label: string;
  columnId: string;
  sorting: SortingState;
  onSort: (id: string) => void;
}) {
  const cur = sorting.find((s) => s.id === columnId);
  const Icon = !cur ? ArrowUpDown : cur.desc ? ArrowDown : ArrowUp;
  return (
    <button
      type="button"
      className="flex items-center gap-1 font-medium hover:text-foreground transition-colors"
      onClick={() => onSort(columnId)}
    >
      {label}
      <Icon
        className={cn(
          "size-3.5",
          cur ? "text-foreground" : "text-muted-foreground/50",
        )}
      />
    </button>
  );
}

export function HoldingsTable({ userId }: HoldingsTableProps) {
  const { data: holdings, isLoading: hLoad } = useHoldings({ user_id: userId });
  const { data: summary } = useHoldingsSummary({ user_id: userId });
  const { mutate: refreshPrices, isPending: isRefreshing } = useRefreshPrices();
  const updateValue = useUpdateHoldingValue();

  const [assetClass, setAssetClass] = React.useState("");
  const [platform, setPlatform] = React.useState("");
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: "name", desc: false },
  ]);
  const [expanded, setExpanded] = React.useState<ExpandedState>({});

  const withIds = React.useMemo(
    () => (holdings ?? []).filter((h): h is HoldingRow => h.id != null),
    [holdings],
  );

  const assetOptions = React.useMemo(() => {
    const s = new Set<string>();
    for (const h of withIds) s.add(h.asset_class);
    return [...s].sort();
  }, [withIds]);

  const platformOptions = React.useMemo(() => {
    const s = new Set<string>();
    for (const h of withIds) s.add(h.account_platform);
    return [...s].sort();
  }, [withIds]);

  const filtered = React.useMemo(() => {
    let list = withIds;
    if (assetClass) list = list.filter((h) => h.asset_class === assetClass);
    if (platform) list = list.filter((h) => h.account_platform === platform);
    return list;
  }, [withIds, assetClass, platform]);

  const totalAssets = summary?.net_worth.total_assets ?? 0;

  const detailQueries = useQueries({
    queries: withIds.map((h) => ({
      queryKey: portfolioKeys.holdingDetail(h.id, userId),
      queryFn: () => fetchHoldingDetail(h.id, { user_id: userId }),
      enabled: Boolean(userId),
      staleTime: 60_000,
    })),
  });

  const detailById = React.useMemo(() => {
    const m = new Map<number, HoldingDetail>();
    withIds.forEach((h, i) => {
      const d = detailQueries[i]?.data;
      if (d) m.set(h.id, d);
    });
    return m;
  }, [withIds, detailQueries]);

  const detailLoadingById = React.useMemo(() => {
    const m = new Map<number, boolean>();
    withIds.forEach((h, i) => {
      m.set(h.id, Boolean(detailQueries[i]?.isLoading));
    });
    return m;
  }, [withIds, detailQueries]);

  const toggleSort = React.useCallback((columnId: string) => {
    setSorting((prev) => {
      const cur = prev.find((s) => s.id === columnId);
      if (!cur) return [{ id: columnId, desc: false }];
      if (!cur.desc) return [{ id: columnId, desc: true }];
      return [{ id: columnId, desc: false }];
    });
  }, []);

  const columns = React.useMemo(
    () => [
      holdingCol.display({
        id: "expander",
        header: () => <span className="sr-only">Expand</span>,
        cell: ({ row }) => (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-8"
            aria-expanded={row.getIsExpanded()}
            onClick={(e) => {
              e.stopPropagation();
              row.toggleExpanded();
            }}
          >
            {row.getIsExpanded() ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </Button>
        ),
        enableSorting: false,
        size: 40,
      }),
      holdingCol.accessor("name", {
        id: "name",
        header: () => (
          <SortHeader
            label="Name"
            columnId="name"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const h = info.row.original;
          return (
            <div>
              <div className="font-medium">{h.name}</div>
              {h.symbol ? (
                <div className="text-xs text-muted-foreground font-mono">{h.symbol}</div>
              ) : null}
            </div>
          );
        },
        sortingFn: "alphanumeric",
      }),
      holdingCol.accessor("asset_class", {
        header: () => (
          <SortHeader
            label="Class"
            columnId="asset_class"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => (
          <Badge variant="secondary" className="font-normal">
            {prettyAssetClass(String(info.getValue()))}
          </Badge>
        ),
      }),
      holdingCol.accessor("quantity", {
        header: () => (
          <SortHeader
            label="Qty"
            columnId="quantity"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const v = info.getValue();
          return v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 4 });
        },
      }),
      holdingCol.accessor("average_cost_per_unit", {
        id: "avg_cost",
        header: () => (
          <SortHeader
            label="Avg cost"
            columnId="avg_cost"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const v = info.getValue();
          return v == null ? "—" : formatCurrency(v, 2);
        },
      }),
      holdingCol.accessor("current_price_per_unit", {
        id: "price",
        header: () => (
          <SortHeader
            label="Price"
            columnId="price"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const v = info.getValue();
          return v == null ? "—" : formatCurrency(v, 2);
        },
      }),
      holdingCol.accessor("current_value", {
        id: "value",
        header: () => (
          <SortHeader
            label="Value"
            columnId="value"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const v = info.getValue();
          return v == null ? "—" : formatCurrency(v);
        },
      }),
      holdingCol.display({
        id: "pnl",
        header: () => (
          <SortHeader
            label="Gain / loss"
            columnId="pnl"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: ({ row }) => {
          const gl = gainLoss(row.original);
          if (!gl) return "—";
          const { abs, pct } = gl;
          const pos = abs >= 0;
          return (
            <div className={cn("tabular-nums", pos ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400")}>
              <div>{formatCurrency(abs)}</div>
              {pct != null && (
                <div className="text-xs opacity-90">{formatPercent(pct)}</div>
              )}
            </div>
          );
        },
        sortingFn: (a, b) => {
          const ga = gainLoss(a.original)?.abs ?? 0;
          const gb = gainLoss(b.original)?.abs ?? 0;
          return ga - gb;
        },
      }),
      holdingCol.display({
        id: "weight",
        header: () => (
          <SortHeader
            label="Weight"
            columnId="weight"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: ({ row }) => {
          const cv = row.original.current_value ?? 0;
          if (totalAssets <= 0) return "—";
          const w = (cv / totalAssets) * 100;
          return formatPercent(w);
        },
        sortingFn: (a, b) => {
          const av = a.original.current_value ?? 0;
          const bv = b.original.current_value ?? 0;
          return av - bv;
        },
      }),
      holdingCol.display({
        id: "ret",
        header: () => (
          <SortHeader
            label="Return"
            columnId="ret"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: ({ row }) => {
          const d = detailById.get(row.original.id);
          const loading = detailLoadingById.get(row.original.id);
          if (loading && !d) return <Skeleton className="h-4 w-14" />;
          if (!d) return "—";
          const ar = d.returns.annualized_return;
          if (typeof ar === "number" && Number.isFinite(ar)) {
            // annualized_return is a decimal fraction (0.075 = 7.5%) — multiply by 100 for display.
            return (
              <span className={`tabular-nums text-sm ${ar >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
                {formatPercent(ar * 100)}
                <span className="block text-[10px] text-muted-foreground">
                  {d.returns.method}
                </span>
              </span>
            );
          }
          return <span className="text-xs text-muted-foreground">{d.returns.method}</span>;
        },
        sortingFn: (a, b) => {
          const ra = detailById.get(a.original.id)?.returns.annualized_return;
          const rb = detailById.get(b.original.id)?.returns.annualized_return;
          const na = typeof ra === "number" && Number.isFinite(ra) ? ra : -Infinity;
          const nb = typeof rb === "number" && Number.isFinite(rb) ? rb : -Infinity;
          return na - nb;
        },
      }),
    ],
    [sorting, totalAssets, detailById, detailLoadingById, toggleSort],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting, expanded },
    onSortingChange: setSorting,
    onExpandedChange: setExpanded,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getRowCanExpand: () => true,
  });

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="text-sm font-medium">Holdings</CardTitle>
          <p className="text-xs text-muted-foreground">
            Expand a row for return detail and manual value edits (MANUAL marks only).
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">Asset class</Label>
            <Select value={assetClass} onValueChange={(v) => setAssetClass(v ?? "")}>
              <SelectTrigger size="sm" className="w-[140px] h-8">
                <SelectValue placeholder="All classes" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">All classes</SelectItem>
                {assetOptions.map((a) => (
                  <SelectItem key={a} value={a}>
                    {prettyAssetClass(a)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">Platform</Label>
            <Select value={platform} onValueChange={(v) => setPlatform(v ?? "")}>
              <SelectTrigger size="sm" className="w-[160px] h-8">
                <SelectValue placeholder="All platforms" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">All platforms</SelectItem>
                {platformOptions.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 gap-1.5"
            disabled={isRefreshing}
            onClick={() => refreshPrices({ user_id: userId })}
          >
            <RefreshCw className={cn("size-3.5", isRefreshing && "animate-spin")} />
            {isRefreshing ? "Refreshing…" : "Refresh prices"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {hLoad ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No holdings match these filters.
          </p>
        ) : (
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((header) => (
                    <TableHead key={header.id} className="whitespace-nowrap">
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <React.Fragment key={row.id}>
                  <TableRow>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                  {row.getIsExpanded() && (
                    <TableRow className="bg-muted/30 hover:bg-muted/30">
                      <TableCell colSpan={columns.length} className="p-4">
                        <HoldingExpandedPanel
                          holding={row.original}
                          detail={detailById.get(row.original.id)}
                          detailLoading={Boolean(detailLoadingById.get(row.original.id))}
                          onSaveManual={(body) =>
                            updateValue.mutateAsync({
                              id: row.original.id,
                              body,
                              user_id: userId,
                            })
                          }
                          isSaving={updateValue.isPending}
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </React.Fragment>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function HoldingExpandedPanel({
  holding,
  detail,
  detailLoading,
  onSaveManual,
  isSaving,
}: {
  holding: HoldingRow;
  detail: HoldingDetail | undefined;
  detailLoading: boolean;
  onSaveManual: (body: HoldingValueUpdate) => Promise<unknown>;
  isSaving: boolean;
}) {
  const [valueStr, setValueStr] = React.useState(
    holding.current_value != null ? String(holding.current_value) : "",
  );
  const [dateStr, setDateStr] = React.useState(
    holding.last_valued_date ?? "",
  );

  React.useEffect(() => {
    setValueStr(holding.current_value != null ? formatInrMoneyInput(holding.current_value) : "");
    setDateStr(holding.last_valued_date ?? "");
  }, [holding.current_value, holding.last_valued_date]);

  const manual = holding.valuation_method === "MANUAL";

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="space-y-2 text-sm">
        <p className="text-xs font-medium text-muted-foreground">Returns detail</p>
        {detailLoading && !detail ? (
          <Skeleton className="h-24 w-full" />
        ) : detail ? (
          <pre className="max-h-48 overflow-auto rounded-md border bg-background/80 p-3 text-xs font-mono">
            {JSON.stringify(detail.returns, null, 2)}
          </pre>
        ) : (
          <p className="text-xs text-muted-foreground">No detail loaded.</p>
        )}
      </div>

      <div className="space-y-3 text-sm">
        <p className="text-xs font-medium text-muted-foreground">Manual mark (MANUAL only)</p>
        {!manual ? (
          <p className="text-xs text-muted-foreground">
            This row uses <strong>{holding.valuation_method}</strong> pricing — the API ignores
            manual patches. Use your broker or statement import to change marks.
          </p>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const current_value =
                valueStr.trim() === "" ? null : parseInrMoneyInput(valueStr);
              if (valueStr.trim() !== "" && (current_value === null || !Number.isFinite(current_value)))
                return;
              await onSaveManual({
                current_value,
                last_valued_date: dateStr || null,
              });
            }}
          >
            <div className="space-y-1">
              <Label htmlFor={`cv-${holding.id}`}>Current value (INR)</Label>
              <Input
                id={`cv-${holding.id}`}
                type="text"
                inputMode="decimal"
                autoComplete="off"
                className="tabular-nums"
                value={valueStr}
                onChange={(e) => setValueStr(reformatInrMoneyTyping(e.target.value))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor={`lv-${holding.id}`}>As of date</Label>
              <Input
                id={`lv-${holding.id}`}
                type="date"
                min="1900-01-01"
                max="9999-12-31"
                value={dateStr.slice(0, 10)}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") {
                    setDateStr("");
                    return;
                  }
                  const v = sanitizeHtmlDateInputValue(raw);
                  if (v != null) setDateStr(v);
                }}
              />
            </div>
            <Button type="submit" size="sm" disabled={isSaving} className="w-fit">
              {isSaving ? "Saving…" : "Save manual value"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
