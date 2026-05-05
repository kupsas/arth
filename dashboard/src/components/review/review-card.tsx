/**
 * ReviewCard — a single card on the Review screen.
 *
 * Displays the key details of an unreviewed transaction and offers three actions:
 *   ✓ Approve        — one click, marks is_reviewed: true immediately
 *   ✏ Edit & Approve — opens the edit sheet with is_reviewed pre-ticked
 *   → Skip           — hides this card locally (no server call, reappears on refresh)
 *
 * The card is designed to be scanned quickly:
 *   - Amount is large and color-coded (green = inflow, red = outflow)
 *   - Counterparty name (or truncated raw description) at the top
 *   - Category badge if already classified
 *   - Raw description as smaller context text
 */

"use client"

import {
  CheckCircle2,
  Pencil,
  ArrowRight,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardFooter } from "@/components/ui/card"
import {
  formatCurrency,
  formatDate,
  txnTypeLabel,
  categoryColor,
  reviewConfidenceBadgeClass,
  reviewConfidenceLabel,
  cn,
} from "@/lib/utils"
import type { Transaction, SpendCategory } from "@/lib/types"

// Colour tokens for each spend category badge
const SPEND_CATEGORY_STYLES: Record<SpendCategory, string> = {
  NEED:       "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  WANT:       "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  INVESTMENT: "bg-purple-500/15 text-purple-600 dark:text-purple-400",
}

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ReviewCardProps {
  transaction: Transaction
  /** Called when user clicks "Approve" — parent handles the PATCH call. */
  onApprove: (id: number) => void
  /** Whether the approve action is currently in flight. */
  isApproving: boolean
  /** Called when user clicks "Edit & Approve" — parent opens the edit sheet. */
  onEditApprove: (txn: Transaction) => void
  /** Called when user clicks "Skip" — parent hides this card locally. */
  onSkip: (id: number) => void
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function ReviewCard({
  transaction: txn,
  onApprove,
  isApproving,
  onEditApprove,
  onSkip,
}: ReviewCardProps) {
  const isInflow = txn.direction === "INFLOW"
  const displayName = txn.counterparty ?? txn.raw_description

  return (
    // h-full: fill the grid cell so every card in a row matches the tallest;
    // CardContent flex-1: body grows so the footer stays pinned to the bottom.
    <Card className="flex h-full min-h-0 flex-col gap-0 overflow-hidden">
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4">
        {/* ── Top row: name + amount ─────────────────────────────────── */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="truncate font-semibold text-sm leading-tight">
              {displayName}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {formatDate(txn.txn_date)} · {txn.account_id}
              {txn.channel ? ` · ${txn.channel}` : ""}
            </p>
          </div>

          {/* Amount — large and prominent */}
          <div
            className={cn(
              "shrink-0 text-xl font-mono font-bold tabular-nums",
              isInflow ? "text-emerald-500" : "text-rose-500",
            )}
          >
            {isInflow ? "+" : "−"}
            {formatCurrency(txn.amount)}
          </div>
        </div>

        {/* ── Raw description (context text) ────────────────────────── */}
        {txn.counterparty && (
          <p className="text-xs text-muted-foreground bg-muted/40 rounded px-2 py-1.5 line-clamp-2 break-words">
            {txn.raw_description}
          </p>
        )}

        {/* ── Classification badges ──────────────────────────────────── */}
        <div className="flex flex-wrap gap-1.5">
          {txn.counterparty_category && (
            <Badge variant="secondary" className="font-normal text-xs">
              <span
                className={cn(
                  "size-1.5 rounded-full shrink-0",
                  categoryColor(txn.counterparty_category),
                )}
              />
              {txn.counterparty_category}
            </Badge>
          )}
          {txn.txn_type && (
            <Badge variant="outline" className="font-normal text-xs">
              {txnTypeLabel(txn.txn_type)}
            </Badge>
          )}
          {/* Spend category — only relevant for outflow transactions */}
          {!isInflow && txn.spend_category && (
            <Badge
              variant="secondary"
              className={cn(
                "font-normal text-xs",
                SPEND_CATEGORY_STYLES[txn.spend_category as SpendCategory],
              )}
            >
              {txn.spend_category}
            </Badge>
          )}
          {!txn.counterparty_category && !txn.txn_type && (
            <Badge variant="outline" className="font-normal text-xs text-muted-foreground">
              Unclassified
            </Badge>
          )}
          <Badge
            variant="outline"
            title={reviewConfidenceLabel(txn.review_confidence)}
            className={cn(
              "font-normal text-xs",
              reviewConfidenceBadgeClass(txn.review_confidence),
            )}
          >
            {txn.review_confidence ? `${txn.review_confidence} confidence` : "Confidence —"}
          </Badge>
        </div>
      </CardContent>

      {/* ── Action footer ──────────────────────────────────────────────── */}
      <CardFooter className="flex gap-2 border-t p-3">
        {/* Approve — green tick */}
        <Button
          size="sm"
          variant="outline"
          className="flex-1 gap-1.5 border-emerald-500/30 text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-600 dark:text-emerald-400 dark:hover:bg-emerald-500/10 dark:hover:text-emerald-400"
          onClick={() => onApprove(txn.id)}
          disabled={isApproving}
        >
          <CheckCircle2 className="size-3.5" />
          {isApproving ? "…" : "Approve"}
        </Button>

        {/* Edit & Approve — pencil */}
        <Button
          size="sm"
          variant="outline"
          className="flex-1 gap-1.5"
          onClick={() => onEditApprove(txn)}
        >
          <Pencil className="size-3.5" />
          Edit
        </Button>

        {/* Skip — right arrow (local only, reappears on refresh) */}
        <Button
          size="sm"
          variant="ghost"
          className="gap-1.5 text-muted-foreground"
          onClick={() => onSkip(txn.id)}
        >
          <ArrowRight className="size-3.5" />
          Skip
        </Button>
      </CardFooter>
    </Card>
  )
}
