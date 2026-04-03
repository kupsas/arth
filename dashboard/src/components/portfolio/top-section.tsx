/**
 * top-section.tsx — composes headline, trend chart, allocation donut, summary
 * table (F1) for the rebuilt holdings page.
 */

"use client";

import { PortfolioValueHeadline } from "@/components/portfolio/portfolio-value-headline";
import { PortfolioValueTrend } from "@/components/portfolio/portfolio-value-trend";
import { AssetAllocationDonut } from "@/components/portfolio/asset-allocation-donut";
import { SummaryTable } from "@/components/portfolio/summary-table";

export interface TopSectionProps {
  userId: string;
}

export function TopSection({ userId }: TopSectionProps) {
  return (
    <section className="space-y-6" aria-label="Portfolio overview">
      <PortfolioValueHeadline userId={userId} />
      <PortfolioValueTrend userId={userId} />
      <div className="grid gap-6 lg:grid-cols-2">
        <AssetAllocationDonut userId={userId} />
        <SummaryTable userId={userId} />
      </div>
    </section>
  );
}
