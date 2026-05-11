/**
 * TransactionTable — the core data table for the Transactions page.
 *
 * Uses TanStack Table (v8) for column definitions and row-selection state.
 * Pagination and sorting are server-side: the parent passes current
 * sort/page state and callbacks; we just render what we receive and call
 * the callbacks when the user interacts.
 *
 * TanStack Table is doing the "heavy lifting" for:
 *   - Column definitions (typed, declarative)
 *   - Row-selection state (tracks selected row IDs via getRowId)
 *   - Header and cell render helpers (flexRender)
 *
 * The actual data fetching is handled by the parent via React Query.
 */

"use client"

import * as React from "react"
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type RowSelectionState,
} from "@tanstack/react-table"
import {
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  CheckCircle2,
  Circle,
  ChevronLeft,
  ChevronRight,
} from "lucide-react"

import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  formatCurrency,
  formatDate,
  txnTypeLabel,
  categoryColor,
  cn,
} from "@/lib/utils"
import type {
  Transaction,
  TransactionFilters,
  PaginatedResponse,
} from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface TransactionTableProps {
  /** Paginated data from the server. undefined while loading the first time. */
  data: PaginatedResponse<Transaction> | undefined
  isLoading: boolean
  /** Current filters (includes sort_by, sort_order, page, page_size) */
  filters: TransactionFilters
  /** Called by the table when user changes sort or page */
  onFiltersChange: (update: Partial<TransactionFilters>) => void
  /** TanStack row selection state — tracked by string row ID (= String(txn.id)) */
  rowSelection: RowSelectionState
  onRowSelectionChange: React.Dispatch<React.SetStateAction<RowSelectionState>>
  /** Called when user clicks a row (not the checkbox) to open the edit sheet */
  onRowClick: (txn: Transaction) => void
}

// ─────────────────────────────────────────────────────────────────────────────
// Column helper (typed to Transaction)
// ─────────────────────────────────────────────────────────────────────────────

const col = createColumnHelper<Transaction>()

// ─────────────────────────────────────────────────────────────────────────────
// SortHeader — column header that shows sort state and toggles sort on click
// ─────────────────────────────────────────────────────────────────────────────

interface SortHeaderProps {
  label: string
  /** The sort_by value for this column (e.g. "txn_date") */
  column: NonNullable<TransactionFilters["sort_by"]>
  currentSortBy: TransactionFilters["sort_by"]
  currentSortOrder: TransactionFilters["sort_order"]
  onSort: (column: NonNullable<TransactionFilters["sort_by"]>) => void
}

