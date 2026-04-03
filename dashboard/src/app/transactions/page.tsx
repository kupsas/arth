/**
 * Transactions page — bank ledger + investment ledger in two tabs.
 *
 * **Bank tab** — filtering, sorting, pagination, bulk actions, inline editing.
 *
 * **Investment tab** — all investment_transactions in the DB (scoped by logged-in
 * user), with filters aligned to the bank tab (search, dates, platform, flow,
 * type, reviewed).
 *
 * State is owned per tab (switching tabs does not reset the other tab’s filters).
 */

"use client"

import * as React from "react"
import { useSearchParams } from "next/navigation"
import { CheckCircle2 } from "lucide-react"
import type { RowSelectionState } from "@tanstack/react-table"

import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TransactionTable } from "@/components/transactions/transaction-table"
import { TransactionFiltersBar } from "@/components/transactions/transaction-filters"
import { InvestmentTransactionFiltersBar } from "@/components/transactions/investment-transaction-filters"
import { InvestmentTransactionTable } from "@/components/transactions/investment-transaction-table"
import { TransactionEditSheet } from "@/components/transactions/transaction-edit-sheet"
import { getPresetRange, type Preset } from "@/components/dashboard/date-range-picker"
import { useAuthMe } from "@/hooks/use-auth"
import { useInvestmentTransactions } from "@/hooks/use-portfolio"
import { useTransactions } from "@/hooks/use-transactions"
import { useBulkUpdate } from "@/hooks/use-transactions"
import type {
  Transaction,
  TransactionFilters,
  DateRange,
  InvestmentTransactionFilters,
} from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// Default filter state — factory so each reset gets fresh today-relative dates
// ─────────────────────────────────────────────────────────────────────────────

function makeDefaultFilters(): TransactionFilters {
  const thisMonth = getPresetRange("this-month")
  return {
    ...thisMonth,          // date_from / date_to = current month
    page: 1,
    page_size: 50,
    sort_by: "txn_date",
    sort_order: "desc",
  }
}

