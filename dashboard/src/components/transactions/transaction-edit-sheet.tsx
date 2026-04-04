/**
 * TransactionEditSheet — a slide-in panel for editing a single transaction.
 *
 * Opens from the right side when the user clicks a row in the table.
 * Also used by the Review Queue ("Edit & Approve" button).
 *
 * Read-only display fields:
 *   - Date, amount, direction, account, channel, raw description
 *
 * Editable fields (mirrors TransactionUpdate in the backend):
 *   - counterparty (text)
 *   - counterparty_category (select)
 *   - txn_type (select)
 *   - notes (textarea)
 *   - is_reviewed (checkbox)
 *
 * Props:
 *   - txnId          the ID of the transaction to display/edit (null → nothing shown)
 *   - open           controlled open state
 *   - onOpenChange   called when the sheet closes
 *   - forceReviewed  if true, pre-ticks is_reviewed to true (used by review queue)
 */

"use client"

import * as React from "react"
import { ArrowLeft, CheckCircle2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { useTransaction, useUpdateTransaction } from "@/hooks/use-transactions"
import {
  formatCurrency,
  formatDate,
  txnTypeLabel,
  categoryColor,
  cn,
} from "@/lib/utils"
import type {
  CounterpartyCategory,
  SpendCategory,
  TransactionUpdate,
  TxnType,
} from "@/lib/types"

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

// spend_category options — only OUTFLOW transactions have one
const SPEND_CATEGORIES: { value: SpendCategory; label: string; color: string }[] = [
  { value: "NEED",       label: "Need",       color: "bg-blue-500" },
  { value: "WANT",       label: "Want",       color: "bg-orange-500" },
  { value: "INVESTMENT", label: "Investment", color: "bg-purple-500" },
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

interface TransactionEditSheetProps {
  txnId: number | null
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When true, pre-checks is_reviewed to true (used by review queue). */
  forceReviewed?: boolean
  /** Stacked above another sheet (e.g. chart drill-down) — higher z-index + optional back. */
  nested?: boolean
  onNestedBack?: () => void
}

// ─────────────────────────────────────────────────────────────────────────────
// Field Row helper — consistent label + control layout
// ─────────────────────────────────────────────────────────────────────────────

function FieldRow({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

const EXCLUSION_PRESETS = [
  { value: "refund", label: "Refund / reversed" },
  { value: "test_transaction", label: "Test transaction" },
  { value: "duplicate", label: "Duplicate" },
  { value: "other", label: "Other (describe below)" },
] as const

export function TransactionEditSheet({
  txnId,
  open,
  onOpenChange,
  forceReviewed = false,
  nested = false,
  onNestedBack,
}: TransactionEditSheetProps) {
  // Fetch the full transaction data when the sheet is open
  const { data: txn, isLoading } = useTransaction(txnId)
  const { mutateAsync: updateTransaction, isPending: isSaving } =
    useUpdateTransaction()

  // ── Form state ────────────────────────────────────────────────────────────
  // Each field is optional (undefined = unchanged when saving)
  const [counterparty, setCounterparty] = React.useState("")
  const [category, setCategory] = React.useState<CounterpartyCategory | "">("")
  const [txnType, setTxnType] = React.useState<TxnType | "">("")
  const [spendCategory, setSpendCategory] = React.useState<SpendCategory | "">("")
  const [notes, setNotes] = React.useState("")
  const [isReviewed, setIsReviewed] = React.useState(false)
  const [excludeAnalytics, setExcludeAnalytics] = React.useState(false)
  const [exclusionPreset, setExclusionPreset] = React.useState<string>("refund")
  const [exclusionOther, setExclusionOther] = React.useState("")

  // ── Sync form state when transaction loads ────────────────────────────────
  // Whenever a new transaction is loaded (txn changes), reset all form fields
  // to match the current server values. This prevents stale data from a
  // previously-opened transaction from bleeding into the new one.
  React.useEffect(() => {
    if (!txn) return
    setCounterparty(txn.counterparty ?? "")
    setCategory((txn.counterparty_category as CounterpartyCategory) ?? "")
    setTxnType((txn.txn_type as TxnType) ?? "")
    setSpendCategory((txn.spend_category as SpendCategory) ?? "")
    setNotes(txn.notes ?? "")
    // forceReviewed means review queue wants to default this to true
    setIsReviewed(forceReviewed || txn.is_reviewed)
    setExcludeAnalytics(Boolean(txn.exclude_from_analytics))
    const reason = txn.exclusion_reason ?? ""
    const known = EXCLUSION_PRESETS.find((p) => p.value === reason)
    if (known) {
      setExclusionPreset(reason)
      setExclusionOther("")
    } else if (reason) {
      setExclusionPreset("other")
      setExclusionOther(reason)
    } else {
      setExclusionPreset("refund")
      setExclusionOther("")
    }
  }, [txn, forceReviewed])

  // ── Save handler ──────────────────────────────────────────────────────────
  async function handleSave() {
    if (!txnId) return

    // Build update payload — only include fields that have changed
    const update: TransactionUpdate = {}

    if (txn) {
      if (counterparty !== (txn.counterparty ?? ""))
        update.counterparty = counterparty || null
      if (category !== ((txn.counterparty_category as CounterpartyCategory) ?? ""))
        update.counterparty_category = (category as CounterpartyCategory) || null
      if (txnType !== ((txn.txn_type as TxnType) ?? ""))
        update.txn_type = (txnType as TxnType) || null
      if (spendCategory !== ((txn.spend_category as SpendCategory) ?? ""))
        update.spend_category = (spendCategory as SpendCategory) || null
      if (notes !== (txn.notes ?? ""))
        update.notes = notes || null
      if (isReviewed !== txn.is_reviewed)
        update.is_reviewed = isReviewed

      const wasExcluded = Boolean(txn.exclude_from_analytics)
      const reasonOut =
        exclusionPreset === "other"
          ? (exclusionOther.trim() || "other")
          : exclusionPreset
      if (excludeAnalytics !== wasExcluded || (excludeAnalytics && reasonOut !== (txn.exclusion_reason ?? ""))) {
        update.exclude_from_analytics = excludeAnalytics
        update.exclusion_reason = excludeAnalytics ? reasonOut : null
      }
    }

    // If forceReviewed and not already reviewed, always include it
    if (forceReviewed && txn && !txn.is_reviewed) {
      update.is_reviewed = true
    }

    try {
      await updateTransaction({ id: txnId, update })
      onOpenChange(false)
    } catch {
      // React Query surfaces this error — could add a toast here later
    }
  }

  // ── Quick "Mark as Reviewed" ──────────────────────────────────────────────
  async function handleMarkReviewed() {
    if (!txnId) return
    try {
      await updateTransaction({ id: txnId, update: { is_reviewed: true } })
      onOpenChange(false)
    } catch {
      // silently fail for now
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className={`w-full sm:max-w-md overflow-y-auto ${nested ? "!z-[61]" : ""}`}
        overlayClassName={nested ? "!z-[60]" : undefined}
      >
        <SheetHeader>
          {nested && onNestedBack && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mb-2 -ml-2 w-fit gap-1 px-2"
              onClick={onNestedBack}
            >
              <ArrowLeft className="size-4" />
              Back to list
            </Button>
          )}
          <SheetTitle>Edit Transaction</SheetTitle>
          <SheetDescription>
            Update the classification details for this transaction.
          </SheetDescription>
        </SheetHeader>

        {/* ── Content ──────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-4 px-4 pb-2">
          {isLoading || !txn ? (
            // Skeleton while transaction is loading
            <div className="flex flex-col gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="flex flex-col gap-1">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ))}
            </div>
          ) : (
            <>
              {/* ── Read-only info card ───────────────────────────────── */}
              <div className="rounded-lg bg-muted/40 border p-3 flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-xs text-muted-foreground">
                      {formatDate(txn.txn_date)} · {txn.account_id}
                      {txn.channel ? ` · ${txn.channel}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground break-words line-clamp-3">
                      {txn.raw_description}
                    </p>
                  </div>
                  <div
                    className={cn(
                      "shrink-0 text-lg font-mono font-semibold tabular-nums",
                      txn.direction === "INFLOW"
                        ? "text-emerald-500"
                        : "text-rose-500",
                    )}
                  >
                    {txn.direction === "INFLOW" ? "+" : "−"}
                    {formatCurrency(txn.amount)}
                  </div>
                </div>
              </div>

              {/* ── Editable fields ───────────────────────────────────── */}

              {/* Counterparty name */}
              <FieldRow label="Counterparty">
                <Input
                  value={counterparty}
                  onChange={(e) => setCounterparty(e.target.value)}
                  placeholder="e.g. Swiggy, Netflix, Amazon"
                  className="h-8 text-sm"
                />
              </FieldRow>

              {/* Category */}
              <FieldRow label="Category">
                <Select
                  value={category}
                  onValueChange={(v) =>
                    setCategory(v as CounterpartyCategory | "")
                  }
                >
                  <SelectTrigger className="h-8 w-full text-sm">
                    <SelectValue placeholder="Select category…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">— None —</SelectItem>
                    {CATEGORIES.map((cat) => (
                      <SelectItem key={cat} value={cat}>
                        <span
                          className={cn(
                            "size-2 shrink-0 rounded-full",
                            categoryColor(cat),
                          )}
                        />
                        {cat}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>

              {/* Transaction type */}
              <FieldRow label="Transaction Type">
                <Select
                  value={txnType}
                  onValueChange={(v) => setTxnType(v as TxnType | "")}
                >
                  <SelectTrigger className="h-8 w-full text-sm">
                    <SelectValue placeholder="Select type…" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">— None —</SelectItem>
                    {TXN_TYPES.map(({ value, label }) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </FieldRow>

              {/* Spend Category — only meaningful for outflow transactions */}
              {txn.direction === "OUTFLOW" && (
                <FieldRow label="Spend Category">
                  <Select
                    value={spendCategory}
                    onValueChange={(v) => setSpendCategory(v as SpendCategory | "")}
                  >
                    <SelectTrigger className="h-8 w-full text-sm">
                      <SelectValue placeholder="Select category…" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="">— None (let pipeline decide) —</SelectItem>
                      {SPEND_CATEGORIES.map(({ value, label, color }) => (
                        <SelectItem key={value} value={value}>
                          <span
                            className={cn("size-2 shrink-0 rounded-full", color)}
                          />
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FieldRow>
              )}

              {/* Notes */}
              <FieldRow label="Notes">
                <Textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Optional note about this transaction…"
                  className="text-sm min-h-[60px] resize-none"
                  rows={2}
                />
              </FieldRow>

              {/* Reviewed checkbox */}
              <div className="flex items-center gap-2">
                <Checkbox
                  id="is-reviewed"
                  checked={isReviewed}
                  onCheckedChange={(checked) => setIsReviewed(Boolean(checked))}
                />
                <label
                  htmlFor="is-reviewed"
                  className="text-sm cursor-pointer select-none"
                >
                  Mark as reviewed
                </label>
              </div>

              {/* Exclude from dashboard metrics */}
              <div className="rounded-lg border border-border p-3 space-y-3">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="exclude-analytics"
                    checked={excludeAnalytics}
                    onCheckedChange={(checked) => setExcludeAnalytics(Boolean(checked))}
                  />
                  <label
                    htmlFor="exclude-analytics"
                    className="text-sm cursor-pointer select-none font-medium"
                  >
                    Exclude from analytics
                  </label>
                </div>
                <p className="text-xs text-muted-foreground -mt-1 pl-6">
                  Transaction stays in your ledger; charts and goal math ignore it.
                </p>
                {excludeAnalytics && (
                  <div className="pl-6 space-y-2">
                    <FieldRow label="Reason">
                      <Select
                        value={exclusionPreset}
                        onValueChange={(v) => setExclusionPreset(v ?? "refund")}
                      >
                        <SelectTrigger className="h-8 w-full text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {EXCLUSION_PRESETS.map((p) => (
                            <SelectItem key={p.value} value={p.value}>
                              {p.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FieldRow>
                    {exclusionPreset === "other" && (
                      <FieldRow label="Describe">
                        <Textarea
                          value={exclusionOther}
                          onChange={(e) => setExclusionOther(e.target.value)}
                          className="text-sm min-h-[50px]"
                          placeholder="Why should this be ignored?"
                        />
                      </FieldRow>
                    )}
                  </div>
                )}
              </div>

              {/* Current category badge (nice visual confirmation) */}
              {category && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Preview:</span>
                  <Badge variant="secondary" className="font-normal">
                    <span
                      className={cn(
                        "size-1.5 shrink-0 rounded-full",
                        categoryColor(category),
                      )}
                    />
                    {category}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    · {txnTypeLabel(txnType)}
                  </span>
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Footer actions ────────────────────────────────────────────── */}
        <SheetFooter>
          {/* Quick approve — skips the edit form */}
          {txn && !txn.is_reviewed && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleMarkReviewed}
              disabled={isSaving}
              className="gap-1.5"
            >
              <CheckCircle2 className="size-4 text-emerald-500" />
              Quick Approve
            </Button>
          )}

          {/* Save changes */}
          <Button
            size="sm"
            onClick={handleSave}
            disabled={isSaving || isLoading || !txn}
          >
            {isSaving ? "Saving…" : "Save Changes"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
