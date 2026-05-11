/**
 * Review page — the human-in-the-loop feedback screen.
 *
 * Two tabs:
 *   1. **Transactions** — bank rows (existing behaviour).
 *   2. **Investment transactions** — broker / PPF ledger lines from email imports
 *      that need ``is_reviewed`` before you trust them.
 *
 * See module history in git for bank-only docs; investment tab mirrors the same
 * approve / edit / skip rhythm.
 */

"use client";

import * as React from "react";
import { CheckCircle2, ClipboardCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InvestmentReviewCard } from "@/components/review/investment-review-card";
import { ReviewCard } from "@/components/review/review-card";
import { TransactionEditSheet } from "@/components/transactions/transaction-edit-sheet";
import { useAuthMe } from "@/hooks/use-auth";
import { useUpdateInvestmentTransaction } from "@/hooks/use-investment-transactions";
import { useInvestmentTransactions } from "@/hooks/use-portfolio";
import { useTransactions, useUpdateTransaction } from "@/hooks/use-transactions";
import type { InvestmentTxn, Transaction } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Tiny count next to each tab label — uses page_size=1 list calls so `total` is cheap. */
function ReviewQueueTabBadge({
  count,
  isLoading,
  unavailable,
}: {
  count: number;
  isLoading: boolean;
  /** e.g. investment queue needs sign-in — show an em dash instead of a number */
  unavailable?: boolean;
}) {
  if (unavailable) {
    return (
      <span className="text-[10px] font-medium text-muted-foreground tabular-nums" title="Sign in to load">
        —
      </span>
    );
  }
  if (isLoading) {
    return <Skeleton className="h-4 w-6 shrink-0 rounded-full" aria-hidden />;
  }
  const label = count > 99 ? "99+" : String(count);
  return (
    <span
      className={cn(
        "inline-flex min-w-5 shrink-0 items-center justify-center rounded-full border px-1.5 py-0 text-[10px] font-medium tabular-nums",
        count > 0
          ? "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-300"
          : "border-transparent bg-muted/70 text-muted-foreground",
      )}
      title={`${count} unreviewed`}
    >
      {label}
    </span>
  );
}

