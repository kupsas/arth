/**
 * ReviewQueueBanner — amber call-to-action when bank and/or investment ledger
 * rows still have is_reviewed=false. Shown on the main dashboard and Holdings
 * so you don’t miss work on either surface.
 */

"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import { useAuthMe } from "@/hooks/use-auth";
import { useInvestmentTransactions } from "@/hooks/use-portfolio";
import { useTransactions } from "@/hooks/use-transactions";

/** Renders the bold counts + correct “need(s)” grammar for the review line. */
function ReviewQueueBannerCopy({
  transactionCount,
  investmentCount,
}: {
  transactionCount: number;
  investmentCount: number;
}) {
  const tx = transactionCount;
  const inv = investmentCount;

  if (tx > 0 && inv > 0) {
    return (
      <>
        <strong>
          {tx.toLocaleString()} {tx === 1 ? "transaction" : "transactions"}
        </strong>
        {" and "}
        <strong>
          {inv.toLocaleString()} {inv === 1 ? "investment" : "investments"}
        </strong>
        {" need review."}
      </>
    );
  }

  if (tx > 0) {
    return (
      <>
        <strong>
          {tx.toLocaleString()} {tx === 1 ? "transaction" : "transactions"}
        </strong>
        {tx === 1 ? " needs" : " need"} review.
      </>
    );
  }

  if (inv > 0) {
    return (
      <>
        <strong>
          {inv.toLocaleString()} {inv === 1 ? "investment" : "investments"}
        </strong>
        {inv === 1 ? " needs" : " need"} review.
      </>
    );
  }

  return null;
}

export function ReviewQueueBanner() {
  const { data: auth } = useAuthMe();
  const userId = auth?.username ?? null;

  const { data: unreviewedBank } = useTransactions({
    is_reviewed: false,
    page: 1,
    page_size: 1,
  });

  const { data: unreviewedInvestments } = useInvestmentTransactions(
    userId
      ? { user_id: userId, is_reviewed: false, page: 1, page_size: 1 }
      : { is_reviewed: false, page: 1, page_size: 1 },
    { enabled: userId != null },
  );

  const txTotal = unreviewedBank?.total ?? 0;
  const invTotal = userId != null ? (unreviewedInvestments?.total ?? 0) : 0;
  const pending = txTotal + invTotal;

  if (pending <= 0) {
    return null;
  }

  return (
    <Link
      href="/review"
      className="flex items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm transition-colors hover:bg-amber-500/20"
    >
      <AlertTriangle className="size-4 shrink-0 text-amber-500" />
      <span className="flex-1 text-amber-700 dark:text-amber-400">
        <ReviewQueueBannerCopy
          transactionCount={txTotal}
          investmentCount={invTotal}
        />
      </span>
      <span className="shrink-0 text-xs font-medium text-amber-600 dark:text-amber-400 underline underline-offset-2">
        Review Queue →
      </span>
    </Link>
  );
}
