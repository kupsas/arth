/**
 * other-assets-section.tsx — F4: simple tables for FD, PPF, NPS, gold, SGB,
 * corporate-bond-like OTHER rows, plus savings / real estate / ESOP / misc OTHER.
 */

"use client";

import * as React from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useHoldings } from "@/hooks/use-portfolio";
import {
  holdingCostBasis,
  isLikelyCorporateBond,
  prettyAssetClassLabel,
} from "@/lib/holdings-display";
import type { Holding, PortfolioAssetClass } from "@/lib/types";
import {
  cn,
  formatCurrency,
  formatDate,
  formatPercent,
  formatPpfMaturityRemaining,
} from "@/lib/utils";

export interface OtherAssetsSectionProps {
  userId: string;
}

type HoldingRow = Holding & { id: number };

function gainClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  if (v > 0) return "text-emerald-600 dark:text-emerald-400";
  if (v < 0) return "text-red-600 dark:text-red-400";
  return "text-muted-foreground";
}

/** Asset classes rendered in this block (not equity / MF). */
const OTHER_CLASSES: PortfolioAssetClass[] = [
  "FD",
  "PPF",
  "NPS",
  "GOLD",
  "SOVEREIGN_GOLD_BOND",
  "SAVINGS",
  "REAL_ESTATE",
  "ESOP",
  "OTHER",
];

/**
 * PPF-only explainer: first ledger BUY used for maturity, plus “no more deposits” illustration.
 * The rate is fetched server-side (Wikipedia API → “current interest rate” sentence) with fallback.
 */
/**
 * NPS Tier I: maturity column shows normal exit (60th birthday) when the API reads ``DOB``
 * from its environment; footnote shows an illustrative future balance (constant assumed return).
 */