export default function ReviewPage() {
  const { data: auth } = useAuthMe();
  const userId = auth?.username ?? null;

  // Headline counts for tab badges (same filters as each queue, minimal page size).
  const { data: bankTabCount, isLoading: bankTabCountLoading } = useTransactions({
    is_reviewed: false,
    page: 1,
    page_size: 1,
    sort_by: "created_at",
    sort_order: "desc",
  });
  const { data: invTabCount, isLoading: invTabCountLoading } = useInvestmentTransactions(
    userId
      ? { user_id: userId, is_reviewed: false, page: 1, page_size: 1 }
      : { is_reviewed: false, page: 1, page_size: 1 },
    { enabled: userId != null },
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Review</h1>
          <p className="text-sm text-muted-foreground">
            Approve or tweak labels before they shape your Expense Trends charts and trends.
          </p>
        </div>
      </div>

      <Tabs defaultValue="transactions" className="w-full">
        <TabsList variant="line" className="mb-1 h-9 w-full min-w-0 justify-start">
          <TabsTrigger value="transactions" className="gap-2 text-xs">
            <span>Transactions</span>
            <ReviewQueueTabBadge
              count={bankTabCount?.total ?? 0}
              isLoading={bankTabCountLoading}
            />
          </TabsTrigger>
          <TabsTrigger value="investments" className="gap-2 text-xs">
            <span>Investment transactions</span>
            <ReviewQueueTabBadge
              count={invTabCount?.total ?? 0}
              isLoading={invTabCountLoading}
              unavailable={userId == null}
            />
          </TabsTrigger>
        </TabsList>

        <TabsContent value="transactions" className="mt-4">
          <BankTransactionReviewTab />
        </TabsContent>

        <TabsContent value="investments" className="mt-4">
          <InvestmentTransactionReviewTab userId={userId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function BankTransactionReviewTab() {
  const [page, setPage] = React.useState(1);
  const [skippedIds, setSkippedIds] = React.useState<Set<number>>(new Set());
  const [editTxn, setEditTxn] = React.useState<Transaction | null>(null);
  const [editSheetOpen, setEditSheetOpen] = React.useState(false);
  const [approvingIds, setApprovingIds] = React.useState<Set<number>>(new Set());
  const [sessionApproved, setSessionApproved] = React.useState(0);

  const { data, isLoading } = useTransactions({
    is_reviewed: false,
    page,
    page_size: 18,
    sort_by: "created_at",
    sort_order: "desc",
  });

  const { mutateAsync: updateTransaction } = useUpdateTransaction();

  async function handleApprove(id: number) {
    setApprovingIds((prev) => new Set(prev).add(id));
    try {
      await updateTransaction({ id, update: { is_reviewed: true } });
      setSessionApproved((n) => n + 1);
    } finally {
      setApprovingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  function handleEditApprove(txn: Transaction) {
    setEditTxn(txn);
    setEditSheetOpen(true);
  }

  function handleSkip(id: number) {
    setSkippedIds((prev) => new Set(prev).add(id));
  }

  function handleEditSheetOpenChange(open: boolean) {
    if (!open && editTxn) {
      setSessionApproved((n) => n + 1);
    }
    setEditSheetOpen(open);
    if (!open) setEditTxn(null);
  }

  const visibleCards = (data?.items ?? []).filter((txn) => !skippedIds.has(txn.id));
  const totalUnreviewed = data?.total ?? 0;
  const totalPages = data?.total_pages ?? 1;

  return (
    <>
      {sessionApproved > 0 && (
        <div className="flex justify-end">
          <div className="flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-sm font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-4" />
            {sessionApproved} approved this session
          </div>
        </div>
      )}

      {!isLoading && (
        <div className="mb-4 flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            {totalUnreviewed === 0
              ? "All caught up!"
              : `${totalUnreviewed.toLocaleString("en-IN")} unreviewed transaction${totalUnreviewed !== 1 ? "s" : ""} remaining`}
          </span>
          {skippedIds.size > 0 && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setSkippedIds(new Set())}
            >
              Restore {skippedIds.size} skipped
            </button>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="rounded-lg border flex flex-col gap-3 p-4">
              <div className="flex items-start justify-between">
                <div className="flex flex-col gap-2 flex-1">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-7 w-16 ml-3" />
              </div>
              <Skeleton className="h-8 w-full" />
              <div className="flex gap-1.5">
                <Skeleton className="h-5 w-20 rounded-full" />
                <Skeleton className="h-5 w-16 rounded-full" />
              </div>
            </div>
          ))}
        </div>
      ) : visibleCards.length === 0 && totalUnreviewed === 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 py-20 text-center">
          <div className="rounded-full bg-emerald-500/10 p-4">
            <ClipboardCheck className="size-10 text-emerald-500" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">All caught up!</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Every transaction has been reviewed. New ones will appear here as they
              arrive.
            </p>
          </div>
        </div>
      ) : visibleCards.length === 0 && totalUnreviewed > 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            You&apos;ve skipped all cards on this page.{" "}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => setSkippedIds(new Set())}
            >
              Restore skipped
            </button>{" "}
            or move to the next page.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visibleCards.map((txn) => (
            <ReviewCard
              key={txn.id}
              transaction={txn}
              onApprove={handleApprove}
              isApproving={approvingIds.has(txn.id)}
              onEditApprove={handleEditApprove}
              onSkip={handleSkip}
            />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}

      <TransactionEditSheet
        txnId={editTxn?.id ?? null}
        open={editSheetOpen}
        onOpenChange={handleEditSheetOpenChange}
        forceReviewed
      />
    </>
  );
}

function InvestmentTransactionReviewTab({ userId }: { userId: string | null }) {
  const [page, setPage] = React.useState(1);
  const [skippedIds, setSkippedIds] = React.useState<Set<number>>(new Set());
  const [approvingIds, setApprovingIds] = React.useState<Set<number>>(new Set());
  const [sessionApproved, setSessionApproved] = React.useState(0);

  const { data, isLoading } = useInvestmentTransactions(
    userId
      ? {
          user_id: userId,
          is_reviewed: false,
          page,
          page_size: 18,
        }
      : {
          is_reviewed: false,
          page,
          page_size: 18,
        },
    { enabled: userId != null },
  );

  const { mutateAsync: patchInv } = useUpdateInvestmentTransaction();

  async function handleApprove(id: number) {
    setApprovingIds((prev) => new Set(prev).add(id));
    try {
      await patchInv({ id, update: { is_reviewed: true } });
      setSessionApproved((n) => n + 1);
    } finally {
      setApprovingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  async function handleEditApprove(txn: InvestmentTxn, notes: string | undefined) {
    const id = txn.id;
    if (id == null) return;
    setApprovingIds((prev) => new Set(prev).add(id));
    try {
      await patchInv({
        id,
        update: { is_reviewed: true, notes: notes ?? null },
      });
      setSessionApproved((n) => n + 1);
    } finally {
      setApprovingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  function handleSkip(id: number) {
    setSkippedIds((prev) => new Set(prev).add(id));
  }

  if (!userId) {
    return (
      <p className="text-sm text-muted-foreground py-8">
        Sign in to review investment transactions scoped to your holdings.
      </p>
    );
  }

  const items = data?.items ?? [];
  const visibleCards = items.filter((txn) => txn.id != null && !skippedIds.has(txn.id));
  const totalUnreviewed = data?.total ?? 0;
  const totalPages = data?.total_pages ?? 1;

  return (
    <>
      {sessionApproved > 0 && (
        <div className="flex justify-end">
          <div className="flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-sm font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-4" />
            {sessionApproved} approved this session
          </div>
        </div>
      )}

      {!isLoading && (
        <div className="mb-4 flex items-center gap-3">
          <span className="text-sm text-muted-foreground">
            {totalUnreviewed === 0
              ? "All caught up!"
              : `${totalUnreviewed.toLocaleString("en-IN")} unreviewed investment transaction${totalUnreviewed !== 1 ? "s" : ""} remaining`}
          </span>
          {skippedIds.size > 0 && (
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setSkippedIds(new Set())}
            >
              Restore {skippedIds.size} skipped
            </button>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="rounded-lg border flex flex-col gap-3 p-4">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-8 w-full" />
            </div>
          ))}
        </div>
      ) : visibleCards.length === 0 && totalUnreviewed === 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 py-20 text-center">
          <div className="rounded-full bg-emerald-500/10 p-4">
            <ClipboardCheck className="size-10 text-emerald-500" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">Nothing to review</h2>
            <p className="mt-1 text-sm text-muted-foreground max-w-md">
              Lines we pulled from your email (for example PPF from annual statements)
              show up here until you&apos;ve checked them. Imports from CSV are marked
              reviewed by default.
            </p>
          </div>
        </div>
      ) : visibleCards.length === 0 && totalUnreviewed > 0 ? (
        <div className="flex flex-col items-center justify-center gap-4 py-16 text-center">
          <p className="text-sm text-muted-foreground">
            You&apos;ve skipped all cards on this page.{" "}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => setSkippedIds(new Set())}
            >
              Restore skipped
            </button>{" "}
            or move to the next page.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {visibleCards.map((txn) => (
            <InvestmentReviewCard
              key={txn.id}
              transaction={txn}
              onApprove={handleApprove}
              isApproving={txn.id != null && approvingIds.has(txn.id)}
              onEditApprove={handleEditApprove}
              onSkip={handleSkip}
            />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </>
  );
}
