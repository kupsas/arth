/**
 * portfolio-value-trend.tsx — Recharts area + gradient; data from B3 trend API.
 * Pill range selector: 3M / 6M / 12M / All (no 1M — monthly points only).
 */

"use client";

import * as React from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { RECHARTS_TOOLTIP_CARD_CLASS } from "@/components/dashboard/recharts-tooltip";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useOnboardingPortfolioPriceBackfillStatus,
  useOnboardingPriceBackfillSSE,
  usePortfolioValueTrend,
  portfolioKeys,
} from "@/hooks/use-portfolio";
import type { PortfolioValueTrendRange } from "@/lib/types";
import {
  cn,
  formatCurrency,
  formatInrChartAxis,
  formatMonthShort,
  formatPercent,
} from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

const RANGES: { value: PortfolioValueTrendRange; label: string }[] = [
  { value: "3M", label: "3M" },
  { value: "6M", label: "6M" },
  { value: "12M", label: "12M" },
  { value: "all", label: "All" },
];

export interface PortfolioValueTrendProps {
  userId: string;
}

/** Maps internal symbol codes to something readable for the overlay label. */
function humaniseSymbol(sym: string | null | undefined): string {
  if (!sym) return "";
  if (sym === "MF_NAV") return "Mutual fund history";
  // NSE symbols are upper-case tickers like "INFY", "HDFCBANK" — show as-is.
  return sym;
}

interface PriceLoadingOverlayProps {
  priceJob: {
    symbols_done: number;
    symbols_total: number;
    days_done?: number;
    days_total?: number;
    current_symbol: string | null;
    message: string | null;
    /** ISO timestamp — used with progress for a rough ETA. */
    started_at?: string | null;
  };
}

/**
 * Linear extrapolation from ``started_at``. When the API sends **day** progress
 * (NSE session walk), ETA uses ``days_done`` / ``days_total`` so it matches the
 * line above. After sessions finish, **mutual fund** work only appears in overall
 * ``symbols_*`` — then we fall back to step-based ETA for the remaining tail.
 */
function roughEtaMinutesRemaining(
  startedAtIso: string | null | undefined,
  nowMs: number,
  opts: {
    daysDone?: number | null;
    daysTotal?: number | null;
    symDone: number;
    symTotal: number;
  },
): number | null {
  if (!startedAtIso) return null;
  const t0 = Date.parse(startedAtIso);
  if (Number.isNaN(t0)) return null;
  const elapsedMs = nowMs - t0;
  if (elapsedMs <= 0) return null;

  const dTot = opts.daysTotal ?? 0;
  const dDone = opts.daysDone ?? 0;

  if (dTot > 0 && dDone > 0 && dDone < dTot) {
    const remaining = dTot - dDone;
    const etaMs = (elapsedMs / dDone) * remaining;
    const mins = etaMs / 60_000;
    return Number.isFinite(mins) ? mins : null;
  }

  const { symDone, symTotal } = opts;
  if (symTotal <= 0 || symDone <= 0 || symDone >= symTotal) return null;
  const remaining = symTotal - symDone;
  const etaMs = (elapsedMs / symDone) * remaining;
  const mins = etaMs / 60_000;
  return Number.isFinite(mins) ? mins : null;
}

function formatRoughEtaLine(minutes: number | null): string | null {
  if (minutes === null) return null;
  if (minutes < 1 / 120) return null;
  if (minutes < 1) return "~< 1 min left";
  const rounded = Math.round(minutes);
  return `~${rounded} min left`;
}

/**
 * Semi-opaque overlay while historical prices load. Prefers **day-based** progress
 * from the API when present (NSE walks one bhavcopy per session, not per ticker).
 */