function SortHeader({
  label,
  column,
  currentSortBy,
  currentSortOrder,
  onSort,
}: SortHeaderProps) {
  const isActive = currentSortBy === column
  const Icon = isActive
    ? currentSortOrder === "asc"
      ? ArrowUp
      : ArrowDown
    : ArrowUpDown

  return (
    <button
      className="flex items-center gap-1 font-medium hover:text-foreground transition-colors"
      onClick={() => onSort(column)}
    >
      {label}
      <Icon className={cn("size-3.5", isActive ? "text-foreground" : "text-muted-foreground/50")} />
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

export function TransactionTable({
  data,
  isLoading,
  filters,
  onFiltersChange,
  rowSelection,
  onRowSelectionChange,
  onRowClick,
}: TransactionTableProps) {
  // Current page rows (empty array while loading so skeleton can render)
  const rows = data?.items ?? []
  const totalPages = data?.total_pages ?? 1
  const currentPage = filters.page ?? 1

  // ── Sort handler ──────────────────────────────────────────────────────────
  // Clicking the same column toggles asc → desc → asc.
  // Clicking a new column defaults to desc (most common starting point).
  function handleSort(column: NonNullable<TransactionFilters["sort_by"]>) {
    if (filters.sort_by === column) {
      onFiltersChange({
        sort_order: filters.sort_order === "asc" ? "desc" : "asc",
        page: 1,
      })
    } else {
      onFiltersChange({ sort_by: column, sort_order: "desc", page: 1 })
    }
  }

  // ── Column definitions ────────────────────────────────────────────────────
  // Using createColumnHelper gives us full TypeScript inference on accessor keys.

  const columns = React.useMemo(
    () => [
      // ── Checkbox column ──────────────────────────────────────────────────
      col.display({
        id: "select",
        header: ({ table }) => (
          <Checkbox
            checked={table.getIsAllPageRowsSelected()}
            indeterminate={
              table.getIsSomePageRowsSelected() && !table.getIsAllPageRowsSelected()
            }
            onCheckedChange={(checked) =>
              table.toggleAllPageRowsSelected(Boolean(checked))
            }
            aria-label="Select all rows"
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(checked) => row.toggleSelected(Boolean(checked))}
            aria-label="Select row"
            onClick={(e) => e.stopPropagation()} // don't open edit sheet
          />
        ),
        enableSorting: false,
        size: 32,
      }),

      // ── Date column ──────────────────────────────────────────────────────
      col.accessor("txn_date", {
        header: () => (
          <SortHeader
            label="Date"
            column="txn_date"
            currentSortBy={filters.sort_by}
            currentSortOrder={filters.sort_order}
            onSort={handleSort}
          />
        ),
        cell: (info) => (
          <span className="text-muted-foreground text-xs">
            {formatDate(info.getValue())}
          </span>
        ),
      }),

      // ── Counterparty column ──────────────────────────────────────────────
      col.accessor("counterparty", {
        header: () => (
          <SortHeader
            label="Counterparty"
            column="counterparty"
            currentSortBy={filters.sort_by}
            currentSortOrder={filters.sort_order}
            onSort={handleSort}
          />
        ),
        cell: (info) => {
          const txn = info.row.original
          // Prefer named counterparty; fall back to truncated raw description
          const display = txn.counterparty ?? txn.raw_description
          return (
            <div className="max-w-[200px]">
              <p className="truncate font-medium text-sm">{display}</p>
              {txn.counterparty && (
                <p className="truncate text-xs text-muted-foreground">
                  {txn.raw_description}
                </p>
              )}
            </div>
          )
        },
      }),

      // ── Category column ──────────────────────────────────────────────────
      col.accessor("counterparty_category", {
        header: "Category",
        cell: (info) => {
          const cat = info.getValue()
          if (!cat) return <span className="text-muted-foreground">—</span>
          return (
            <Badge
              variant="secondary"
              className="inline-flex h-auto min-h-5 w-max max-w-none items-start justify-start gap-1.5 overflow-visible whitespace-normal rounded-md py-1 text-left font-normal"
            >
              <span
                className={cn(
                  "mt-1.5 size-1.5 shrink-0 rounded-full self-start",
                  categoryColor(cat),
                )}
              />
              <span className="break-words">{cat}</span>
            </Badge>
          )
        },
      }),

      // ── Amount column ────────────────────────────────────────────────────
      col.accessor("amount", {
        header: () => (
          <div className="text-right">
            <SortHeader
              label="Amount"
              column="amount"
              currentSortBy={filters.sort_by}
              currentSortOrder={filters.sort_order}
              onSort={handleSort}
            />
          </div>
        ),
        cell: (info) => {
          const txn = info.row.original
          const isInflow = txn.direction === "INFLOW"
          return (
            <div
              className={cn(
                "text-right font-mono font-medium tabular-nums",
                isInflow ? "text-emerald-500" : "text-rose-500",
              )}
            >
              {isInflow ? "+" : "−"}{formatCurrency(info.getValue())}
            </div>
          )
        },
      }),

      // ── Account column ───────────────────────────────────────────────────
      col.accessor("account_id", {
        header: "Account",
        cell: (info) => (
          <span className="text-xs text-muted-foreground font-mono">
            {info.getValue()}
          </span>
        ),
      }),

      // ── Transaction type column ──────────────────────────────────────────
      col.accessor("txn_type", {
        header: "Type",
        cell: (info) => (
          <span className="text-xs text-muted-foreground">
            {txnTypeLabel(info.getValue())}
          </span>
        ),
      }),

      // ── Channel column ───────────────────────────────────────────────────
      col.accessor("channel", {
        header: "Channel",
        cell: (info) => (
          <span className="text-xs text-muted-foreground">
            {info.getValue() ?? "—"}
          </span>
        ),
      }),

      // ── Reviewed status column ───────────────────────────────────────────
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
    // Re-create column defs only when sort state changes (to update sort icons)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filters.sort_by, filters.sort_order],
  )

  // ── TanStack Table instance ───────────────────────────────────────────────
  const table = useReactTable({
    data: rows,
    columns,
    // Identify each row by its DB id so selection persists across page changes
    getRowId: (row) => String(row.id),
    // Pagination and sorting are handled by the server — tell TanStack Table
    // not to do them client-side
    manualPagination: true,
    manualSorting: true,
    rowCount: data?.total ?? 0,
    getCoreRowModel: getCoreRowModel(),
    // Row selection state is controlled externally (parent owns it)
    state: { rowSelection },
    onRowSelectionChange,
    enableRowSelection: true,
  })

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-2">
      {/* ── Table ─────────────────────────────────────────────────────── */}
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id} className="hover:bg-transparent">
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id} className="text-muted-foreground">
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>

          <TableBody>
            {/* Loading skeleton — show placeholder rows while fetching */}
            {isLoading && rows.length === 0 ? (
              Array.from({ length: 10 }).map((_, i) => (
                <TableRow key={i}>
                  {columns.map((_, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-4 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : rows.length === 0 ? (
              // Empty state
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-32 text-center text-muted-foreground"
                >
                  No transactions found.
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.id}
                  data-state={row.getIsSelected() ? "selected" : undefined}
                  className="cursor-pointer"
                  onClick={() => onRowClick(row.original)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      className={
                        cell.column.id === "counterparty_category"
                          ? "whitespace-normal text-left align-top"
                          : undefined
                      }
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* ── Pagination controls ───────────────────────────────────────── */}
      <div className="flex items-center justify-between px-1">
        {/* Left: row count info */}
        <p className="text-xs text-muted-foreground">
          {data
            ? `${data.total.toLocaleString("en-IN")} transaction${data.total !== 1 ? "s" : ""}`
            : "Loading…"}
        </p>

        {/* Center: page info + nav buttons */}
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

        {/* Right: rows per page (fixed at 50 for now — easy to add a select) */}
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
  )
}
