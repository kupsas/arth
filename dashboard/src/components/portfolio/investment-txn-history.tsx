/**
 * investment-txn-history.tsx — investment ledger with filters + pagination (F2.5.6).
 *
 * Always passes ``user_id`` (your login username) so the API joins through holdings
 * and never leaks another household member's trades.
 *
 * Bank link: when ``bank_transaction_id`` is set, we deep-link to Transactions with
 * ``?txn_id=`` so the edit sheet can open on that bank row.
 */

"use client";

import * as React from "react";
import Link from "next/link";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, ExternalLink } from "lucide-react";

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
import { useInvestmentTransactions } from "@/hooks/use-portfolio";
import type { InvestmentLedgerTxnType, InvestmentTxn } from "@/lib/types";
import { cn, formatCurrency, formatDate } from "@/lib/utils";

const PAGE_SIZE = 30;

const TXN_TYPES: InvestmentLedgerTxnType[] = [
  "BUY",
  "SELL",
  "DIVIDEND",
  "SIP",
  "SWITCH_IN",
  "SWITCH_OUT",
];

export interface InvestmentTxnHistoryProps {
  userId: string;
}

function txnBadgeVariant(t: string): "default" | "secondary" | "outline" | "destructive" {
  if (t === "SELL" || t === "SWITCH_OUT") return "destructive";
  if (t === "DIVIDEND") return "secondary";
  return "default";
}

const invTxnCol = createColumnHelper<InvestmentTxn>();

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

export function InvestmentTxnHistory({ userId }: InvestmentTxnHistoryProps) {
  const [txnType, setTxnType] = React.useState("");
  const [symbol, setSymbol] = React.useState("");
  const [debouncedSymbol, setDebouncedSymbol] = React.useState("");
  const [dateFrom, setDateFrom] = React.useState("");
  const [dateTo, setDateTo] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: "txn_date", desc: true },
  ]);

  React.useEffect(() => {
    const t = setTimeout(() => setDebouncedSymbol(symbol.trim()), 300);
    return () => clearTimeout(t);
  }, [symbol]);

  React.useEffect(() => {
    setPage(1);
  }, [txnType, debouncedSymbol, dateFrom, dateTo]);

  const { data, isLoading } = useInvestmentTransactions({
    user_id: userId,
    txn_type: txnType || undefined,
    symbol: debouncedSymbol || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    page,
    page_size: PAGE_SIZE,
  });

  const rows = data?.items ?? [];
  const totalPages = data?.total_pages ?? 1;
  const hasMore = page < totalPages;
  const hasPrev = page > 1;

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
      invTxnCol.accessor("txn_date", {
        id: "txn_date",
        header: () => (
          <SortHeader
            label="Date"
            columnId="txn_date"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => formatDate(info.getValue()),
      }),
      invTxnCol.accessor("symbol", {
        header: () => (
          <SortHeader
            label="Symbol"
            columnId="symbol"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => {
          const s = info.getValue();
          return s ? <span className="font-mono text-xs">{s}</span> : "—";
        },
      }),
      invTxnCol.accessor("txn_type", {
        header: () => (
          <SortHeader
            label="Type"
            columnId="txn_type"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => (
          <Badge variant={txnBadgeVariant(String(info.getValue()))} className="font-mono text-[10px]">
            {String(info.getValue())}
          </Badge>
        ),
      }),
      invTxnCol.accessor("quantity", {
        header: () => (
          <SortHeader
            label="Qty"
            columnId="quantity"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) =>
          Number(info.getValue()).toLocaleString("en-IN", { maximumFractionDigits: 6 }),
      }),
      invTxnCol.accessor("price_per_unit", {
        id: "price",
        header: () => (
          <SortHeader
            label="Price / unit"
            columnId="price"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => formatCurrency(Number(info.getValue()), 4),
      }),
      invTxnCol.accessor("total_amount", {
        id: "total",
        header: () => (
          <SortHeader
            label="Total"
            columnId="total"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
        cell: (info) => formatCurrency(Number(info.getValue())),
      }),
      invTxnCol.accessor("account_platform", {
        id: "platform",
        header: () => (
          <SortHeader
            label="Platform"
            columnId="platform"
            sorting={sorting}
            onSort={toggleSort}
          />
        ),
      }),
      invTxnCol.display({
        id: "bank",
        header: "Bank link",
        cell: ({ row }) => {
          const id = row.original.bank_transaction_id;
          if (id == null) {
            return <span className="text-muted-foreground text-xs">—</span>;
          }
          return (
            <Link
              href={`/transactions?txn_id=${id}`}
              className="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
            >
              Txn #{id}
              <ExternalLink className="size-3 opacity-70" />
            </Link>
          );
        },
      }),
    ],
    [sorting, toggleSort],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <Card>
      <CardHeader className="space-y-1 pb-3">
        <CardTitle className="text-sm font-medium">Investment transactions</CardTitle>
        <p className="text-xs text-muted-foreground">
          Ledger lines imported from brokers — bank link opens the matching expense row when wired.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">Type</Label>
            <Select value={txnType} onValueChange={(v) => setTxnType(v ?? "")}>
              <SelectTrigger size="sm" className="h-8 w-[140px]">
                <SelectValue placeholder="All types" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">All types</SelectItem>
                {TXN_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">Symbol contains</Label>
            <Input
              className="h-8 w-[140px]"
              placeholder="e.g. INFY"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">From</Label>
            <Input
              type="date"
              className="h-8 w-[11rem]"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">To</Label>
            <Input
              type="date"
              className="h-8 w-[11rem]"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground py-8 text-center">
            No rows in this window — widen filters or check imports.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-md border">
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
                  <TableRow key={row.id}>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            Page {page} of {totalPages}
            {data?.total != null
              ? ` · ${data.total.toLocaleString()} total`
              : ""}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              disabled={!hasPrev || isLoading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              disabled={!hasMore || isLoading}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