function PriceLoadingOverlay({ priceJob }: PriceLoadingOverlayProps) {
  /** Refresh once per second so the rough ETA moves between SSE updates. */
  const [nowMs, setNowMs] = React.useState(() =>
    typeof Date !== "undefined" ? Date.now() : 0,
  );
  React.useEffect(() => {
    const id = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  const symTotal =
    priceJob.symbols_total > 0 ? priceJob.symbols_total : null;
  const symDone = priceJob.symbols_done;
  const pct =
    symTotal !== null ? Math.round((symDone / symTotal) * 100) : undefined;

  const rawLabel = humaniseSymbol(priceJob.current_symbol);
  const detailLabel =
    rawLabel && /^\d{4}-\d{2}-\d{2}$/.test(rawLabel)
      ? `Session ${rawLabel}`
      : rawLabel;

  const dayProgress =
    priceJob.days_total != null &&
    priceJob.days_total > 0 &&
    priceJob.days_done != null
      ? `${priceJob.days_done} of ${priceJob.days_total} trading days`
      : null;

  const etaLine = formatRoughEtaLine(
    roughEtaMinutesRemaining(priceJob.started_at, nowMs, {
      daysDone: priceJob.days_done,
      daysTotal: priceJob.days_total,
      symDone,
      symTotal: symTotal ?? 0,
    }),
  );

  return (
    <div
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 rounded-md bg-background/75 backdrop-blur-[3px] animate-in fade-in duration-300 px-8"
      aria-busy="true"
      aria-live="polite"
    >
      <Loader2
        className="h-8 w-8 animate-spin text-primary opacity-90"
        aria-hidden
      />

      <div className="flex flex-col items-center gap-1 w-full max-w-[260px]">
        <p className="text-sm font-medium text-foreground tracking-tight">
          Portfolio trend loading
        </p>

        <Progress value={pct} className="mt-1 h-1.5 w-full" />

        {symTotal !== null && (
          <p className="text-xs text-muted-foreground tabular-nums text-center">
            {dayProgress ?? (
              <>
                {symDone} of {symTotal} {symTotal === 1 ? "step" : "steps"}
              </>
            )}
            {pct !== undefined && (
              <span className="ml-1 text-foreground/60">({pct}%)</span>
            )}
          </p>
        )}

        {etaLine && (
          <p className="text-[11px] text-muted-foreground/85 tabular-nums text-center">
            {etaLine}
          </p>
        )}

        {detailLabel && (
          <p className="text-[11px] text-muted-foreground/70 truncate max-w-[220px]">
            {detailLabel}
          </p>
        )}
      </div>
    </div>
  );
}

export function PortfolioValueTrend({ userId }: PortfolioValueTrendProps) {
  const gradId = React.useId().replace(/:/g, "");
  const queryClient = useQueryClient();
  const [range, setRange] = React.useState<PortfolioValueTrendRange>("12M");
  const { data, isLoading } = usePortfolioValueTrend(range, {
    user_id: userId,
  });

  // Polling hook — provides the initial/seed status (fires on mount, fast).
  const { data: priceJobPolled } = useOnboardingPortfolioPriceBackfillStatus(
    Boolean(userId),
  );

  // SSE hook — real-time updates while the job is running (≤ 0.5 s lag).
  const priceJobSSE = useOnboardingPriceBackfillSSE(Boolean(userId));

  // Prefer SSE data when available; fall back to polling snapshot.
  const priceJob = priceJobSSE ?? priceJobPolled;

  const lastPriceJobFinishedRef = React.useRef<string | null>(null);

  /** When prices finish loading, refresh the chart series (all ranges). */
  React.useEffect(() => {
    if (priceJob?.status !== "complete" || !priceJob.finished_at) return;
    if (lastPriceJobFinishedRef.current === priceJob.finished_at) return;
    lastPriceJobFinishedRef.current = priceJob.finished_at;
    void queryClient.invalidateQueries({
      queryKey: [...portfolioKeys.all, "value-trend"],
    });
  }, [priceJob?.status, priceJob?.finished_at, queryClient]);

  const chartData = React.useMemo(
    () =>
      (data?.points ?? []).map((p) => ({
        ...p,
        month: formatMonthShort(p.date),
      })),
    [data?.points],
  );

  /** "MUTUAL_FUND" → "Mutual fund" — matches asset-allocation donut labels. */
  const labelPretty = React.useCallback((s: string) => {
    return s
      .split(/[_\s]+/)
      .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
      .join(" ");
  }, []);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between space-y-0">
        <div>
          <CardTitle className="text-sm font-medium">
            Portfolio value trend
          </CardTitle>
          <p className="text-xs text-muted-foreground mt-1">
            Monthly total (holdings only). Hover a point for % change vs prior
            month.
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {RANGES.map((r) => (
            <Button
              key={r.value}
              type="button"
              size="sm"
              variant={range === r.value ? "default" : "outline"}
              className="rounded-full h-7 px-2.5 text-xs"
              onClick={() => setRange(r.value)}
            >
              {r.label}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {priceJob?.status === "error" && priceJob.error && (
          <p className="text-xs text-muted-foreground mb-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2">
            Couldn&apos;t load full chart history this round. Use Refresh prices on the
            portfolio page later, or open this page again.
          </p>
        )}
        <div className="relative min-h-[280px]">
          {isLoading ? (
            <Skeleton className="h-[280px] w-full" />
          ) : chartData.length === 0 ? (
            <p className="text-sm text-muted-foreground py-12 text-center">
              No history points yet — add holdings or widen the range.
            </p>
          ) : (
            <div className="h-[280px] w-full min-w-0">
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart
                  data={chartData}
                  margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient
                      id={`pvFill-${gradId}`}
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="var(--chart-1)"
                        stopOpacity={0.35}
                      />
                      <stop
                        offset="100%"
                        stopColor="var(--chart-1)"
                        stopOpacity={0}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                  <XAxis
                    dataKey="month"
                    tick={{ fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <YAxis
                    tickFormatter={formatInrChartAxis}
                    width={48}
                    tick={{ fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.[0]) return null;
                      const row = payload[0].payload as {
                        month: string;
                        total_portfolio_value: number;
                        pct_change_vs_prior_month: number | null;
                        by_asset_class?: Record<string, number>;
                      };
                      const ch = row.pct_change_vs_prior_month;
                      const breakdown = Object.entries(row.by_asset_class ?? {})
                        .filter(([, amt]) => amt > 0)
                        .sort((a, b) => b[1] - a[1]);
                      return (
                        <div
                          className={cn(
                            RECHARTS_TOOLTIP_CARD_CLASS,
                            "max-w-[240px]",
                          )}
                        >
                          <p className="font-medium leading-tight">{row.month}</p>
                          <p className="mt-1.5 text-muted-foreground leading-tight">
                            {formatCurrency(row.total_portfolio_value)}
                          </p>
                          {breakdown.length > 0 && (
                            <ul className="mt-2 space-y-1 border-t border-border pt-2">
                              {breakdown.map(([ac, amt]) => (
                                <li
                                  key={ac}
                                  className="flex justify-between gap-3 text-muted-foreground"
                                >
                                  <span className="truncate" title={ac}>
                                    {labelPretty(ac)}
                                  </span>
                                  <span className="font-mono text-foreground shrink-0">
                                    {formatCurrency(amt)}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                          {ch != null ? (
                            <p
                              className={cn(
                                breakdown.length > 0 && "mt-2",
                                ch >= 0
                                  ? "text-emerald-600 dark:text-emerald-400"
                                  : "text-red-600 dark:text-red-400",
                              )}
                            >
                              {ch >= 0 ? "+" : ""}
                              {formatPercent(ch, 2)} vs prior month
                            </p>
                          ) : (
                            <p
                              className={cn(
                                breakdown.length > 0 && "mt-2",
                                "text-muted-foreground",
                              )}
                            >
                              First month
                            </p>
                          )}
                        </div>
                      );
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="total_portfolio_value"
                    stroke="var(--chart-1)"
                    strokeWidth={2}
                    fill={`url(#pvFill-${gradId})`}
                    dot={false}
                    activeDot={{ r: 4 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
          {priceJob?.status === "running" && (
            <PriceLoadingOverlay priceJob={priceJob} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
