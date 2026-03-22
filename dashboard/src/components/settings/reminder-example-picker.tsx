"use client"

/**
 * ReminderExamplePicker — pick up to 5 past OUTFLOW transactions as mapping examples.
 *
 * The API requires each example to be an expense with a non-empty counterparty so
 * fingerprints stay reliable. We search the last year of outflows by default.
 */

import * as React from "react"
import { Plus, Search } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { useTransaction, useTransactions } from "@/hooks/use-transactions"
import type { Transaction, TransactionFilters } from "@/lib/types"
import { cn, formatCurrency } from "@/lib/utils"

/** Must match api.reminder_matching.MAX_EXAMPLE_TRANSACTION_IDS */
const MAX_EXAMPLES = 5

function localYYYYMMDD(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, "0")
  const day = String(d.getDate()).padStart(2, "0")
  return `${y}-${m}-${day}`
}

function defaultPickerFilters(): TransactionFilters {
  const end = new Date()
  const start = new Date(end)
  start.setFullYear(start.getFullYear() - 1)
  return {
    date_from: localYYYYMMDD(start),
    date_to: localYYYYMMDD(end),
    direction: "OUTFLOW",
    page: 1,
    page_size: 25,
    sort_by: "txn_date",
    sort_order: "desc",
  }
}

function ExampleTxnChip({
  id,
  onRemove,
  disabled,
}: {
  id: number
  onRemove: () => void
  disabled?: boolean
}) {
  const { data: txn, isLoading } = useTransaction(id)
  const label = isLoading
    ? "…"
    : txn
      ? `${txn.txn_date} · ${txn.counterparty ?? "—"} · ${formatCurrency(txn.amount)}`
      : `ID ${id}`
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-md border border-border",
        "bg-muted/40 px-2 py-0.5 text-xs",
      )}
    >
      <span className="truncate" title={label}>
        {label}
      </span>
      <button
        type="button"
        className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50"
        onClick={onRemove}
        disabled={disabled}
        aria-label="Remove example transaction"
      >
        ×
      </button>
    </span>
  )
}

export interface ReminderExamplePickerProps {
  ids: number[]
  onIdsChange: (ids: number[]) => void
  disabled?: boolean
}

export function ReminderExamplePicker({
  ids,
  onIdsChange,
  disabled,
}: ReminderExamplePickerProps) {
  const [open, setOpen] = React.useState(false)
  const [searchInput, setSearchInput] = React.useState("")
  const [debouncedSearch, setDebouncedSearch] = React.useState("")
  const [filters, setFilters] = React.useState<TransactionFilters>(defaultPickerFilters)

  React.useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const queryFilters = React.useMemo(
    () => ({
      ...filters,
      search: debouncedSearch || undefined,
    }),
    [filters, debouncedSearch],
  )

  const { data, isLoading } = useTransactions(queryFilters, { enabled: open })

  React.useEffect(() => {
    if (!open) {
      setSearchInput("")
      setDebouncedSearch("")
      setFilters(defaultPickerFilters())
    }
  }, [open])

  function toggleRow(row: Transaction) {
    const id = row.id
    if (id == null) return
    if (ids.includes(id)) {
      onIdsChange(ids.filter((x) => x !== id))
      return
    }
    if (ids.length >= MAX_EXAMPLES) return
    onIdsChange([...ids, id])
  }

  const totalPages = data?.total_pages ?? 1

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        Optional — add up to {MAX_EXAMPLES} past payments that look like this obligation.
        The dashboard uses them to recognize the same payee and amount band each month.
      </p>
      <div className="flex flex-wrap gap-2 min-h-[28px]">
        {ids.map((id) => (
          <ExampleTxnChip
            key={id}
            id={id}
            disabled={disabled}
            onRemove={() => onIdsChange(ids.filter((x) => x !== id))}
          />
        ))}
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger
          disabled={disabled}
          render={
            <Button type="button" variant="outline" size="sm" disabled={disabled}>
              <Plus className="size-3.5 mr-1" />
              Browse transactions ({ids.length}/{MAX_EXAMPLES})
            </Button>
          }
        />
        <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col gap-3">
          <DialogHeader>
            <DialogTitle>Pick example transactions</DialogTitle>
            <DialogDescription>
              Expenses only, last 12 months by default. Select payments that should match
              this reminder in future months (same counterparty and similar amount).
            </DialogDescription>
          </DialogHeader>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-8 h-8 text-sm"
              placeholder="Search counterparty or description…"
              value={searchInput}
              onChange={(e) => {
                setSearchInput(e.target.value)
                setFilters((f) => ({ ...f, page: 1 }))
              }}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border">
            {isLoading && (
              <p className="p-3 text-xs text-muted-foreground">Loading…</p>
            )}
            {!isLoading && data && data.items.length === 0 && (
              <p className="p-3 text-xs text-muted-foreground">No transactions found.</p>
            )}
            {!isLoading && data && data.items.length > 0 && (
              <ul className="divide-y divide-border text-xs">
                {data.items.map((row) => {
                  const id = row.id
                  if (id == null) return null
                  const checked = ids.includes(id)
                  const atCap = ids.length >= MAX_EXAMPLES && !checked
                  const cp = (row.counterparty ?? "").trim()
                  const disabledRow = atCap || !cp
                  return (
                    <li key={id}>
                      <div
                        className={cn(
                          "flex items-start gap-2 px-2 py-2",
                          !disabledRow && "hover:bg-muted/40",
                          disabledRow && "opacity-50",
                        )}
                      >
                        <Checkbox
                          checked={checked}
                          disabled={disabledRow}
                          onCheckedChange={() => toggleRow(row)}
                          className="mt-0.5"
                          onClick={(e) => e.stopPropagation()}
                        />
                        <span className="min-w-0 flex-1 text-left">
                          <span className="font-medium">{row.txn_date}</span>
                          <span className="text-muted-foreground">
                            {" "}
                            · {row.counterparty ?? "—"} · {formatCurrency(row.amount)}
                          </span>
                          {!cp && (
                            <span className="block text-amber-600 dark:text-amber-500">
                              Needs a counterparty to use as an example
                            </span>
                          )}
                        </span>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-border pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={filters.page! <= 1}
              onClick={() =>
                setFilters((f) => ({ ...f, page: Math.max(1, (f.page ?? 1) - 1) }))
              }
            >
              Previous
            </Button>
            <span className="text-xs text-muted-foreground">
              Page {filters.page} / {totalPages}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={(filters.page ?? 1) >= totalPages}
              onClick={() =>
                setFilters((f) => ({ ...f, page: (f.page ?? 1) + 1 }))
              }
            >
              Next
            </Button>
          </div>
          <Button type="button" className="w-full" onClick={() => setOpen(false)}>
            Done
          </Button>
        </DialogContent>
      </Dialog>
    </div>
  )
}
