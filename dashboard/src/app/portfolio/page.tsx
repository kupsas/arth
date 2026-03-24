/**
 * portfolio/page.tsx — asset layer home (Phase F2.5).
 *
 * Tab 1 (Overview): KPI cards, allocation charts, concentration, net-worth trend,
 * and the holdings table with refresh + expand rows.
 * Tab 2 (Transactions): investment ledger with user-scoped API calls.
 *
 * ``user_id`` for every portfolio fetch is the logged-in username from GET /api/auth/me
 * (same string stored on Holding.user_id in the database).
 */

"use client";

import { AssetAllocation } from "@/components/portfolio/asset-allocation";
import { ConcentrationCard } from "@/components/portfolio/concentration-card";
import { HoldingsTable } from "@/components/portfolio/holdings-table";
import { InvestmentTxnHistory } from "@/components/portfolio/investment-txn-history";
import { NetWorthChart } from "@/components/portfolio/net-worth-chart";
import { PortfolioSummaryCards } from "@/components/portfolio/portfolio-summary-cards";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuthMe } from "@/hooks/use-auth";

export default function PortfolioPage() {
  const { data: auth, isLoading, isError } = useAuthMe();
  const userId = auth?.username ?? null;

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (isError || !userId) {
    return (
      <div className="mx-auto max-w-lg rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-sm">
        <p className="font-medium text-destructive">Could not resolve your session user.</p>
        <p className="text-muted-foreground mt-2">
          Try logging in again. Portfolio data is keyed by username — without it we cannot safely
          load holdings.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Tracked investments, allocation, and the investment ledger — scoped to{" "}
          <span className="font-medium text-foreground">{userId}</span>.
        </p>
      </div>

      <Tabs defaultValue="overview" className="w-full">
        <TabsList variant="line" className="mb-4 h-9 w-full min-w-0 justify-start sm:w-auto">
          <TabsTrigger value="overview" className="text-sm">
            Overview
          </TabsTrigger>
          <TabsTrigger value="transactions" className="text-sm">
            Transactions
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-0 space-y-6">
          <PortfolioSummaryCards userId={userId} />

          <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <AssetAllocation userId={userId} />
            </div>
            <ConcentrationCard userId={userId} />
          </div>

          <NetWorthChart userId={userId} />
          <HoldingsTable userId={userId} />
        </TabsContent>

        <TabsContent value="transactions" className="mt-0">
          <InvestmentTxnHistory userId={userId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
