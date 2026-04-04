/**
 * InvestmentReviewCard — one unreviewed investment transaction (PPF, equity, MF ledger).
 *
 * Mirrors the bank ReviewCard pattern: Approve marks ``is_reviewed`` on the server,
 * Edit opens a dialog to tweak notes then approve, Skip hides locally until refresh.
 */

"use client";

import * as React from "react";
import { CheckCircle2, ArrowRight, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import type { InvestmentTxn } from "@/lib/types";
import { cn, formatCurrency, formatDate } from "@/lib/utils";

export interface InvestmentReviewCardProps {
  transaction: InvestmentTxn;
  onApprove: (id: number) => void;
  isApproving: boolean;
  onEditApprove: (txn: InvestmentTxn, notes: string | undefined) => void;
  onSkip: (id: number) => void;
}

function isSellLike(t: string): boolean {
  return t === "SELL" || t === "SWITCH_OUT";
}

export function InvestmentReviewCard({
  transaction: txn,
  onApprove,
  isApproving,
  onEditApprove,
  onSkip,
}: InvestmentReviewCardProps) {
  const [editOpen, setEditOpen] = React.useState(false);
  const [draftNotes, setDraftNotes] = React.useState(txn.notes ?? "");

  React.useEffect(() => {
    if (editOpen) setDraftNotes(txn.notes ?? "");
  }, [editOpen, txn.notes, txn.id]);

  const id = txn.id;
  if (id == null) return null;

  const sellish = isSellLike(String(txn.txn_type));

  return (
    <>
      <Card className="flex h-full min-h-0 flex-col gap-0 overflow-hidden">
        <CardContent className="flex min-h-0 flex-1 flex-col gap-3 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <p className="truncate font-semibold text-sm leading-tight font-mono">
                {txn.symbol ?? "—"}
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {formatDate(txn.txn_date)} · {txn.account_platform}
              </p>
            </div>
            <div
              className={cn(
                "shrink-0 text-lg font-mono font-bold tabular-nums",
                sellish ? "text-rose-500" : "text-emerald-500",
              )}
            >
              {formatCurrency(txn.total_amount)}
            </div>
          </div>

          <div className="flex flex-wrap gap-1.5 text-xs">
            <Badge variant="secondary" className="font-normal">
              {txn.txn_type}
            </Badge>
            <span className="text-muted-foreground">
              {txn.quantity.toLocaleString()} @ {formatCurrency(txn.price_per_unit)}
            </span>
          </div>

          {txn.notes ? (
            <p className="text-xs text-muted-foreground bg-muted/40 rounded px-2 py-1.5 line-clamp-3 wrap-break-word">
              {txn.notes}
            </p>
          ) : null}
        </CardContent>

        <CardFooter className="flex gap-2 border-t p-3">
          <Button
            size="sm"
            variant="outline"
            className="flex-1 gap-1.5 border-emerald-500/30 text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-600 dark:text-emerald-400"
            onClick={() => onApprove(id)}
            disabled={isApproving}
          >
            <CheckCircle2 className="size-3.5" />
            {isApproving ? "…" : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="flex-1 gap-1.5"
            onClick={() => setEditOpen(true)}
          >
            <Pencil className="size-3.5" />
            Edit
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="gap-1.5 text-muted-foreground"
            onClick={() => onSkip(id)}
          >
            <ArrowRight className="size-3.5" />
            Skip
          </Button>
        </CardFooter>
      </Card>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Edit &amp; approve</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`inv-notes-${id}`} className="text-xs text-muted-foreground">
              Notes (optional)
            </Label>
            <Textarea
              id={`inv-notes-${id}`}
              value={draftNotes}
              onChange={(e) => setDraftNotes(e.target.value)}
              rows={4}
              className="text-sm"
            />
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={() => setEditOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              onClick={() => {
                const trimmed = draftNotes.trim();
                onEditApprove(txn, trimmed.length ? trimmed : undefined);
                setEditOpen(false);
              }}
            >
              Save &amp; approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
