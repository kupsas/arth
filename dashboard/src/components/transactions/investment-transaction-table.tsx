/**
 * InvestmentTransactionTable — read-only ledger grid for /transactions (investment tab).
 *
 * Pagination is server-side (same pattern as ``TransactionTable``). Rows are not
 * clickable for editing here — use Review or Portfolio for deeper workflows.
 */

"use client";

import * as React from "react";
import Link from "next/link";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { CheckCircle2, ChevronLeft, ChevronRight, Circle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useHoldings } from "@/hooks/use-portfolio";
import { formatCurrency, formatDate } from "@/lib/utils";
import type {
  InvestmentTxn,
  InvestmentTransactionFilters,
  PaginatedResponse,
} from "@/lib/types";

interface InvestmentTransactionTableProps {
  data: PaginatedResponse<InvestmentTxn> | undefined;
  isLoading: boolean;
  filters: InvestmentTransactionFilters;
  onFiltersChange: (update: Partial<InvestmentTransactionFilters>) => void;
  /** Used to resolve holding names for the Linked holding column. */
  userId: string;
}

const col = createColumnHelper<InvestmentTxn>();

function txnBadgeVariant(t: string): "default" | "secondary" | "outline" | "destructive" {
  if (t === "SELL" || t === "SWITCH_OUT") return "destructive";
  if (t === "DIVIDEND") return "secondary";
  return "default";
}

export function InvestmentTransactionTable({
  data,
  isLoading,
  filters,
  onFiltersChange,
  userId,
}: InvestmentTransactionTableProps) {
  const rows = data?.items ?? [];
  const totalPages = data?.total_pages ?? 1;
  const currentPage = filters.page ?? 1;

  const { data: holdings } = useHoldings({ user_id: userId });
  const holdingNameById = React.useMemo(() => {
    const m = new Map<number, string>();
    for (const h of holdings ?? []) {
      if (h.id != null) m.set(h.id, h.name);
    }
    return m;
  }, [holdings]);

  const columns = React.useMemo(
    () => [
      col.accessor("txn_date", {
        header: "Date",
        cell: (info) => (
          <span className="text-muted-foreground text-xs">{formatDate(info.getValue())}</span>
        ),
      }),
      col.accessor("symbol", {
        header: "Symbol",
        cell: (info) => {
          const s = info.getValue();
          return s ? <span className="font-mono text-xs">{s}</span> : "—";
        },
      }),
      col.accessor("txn_type", {
        header: "Type",
        cell: (info) => (
          <Badge variant={txnBadgeVariant(String(info.getValue()))} className="font-mono text-[10px]">
            {String(info.getValue())}
          </Badge>
        ),
      }),
      col.accessor("quantity", {
        header: "Qty",
        cell: (info) =>
          Number(info.getValue()).toLocaleString("en-IN", { maximumFractionDigits: 6 }),
      }),
      col.accessor("price_per_unit", {
        header: "Price / unit",
        cell: (info) => formatCurrency(Number(info.getValue()), 4),
      }),
      col.accessor("total_amount", {
        header: "Total",
        cell: (info) => formatCurrency(Number(info.getValue())),
      }),
      col.accessor("account_platform", {
        header: "Platform",
        cell: (info) => (
          <span className="text-xs text-muted-foreground">{info.getValue()}</span>
        ),
      }),
      col.accessor("holding_id", {
        header: "Linked holding",
        cell: (info) => {
          const hid = info.getValue() as number | null;
          if (hid == null) {
            return (
              <span className="text-xs text-amber-600 dark:text-amber-500" title="Not linked to a portfolio row">
                Unlinked
              </span>
            );
          }
          const label = holdingNameById.get(hid) ?? `Holding #${hid}`;
          return (
            <Link
              href="/portfolio"
              className="block max-w-[200px] truncate text-xs text-primary underline-offset-2 hover:underline"
              title={`${label} (id ${hid}) — open Portfolio to see this position`}
            >
              {label}
              <span className="ml-1 font-mono text-[10px] text-muted-foreground">#{hid}</span>
            </Link>
          );
        },
      }),
      col.accessor("notes", {
        header: "Notes",
        cell: (info) => {
          const n = info.getValue();
          if (!n) return <span className="text-muted-foreground">—</span>;
          return (
            <span className="max-w-[160px] truncate block text-xs" title={n}>
              {n}
            </span>
          );
        },
      }),
      col.accessor("is_reviewed", {
        header: "Reviewed",
        cell: (info) =>
          info.getValue() ? (
            <CheckCircle2 className="size-4 text-emerald-500" />
          ) : (
            <Circle className="size-4 text-muted-foreground/30" />
          ),
      }),
    ],
    [holdingNameById],
  );

  const table = useReactTable({
    data: rows,
    columns,
    manualPagination: true,
    rowCount: data?.total ?? 0,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="flex flex-col gap-2">
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="hover:bg-transparent">
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id} className="text-muted-foreground whitespace-nowrap">
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {isLoading && rows.length === 0 ? (
              Array.from({ length: 8 }).map((_, i) => (
                <TableRow key={i}>
                  {columns.map((_, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-32 text-center text-muted-foreground"
                >
                  No investment transactions found.
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between px-1">
        <p className="text-xs text-muted-foreground">
          {data
            ? `${data.total.toLocaleString()} row${data.total !== 1 ? "s" : ""}`
            : "Loading…"}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="icon-sm"
            disabled={currentPage <= 1}
            onClick={() => onFiltersChange({ page: currentPage - 1 })}
            aria-label="Previous page"
          >
            <ChevronLeft />
          </Button>
          <span className="text-xs text-muted-foreground">
            Page {currentPage} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="icon-sm"
            disabled={currentPage >= totalPages}
            onClick={() => onFiltersChange({ page: currentPage + 1 })}
            aria-label="Next page"
          >
            <ChevronRight />
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {data
            ? `${(currentPage - 1) * (filters.page_size ?? 50) + 1}–${Math.min(
                currentPage * (filters.page_size ?? 50),
                data.total,
              )}`
            : ""}
        </p>
      </div>
    </div>
  );
}
