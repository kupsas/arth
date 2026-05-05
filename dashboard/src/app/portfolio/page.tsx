/**
 * portfolio/page.tsx — holdings-focused dashboard (Phase F0–F5 rebuild).
 *
 * Single scrollable layout: overview → equities → mutual funds → other assets.
 * The investment ledger tab was removed here per spec; ``InvestmentTxnHistory``
 * remains in the codebase for a future route.
 *
 * ``user_id`` for every portfolio fetch is the logged-in username from
 * GET /api/auth/me (same string stored on Holding.user_id).
 */

"use client";

import { EquitiesSection } from "@/components/portfolio/equities-section";
import { MutualFundsSection } from "@/components/portfolio/mutual-funds-section";
import { OtherAssetsSection } from "@/components/portfolio/other-assets-section";
import { TopSection } from "@/components/portfolio/top-section";
import { ReviewQueueBanner } from "@/components/review/review-queue-banner";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthMe } from "@/hooks/use-auth";

export default function PortfolioPage() {
  const { data: auth, isLoading, isError } = useAuthMe();
  const userId = auth?.username ?? null;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-32 w-full max-w-md" />
        <Skeleton className="h-[280px] w-full" />
        <div className="grid gap-6 lg:grid-cols-2">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  if (isError || !userId) {
    return (
      <div className="mx-auto max-w-lg rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-sm">
        <p className="font-medium text-destructive">Couldn&apos;t load your session. Try refreshing the page.</p>
        <p className="text-muted-foreground mt-2">
          Try logging in again. Portfolio data is keyed by username — without it we
          cannot safely load holdings.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-10 pb-16">
      <ReviewQueueBanner />

      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Holdings</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Long-horizon performance and allocation for{" "}
          <span className="font-medium text-foreground">{userId}</span>.
        </p>
      </div>

      <TopSection userId={userId} />

      <EquitiesSection userId={userId} />

      <MutualFundsSection userId={userId} />

      <OtherAssetsSection userId={userId} />
    </div>
  );
}
