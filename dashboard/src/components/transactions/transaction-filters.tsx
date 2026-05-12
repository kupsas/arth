/**
 * TransactionFilters — the filter bar above the transaction table.
 *
 * Provides controls for:
 *   - Free-text search (counterparty + raw_description)
 *   - Date range (reuses the DateRangePicker from the dashboard)
 *   - Account (dropdown populated from accounts-summary)
 *   - Direction (All / Inflow / Outflow)
 *   - Category (dropdown of all categories)
 *   - Transaction type
 *   - Reviewed status
 *
 * Design decision: the parent owns ALL filter state. This component only
 * renders inputs and fires callbacks. No local filter state here — makes
 * the "Reset filters" button trivial to implement (parent just resets its state).
 *
 * Search is debounced here (300ms) to avoid a server request on every keystroke.
 */

"use client"

import * as React from "react"
import { SearchIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  DateRangePicker,
  type Preset,
} from "@/components/dashboard/date-range-picker"
import { useAccountsSummary } from "@/hooks/use-metrics"
import type { TransactionFilters, Direction, CounterpartyCategory, TxnType, DateRange } from "@/lib/types"
import posthog from "posthog-js"

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const CATEGORIES: CounterpartyCategory[] = [
  "Asset Markets",
  "Entertainment & Events",
  "Fees, Charges & Interest",
  "Financial Services, Insurance & Banking",
  "Food & Dining",
  "Friends and Family",
  "Gifts & Personal Transfers",
  "Healthcare & Pharmacy",
  "Miscellaneous",
  "Mobile, OTT & Subscriptions",
  "Personal Grooming",
  "Rent & Housing",
  "Salary & Income",
  "Self Transfer",
  "Shopping & E-commerce",
  "Swiggy",
  "Transport & Fuel",
  "Travel & Stay",
  "Utilities & Internet",
]