function makeDefaultInvestmentFilters(): InvestmentTransactionFilters {
  const thisMonth = getPresetRange("this-month")
  return {
    ...thisMonth,
    page: 1,
    page_size: 50,
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helper: count active filters (for the Reset button label)
// ─────────────────────────────────────────────────────────────────────────────

function countActiveFilters(filters: TransactionFilters, datePreset: Preset): number {
  let count = 0
  if (filters.search)       count++
  // Count the date preset only if it's not the default "this-month" — deselected
  // ("all") counts as an active filter too (showing all-time is different from default)
  if (datePreset !== "this-month") count++
  if (filters.account_id)   count++
  if (filters.direction)    count++
  if (filters.category)     count++
  if (filters.txn_type)     count++
  if (filters.is_reviewed !== undefined) count++
  return count
}

function countActiveInvestmentFilters(
  filters: InvestmentTransactionFilters,
  datePreset: Preset,
): number {
  let count = 0
  if (filters.search) count++
  if (datePreset !== "this-month") count++
  if (filters.account_platform) count++
  if (filters.flow) count++
  if (filters.txn_type) count++
  if (filters.is_reviewed !== undefined) count++
  return count
}

// ─────────────────────────────────────────────────────────────────────────────
// Investment tab — scoped investment_transactions list
// ─────────────────────────────────────────────────────────────────────────────

function InvestmentTransactionsTabContent() {
  const { data: auth } = useAuthMe()
  const userId = auth?.username ?? null

  const [filters, setFilters] = React.useState<InvestmentTransactionFilters>(
    makeDefaultInvestmentFilters,
  )
  const [datePreset, setDatePreset] = React.useState<Preset>("this-month")

  function handleFiltersChange(update: Partial<InvestmentTransactionFilters>) {
    setFilters((prev) => ({ ...prev, ...update }))
  }

  function handleReset() {
    setFilters(makeDefaultInvestmentFilters())
    setDatePreset("this-month")
  }

  function handleDatePresetChange(preset: Preset, range: DateRange) {
    setDatePreset(preset)
    setFilters((prev) => ({
      ...prev,
      date_from: range.date_from,
      date_to: range.date_to,
      page: 1,
    }))
  }

  const { data, isLoading } = useInvestmentTransactions(
    { ...filters, user_id: userId ?? undefined },
    { enabled: Boolean(userId) },
  )

  if (!userId) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        Sign in to load investment transactions scoped to your holdings.
      </p>
    )
  }

  const activeFilterCount = countActiveInvestmentFilters(filters, datePreset)

  return (
    <>
      <InvestmentTransactionFiltersBar
        filters={filters}
        onFiltersChange={handleFiltersChange}
        onReset={handleReset}
        activeCount={activeFilterCount}
        datePreset={datePreset}
        onDatePresetChange={handleDatePresetChange}
        userId={userId}
      />
      <InvestmentTransactionTable
        data={data}
        isLoading={isLoading}
        filters={filters}
        onFiltersChange={handleFiltersChange}
      />
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Page component (inner — uses useSearchParams; wrapped in Suspense below)
// ─────────────────────────────────────────────────────────────────────────────

function TransactionsPageInner() {
  const searchParams = useSearchParams()
  const txnIdParam = searchParams.get("txn_id")

  // ── Filter state — lazy init so `new Date()` runs in the browser, not at build time
  const [filters, setFilters] = React.useState<TransactionFilters>(makeDefaultFilters)

  // ── Date preset — owned here so it resets correctly with the rest of filters
  const [datePreset, setDatePreset] = React.useState<Preset>("this-month")

  // ── Row selection state (TanStack Table format) ───────────────────────────
  // Keys are string row IDs (= String(txn.id)), values are boolean
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({})

  // ── Edit sheet state ──────────────────────────────────────────────────────
  const [editTxnId, setEditTxnId] = React.useState<number | null>(null)
  const [editSheetOpen, setEditSheetOpen] = React.useState(false)

  // Deep link from Portfolio investment row: /transactions?txn_id=123
  React.useEffect(() => {
    if (!txnIdParam) return
    const id = Number.parseInt(txnIdParam, 10)
    if (!Number.isFinite(id)) return
    setEditTxnId(id)
    setEditSheetOpen(true)
  }, [txnIdParam])

  // ── Data fetching ─────────────────────────────────────────────────────────
  const { data, isLoading } = useTransactions(filters)

  // ── Bulk update mutation ──────────────────────────────────────────────────
  const { mutateAsync: bulkUpdate, isPending: isBulkUpdating } = useBulkUpdate()

  // ─────────────────────────────────────────────────────────────────────────
  // Handlers
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Merges a partial update into the current filter state.
   * The table calls this for sort changes and page changes.
   * The filter bar calls this for search/dropdown changes.
   */
  function handleFiltersChange(update: Partial<TransactionFilters>) {
    setFilters((prev) => ({ ...prev, ...update }))
  }

  /** Resets all filters back to defaults and clears row selection. */
  function handleReset() {
    setFilters(makeDefaultFilters())
    setDatePreset("this-month")
    setRowSelection({})
  }

  /** Called by the filter bar when the user picks a date preset pill. */
  function handleDatePresetChange(preset: Preset, range: DateRange) {
    setDatePreset(preset)
    setFilters((prev) => ({
      ...prev,
      date_from: range.date_from,
      date_to: range.date_to,
      page: 1,
    }))
  }

  /** Opens the edit sheet for the clicked transaction. */
  function handleRowClick(txn: Transaction) {
    setEditTxnId(txn.id)
    setEditSheetOpen(true)
  }

  /** Marks all selected transactions as reviewed in one PATCH call. */
  async function handleBulkMarkReviewed() {
    const ids = Object.keys(rowSelection)
      .filter((k) => rowSelection[k])
      .map(Number)

    if (ids.length === 0) return

    await bulkUpdate({ ids, update: { is_reviewed: true } })
    setRowSelection({}) // clear selection after bulk action
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Derived values
  // ─────────────────────────────────────────────────────────────────────────

  const selectedCount = Object.values(rowSelection).filter(Boolean).length
  const activeFilterCount = countActiveFilters(filters, datePreset)

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4">

      {/* ── Page heading ────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-xl font-semibold">Transactions</h1>
        <p className="text-sm text-muted-foreground">
          Browse bank activity and broker ledger lines — filter, review, and reconcile.
        </p>
      </div>

      <Tabs defaultValue="bank" className="w-full">
        <TabsList variant="line" className="mb-1 h-9 w-full min-w-0 justify-start">
          <TabsTrigger value="bank" className="text-xs">
            Bank transactions
          </TabsTrigger>
          <TabsTrigger value="investments" className="text-xs">
            Investment transactions
          </TabsTrigger>
        </TabsList>

        <TabsContent value="bank" className="mt-4 flex flex-col gap-4">
          {selectedCount > 0 && (
            <div className="flex items-center justify-end">
              <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2">
                <span className="text-sm font-medium">
                  {selectedCount} selected
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleBulkMarkReviewed}
                  disabled={isBulkUpdating}
                  className="gap-1.5"
                >
                  <CheckCircle2 className="size-4 text-emerald-500" />
                  {isBulkUpdating ? "Updating…" : "Mark as Reviewed"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setRowSelection({})}
                  className="text-muted-foreground"
                >
                  Clear
                </Button>
              </div>
            </div>
          )}

          <TransactionFiltersBar
            filters={filters}
            onFiltersChange={handleFiltersChange}
            onReset={handleReset}
            activeCount={activeFilterCount}
            datePreset={datePreset}
            onDatePresetChange={handleDatePresetChange}
          />

          <TransactionTable
            data={data}
            isLoading={isLoading}
            filters={filters}
            onFiltersChange={handleFiltersChange}
            rowSelection={rowSelection}
            onRowSelectionChange={setRowSelection}
            onRowClick={handleRowClick}
          />

          <TransactionEditSheet
            txnId={editTxnId}
            open={editSheetOpen}
            onOpenChange={setEditSheetOpen}
          />
        </TabsContent>

        <TabsContent value="investments" className="mt-4">
          <InvestmentTransactionsTabContent />
        </TabsContent>
      </Tabs>

    </div>
  )
}

export default function TransactionsPage() {
  return (
    <React.Suspense
      fallback={
        <div className="flex flex-col gap-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <TransactionsPageInner />
    </React.Suspense>
  )
}