function NpsHoldingFootnotes({ rows }: { rows: HoldingRow[] }) {
  return (
    <div className="border-t border-border/60 space-y-4 px-4 py-3">
      {rows.map((h) => (
        <div key={h.id} className="text-xs text-muted-foreground space-y-1.5 leading-relaxed">
          <p>
            <span className="text-foreground/80">Normal exit (Tier I) </span>
            {h.maturity_date ? (
              <>
                is projected on <strong>{formatDate(h.maturity_date)}</strong> (60th birthday).
              </>
            ) : (
              <>— set <code className="text-[11px]">DOB=YYYY-MM-DD</code> in the API{" "}
                <code className="text-[11px]">.env</code> to show this date.</>
            )}
          </p>
          <p>
            <span className="text-foreground/80">
              If the balance stays as today and grows at the assumed rate until that date, the
              illustrative value is{" "}
            </span>
            {h.nps_projected_value_at_normal_exit != null ? (
              <strong className="text-foreground tabular-nums">
                {formatCurrency(h.nps_projected_value_at_normal_exit)}
              </strong>
            ) : (
              "—"
            )}
            {h.nps_projection_annual_rate_pct != null ? (
              <>
                {" "}
                (nominal <strong>{formatPercent(h.nps_projection_annual_rate_pct, 1)}</strong>{" "}
                p.a., compounded like the PPF illustration).
              </>
            ) : null}
          </p>
          {h.nps_projection_note ? (
            <p className="text-[11px] opacity-90">{h.nps_projection_note}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function PpfHoldingFootnotes({ rows }: { rows: HoldingRow[] }) {
  return (
    <div className="border-t border-border/60 space-y-4 px-4 py-3">
      {rows.map((h) => (
        <div key={h.id} className="text-xs text-muted-foreground space-y-1.5 leading-relaxed">
          <p>
            <span className="text-foreground/80">First contribution made </span> :{" "}
            {h.ppf_first_contribution_date
              ? formatDate(h.ppf_first_contribution_date)
              : "— (no linked contribution rows yet)"}
            .
          </p>
          <p>
            <span className="text-foreground/80">
              If you add no more money, projected value at maturity{" "}
            </span>
            ({formatDate(h.maturity_date) ?? "—"}) will be:{" "}
            {h.ppf_projected_value_at_maturity != null ? (
              <strong className="text-foreground tabular-nums">
                {formatCurrency(h.ppf_projected_value_at_maturity)}
              </strong>
            ) : (
              "—"
            )}
            {h.ppf_projection_annual_rate_pct != null ? (
              <>
                {" "}
                using <strong>{formatPercent(h.ppf_projection_annual_rate_pct, 1)}</strong> p.a.,
                compounded once per year from today&apos;s balance (
                {h.current_value != null ? formatCurrency(h.current_value) : "—"}).
              </>
            ) : null}
          </p>
          {/* <p className="text-[11px] opacity-90">
            Real PPF credits interest annually with monthly balance rules, and the government can change the rate every quarter — treat
            this as a rough guide, not a bank guarantee.
          </p> */}
        </div>
      ))}
    </div>
  );
}

function CompactHoldingTable({
  title,
  description,
  rows,
  /** PPF: show "X yrs left" from API maturity; other classes: show the calendar date. */
  maturityDisplay = "date",
  /** PPF: show maturity / projection footnotes under the table. */
  ppfFootnotes = false,
  /** NPS: normal exit date + illustrative projection (API reads ``DOB``). */
  npsFootnotes = false,
}: {
  title: string;
  description?: string;
  rows: HoldingRow[];
  maturityDisplay?: "date" | "ppf_remaining";
  ppfFootnotes?: boolean;
  npsFootnotes?: boolean;
}) {
  if (rows.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
        {description ? (
          <CardDescription>{description}</CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="px-0 sm:px-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Platform</TableHead>
              <TableHead className="text-right">Invested / principal</TableHead>
              <TableHead className="text-right">Current value</TableHead>
              <TableHead className="text-right">Overall gain</TableHead>
              <TableHead className="text-right">Maturity</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((h) => {
              const basis = holdingCostBasis(h);
              return (
                <TableRow key={h.id}>
                  <TableCell className="font-medium max-w-[200px]">
                    {h.name}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {h.account_platform}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {basis != null ? formatCurrency(basis) : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {h.current_value != null
                      ? formatCurrency(h.current_value)
                      : "—"}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      gainClass(h.overall_gain),
                    )}
                  >
                    {h.overall_gain != null
                      ? `${h.overall_gain > 0 ? "+" : ""}${formatCurrency(h.overall_gain)}`
                      : "—"}
                    {h.overall_gain_pct != null ? (
                      <span className="text-muted-foreground text-xs ml-1">
                        (
                        {h.overall_gain_pct > 0 ? "+" : ""}
                        {formatPercent(h.overall_gain_pct, 1)})
                      </span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right text-sm tabular-nums">
                    {maturityDisplay === "ppf_remaining"
                      ? formatPpfMaturityRemaining(h.maturity_date)
                      : formatDate(h.maturity_date)}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        {ppfFootnotes ? <PpfHoldingFootnotes rows={rows} /> : null}
        {npsFootnotes ? <NpsHoldingFootnotes rows={rows} /> : null}
      </CardContent>
    </Card>
  );
}

const SECTION_IDS: Partial<Record<PortfolioAssetClass, string>> = {
  FD: "holdings-section-fd",
  PPF: "holdings-section-ppf",
  NPS: "holdings-section-nps",
  GOLD: "holdings-section-gold",
  SOVEREIGN_GOLD_BOND: "holdings-section-sgb",
  SAVINGS: "holdings-section-savings",
  REAL_ESTATE: "holdings-section-realestate",
  ESOP: "holdings-section-esop",
};

export function OtherAssetsSection({ userId }: OtherAssetsSectionProps) {
  const { data: holdings, isLoading } = useHoldings({
    user_id: userId,
    is_active: true,
  });

  const byClass = React.useMemo(() => {
    const map = new Map<string, HoldingRow[]>();
    for (const ac of OTHER_CLASSES) {
      map.set(ac, []);
    }
    for (const h of holdings ?? []) {
      if (!h.is_active || h.id == null) continue;
      if (h.asset_class === "EQUITY" || h.asset_class === "MUTUAL_FUND") continue;
      const row = h as HoldingRow;
      const list = map.get(h.asset_class) ?? [];
      list.push(row);
      map.set(h.asset_class, list);
    }
    return map;
  }, [holdings]);

  const corporateBonds = React.useMemo(() => {
    return (byClass.get("OTHER") ?? []).filter(isLikelyCorporateBond);
  }, [byClass]);

  const otherMisc = React.useMemo(() => {
    return (byClass.get("OTHER") ?? []).filter((h) => !isLikelyCorporateBond(h));
  }, [byClass]);

  if (isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }

  const hasAny =
    OTHER_CLASSES.some((ac) => (byClass.get(ac)?.length ?? 0) > 0) ||
    corporateBonds.length > 0;

  if (!hasAny) {
    return null;
  }

  return (
    <section
      className="scroll-mt-24 space-y-6"
      aria-label="Other assets"
    >
      <h2 className="text-lg font-semibold tracking-tight">Other assets</h2>
      <p className="text-sm text-muted-foreground">
        Fixed income, gold, and balance-sheet style positions — no grouping
        toggles here.
      </p>

      {(["FD", "PPF", "NPS", "GOLD", "SOVEREIGN_GOLD_BOND"] as const).map(
        (ac) => (
          <div key={ac} id={SECTION_IDS[ac]} className="scroll-mt-24">
            <CompactHoldingTable
              title={prettyAssetClassLabel(ac)}
              rows={byClass.get(ac) ?? []}
              maturityDisplay={ac === "PPF" ? "ppf_remaining" : "date"}
              ppfFootnotes={ac === "PPF"}
              npsFootnotes={ac === "NPS"}
            />
          </div>
        ),
      )}

      {corporateBonds.length > 0 ? (
        <div id="holdings-section-corporate-bonds" className="scroll-mt-24">
          <CompactHoldingTable
            title="Corporate bonds & NCDs"
            description="Detected from OTHER holdings by name keywords (bond, NCD, debenture, …)."
            rows={corporateBonds}
          />
        </div>
      ) : null}

      {(["SAVINGS", "REAL_ESTATE", "ESOP"] as const).map((ac) => (
        <div key={ac} id={SECTION_IDS[ac]} className="scroll-mt-24">
          <CompactHoldingTable
            title={prettyAssetClassLabel(ac)}
            rows={byClass.get(ac) ?? []}
          />
        </div>
      ))}

      {otherMisc.length > 0 ? (
        <div id="holdings-section-other" className="scroll-mt-24">
          <CompactHoldingTable
            title="Other"
            description="OTHER asset class excluding bond-like names."
            rows={otherMisc}
          />
        </div>
      ) : null}
    </section>
  );
}