const TXN_TYPES: { value: TxnType; label: string }[] = [
  { value: "BANK_TRANSFER",          label: "Bank Transfer" },
  { value: "CARD_EXPENSE",           label: "Card Expense" },
  { value: "CARD_PAYMENT",           label: "CC Bill Payment" },
  { value: "EQUITY_PURCHASE",        label: "Equity Purchase" },
  { value: "EQUITY_SALE",            label: "Equity Sale" },
  { value: "EXPENSE_OTHER",          label: "Other Expense" },
  { value: "INCOME_DIVIDEND",        label: "Dividend" },
  { value: "INCOME_OTHER",           label: "Other Income" },
  { value: "INCOME_SALARY",          label: "Salary" },
  { value: "LOAN_INSURANCE_PAYMENT", label: "Loan / Insurance" },
  { value: "MF_PURCHASE",            label: "MF Purchase" },
  { value: "MF_SALE",                label: "MF Redemption" },
  { value: "SELF_TRANSFER",          label: "Self Transfer" },
  { value: "UPI_EXPENSE",            label: "UPI Expense" },
  { value: "UPI_TRANSFER",           label: "UPI Transfer" },
]

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface TransactionFiltersProps {
  filters: TransactionFilters
  onFiltersChange: (update: Partial<TransactionFilters>) => void
  onReset: () => void
  /** Number of active filters (shown in the Reset button). */
  activeCount: number
  /**
   * Controlled date preset — owned by the parent page so that resetting
   * filters also resets the active pill in the date picker.
   */
  datePreset: Preset
  onDatePresetChange: (preset: Preset, range: DateRange) => void
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function TransactionFiltersBar({
  filters,
  onFiltersChange,
  onReset,
  activeCount,
  datePreset,
  onDatePresetChange,
}: TransactionFiltersProps) {
  // ── Accounts list for the account dropdown ────────────────────────────────
  const { data: accounts } = useAccountsSummary()

  // ── Debounced search ──────────────────────────────────────────────────────
  // We store the raw input value in local state so the text box stays responsive
  // while we debounce the actual API call.
  const [searchInput, setSearchInput] = React.useState(filters.search ?? "")

  // Keep searchInput in sync if parent resets filters
  React.useEffect(() => {
    setSearchInput(filters.search ?? "")
  }, [filters.search])

  // After 300ms of no typing, push the value up to the parent (triggers a fetch)
  React.useEffect(() => {
    const timer = setTimeout(() => {
      // Only call parent if the value actually changed
      if (searchInput !== (filters.search ?? "")) {
        onFiltersChange({ search: searchInput || undefined, page: 1 })
      }
    }, 300)
    return () => clearTimeout(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput])

  // ── Date range state ──────────────────────────────────────────────────────
  // Preset is controlled by the parent (so reset works correctly).
  // customRange is local because it's just intermediate calendar UI state.
  const [customRange, setCustomRange] = React.useState<DateRange>({})

  function handlePresetChange(newPreset: Preset, newRange: DateRange) {
    // Tell parent about the new preset and its date range
    onDatePresetChange(newPreset, newRange)
    onFiltersChange({
      date_from: newRange.date_from,
      date_to: newRange.date_to,
      page: 1,
    })
  }

  function handleCustomChange(newRange: DateRange) {
    setCustomRange(newRange)
    onDatePresetChange("custom", newRange)
    onFiltersChange({
      date_from: newRange.date_from,
      date_to: newRange.date_to,
      page: 1,
    })
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-3">
      {/* ── Row 1: Search + Date Range ──────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Search input */}
        <div className="relative flex-1 min-w-[180px]">
          <SearchIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground pointer-events-none" />
          <Input
            type="search"
            placeholder="Search counterparty or description…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-8 h-8 text-sm"
          />
        </div>

        {/* Date range picker — controlled by parent; clearable so pills can be deselected */}
        <DateRangePicker
          preset={datePreset}
          customRange={customRange}
          onPresetChange={handlePresetChange}
          onCustomChange={handleCustomChange}
          clearable
        />
      </div>

      {/* ── Row 2: Dropdowns ────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">

        {/* Account */}
        <Select
          value={filters.account_id ?? ""}
          onValueChange={(v) =>
            onFiltersChange({ account_id: v || undefined, page: 1 })
          }
        >
          <SelectTrigger size="sm" className="w-[140px]">
            <SelectValue placeholder="All accounts" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All accounts</SelectItem>
            {accounts?.map((acc) => (
              <SelectItem key={acc.account_id} value={acc.account_id}>
                {acc.account_id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Direction */}
        <Select
          value={filters.direction ?? ""}
          onValueChange={(v) =>
            onFiltersChange({ direction: (v as Direction) || undefined, page: 1 })
          }
        >
          <SelectTrigger size="sm" className="w-[110px]">
            <SelectValue placeholder="All flows" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All flows</SelectItem>
            <SelectItem value="INFLOW">Inflow</SelectItem>
            <SelectItem value="OUTFLOW">Outflow</SelectItem>
          </SelectContent>
        </Select>

        {/* Category */}
        <Select
          value={filters.category ?? ""}
          onValueChange={(v) => {
            if (v) posthog.capture("transaction_category_filter_applied", { category: v });
            onFiltersChange({ category: v || undefined, page: 1 });
          }}
        >
          <SelectTrigger size="sm" className="w-[160px]">
            <SelectValue placeholder="All categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All categories</SelectItem>
            {CATEGORIES.map((cat) => (
              <SelectItem key={cat} value={cat}>
                {cat}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Transaction type */}
        <Select
          value={filters.txn_type ?? ""}
          onValueChange={(v) =>
            onFiltersChange({ txn_type: (v as TxnType) || undefined, page: 1 })
          }
        >
          <SelectTrigger size="sm" className="w-[150px]">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All types</SelectItem>
            {TXN_TYPES.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Reviewed status */}
        <Select
          value={
            filters.is_reviewed === undefined
              ? ""
              : filters.is_reviewed
              ? "true"
              : "false"
          }
          onValueChange={(v) =>
            onFiltersChange({
              is_reviewed: v === "" ? undefined : v === "true",
              page: 1,
            })
          }
        >
          <SelectTrigger size="sm" className="w-[130px]">
            <SelectValue placeholder="Any status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">Any status</SelectItem>
            <SelectItem value="true">Reviewed</SelectItem>
            <SelectItem value="false">Unreviewed</SelectItem>
          </SelectContent>
        </Select>

        {/* Reset button — only shown when filters are active */}
        {activeCount > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onReset}
            className="gap-1.5 text-muted-foreground"
          >
            <XIcon className="size-3.5" />
            Reset ({activeCount})
          </Button>
        )}
      </div>
    </div>
  )
}
