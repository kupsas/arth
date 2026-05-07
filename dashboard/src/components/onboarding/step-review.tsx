"use client"

/**
 * Onboarding **Review** step — merges the old Portfolio + Coverage panels.
 *
 * Section A: transaction totals + classification mix, a rotating insights carousel
 * (personalised metrics when we have enough history, otherwise Indian finance trivia),
 * and a compact broker portfolio card (cost basis + equity/MF split).
 *
 * Section B: gap list from ``GET /api/onboarding/gaps`` with **one** transaction upload
 * button (smart detection handles format) — no per-gap upload buttons.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, PartyPopper, Sparkles } from "lucide-react"
import * as React from "react"

import { UploadButton } from "@/components/dashboard/upload-button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { holdingsCoverageKey, useHoldingsCoverage, useOnboardingGaps } from "@/hooks/use-onboarding-gaps"
import {
  fetchCategoryBreakdown,
  fetchCategoryTrend,
  fetchClassificationStats,
  fetchMonthlyTrend,
  fetchNegativeSurplusMonths,
  fetchOnboardingPortfolioSnapshot,
  fetchSpendCategoryBreakdown,
} from "@/lib/api"
import type { CategoryBreakdown, MonthlyTrend, SpendCategoryBreakdown } from "@/lib/types"
import { cn, formatCurrency } from "@/lib/utils"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"

const TRIVIA_SLIDES: string[] = [
  "UPI users make 8× more transactions per month than traditional credit card users.",
  "Mutual fund investments in India grew 655% in 5 years — from ₹62K crore to ₹4.7 lakh crore.",
  "Indian household financial liabilities grew 102% from 2019–2025, while financial assets grew just 48%.",
  "45% of UPI credit card users in India are under 30 years old.",
  "75% of UPI transactions happen at kirana stores and local merchants — not big retailers.",
  "ELSS mutual funds offer 80C tax deductions with the shortest lock-in — just 3 years.",
  "The 50-30-20 rule: 50% on needs, 30% on wants, 20% on savings.",
  "Arth only reads emails from known bank senders — your personal emails are never accessed.",
]

/** ~last three months for category / spend breakdowns */
function trailingDaysRange(days: number): { date_from: string; date_to: string } {
  const end = new Date()
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000)
  return {
    date_from: start.toISOString().slice(0, 10),
    date_to: end.toISOString().slice(0, 10),
  }
}

function formatMonthLabel(ym: string): string {
  const [y, m] = ym.split("-").map(Number)
  if (!y || !m) return ym
  return new Date(y, m - 1, 1).toLocaleString("en-IN", { month: "short", year: "numeric" })
}

function countMonthsWithActivity(rows: MonthlyTrend[]): number {
  return rows.filter((r) => r.income > 0 || r.expense > 0).length
}

function pickHighestExpenseMonth(rows: MonthlyTrend[]): MonthlyTrend | null {
  const withExpense = rows.filter((r) => r.expense > 0)
  if (withExpense.length === 0) return null
  return withExpense.reduce((a, b) => (b.expense > a.expense ? b : a))
}

function sumCategoryTrendAmount(rows: { month: string; amount: number }[]): number {
  return rows.reduce((s, r) => s + (r.amount ?? 0), 0)
}

function spendRow(
  rows: SpendCategoryBreakdown[],
  key: SpendCategoryBreakdown["spend_category"],
): SpendCategoryBreakdown | undefined {
  return rows.find((r) => r.spend_category === key)
}

type ReviewInsightsBundle = {
  /** True when we have at least 3 months with any income/expense in the trailing window */
  usePersonalized: boolean
  personalizedSlides: string[]
  triviaSlides: string[]
}

async function fetchReviewInsights(): Promise<ReviewInsightsBundle> {
  const date3m = trailingDaysRange(95)

  const settled = await Promise.allSettled([
    fetchCategoryBreakdown(date3m, "OUTFLOW"),
    fetchClassificationStats(),
    fetchMonthlyTrend(12),
    fetchCategoryTrend("swiggy_food", 3),
    fetchCategoryTrend("transport", 3),
    fetchSpendCategoryBreakdown(date3m),
    fetchNegativeSurplusMonths(12),
  ])

  const categories: CategoryBreakdown[] =
    settled[0].status === "fulfilled" ? settled[0].value : []
  const classStats =
    settled[1].status === "fulfilled" ? settled[1].value : null
  const monthly: MonthlyTrend[] = settled[2].status === "fulfilled" ? settled[2].value : []
  const swiggy = settled[3].status === "fulfilled" ? settled[3].value : []
  const transport = settled[4].status === "fulfilled" ? settled[4].value : []
  const spendCat: SpendCategoryBreakdown[] =
    settled[5].status === "fulfilled" ? settled[5].value : []
  const neg =
    settled[6].status === "fulfilled" ? settled[6].value : { months_with_deficit: 0 }

  const monthsWithActivity = countMonthsWithActivity(monthly)
  const usePersonalized = monthsWithActivity >= 3

  const personalizedSlides: string[] = []

  const top = categories.find((c) => (c.amount ?? 0) > 0)
  if (top) {
    const label =
      top.category != null && String(top.category).trim() !== "" ? String(top.category) : "Unclassified"
    personalizedSlides.push(
      `Your top spending category in the last few months was ${label} — about ${top.percentage.toFixed(0)}% of your outflows.`,
    )
  }

  if (classStats && classStats.total_transactions > 0) {
    const auto = Math.round(classStats.rules_pct + classStats.llm_pct)
    const you = Math.round(classStats.user_confirmed_pct)
    personalizedSlides.push(
      `We auto-classified about ${auto}% of your transactions using rules and AI — you personally confirmed ${you}%.`,
    )
  }

  const hi = pickHighestExpenseMonth(monthly)
  if (hi && usePersonalized) {
    personalizedSlides.push(
      `Your highest-spending month was ${formatMonthLabel(hi.month)} at ${formatCurrency(hi.expense)}.`,
    )
  }

  const sw = sumCategoryTrendAmount(swiggy)
  const tr = sumCategoryTrendAmount(transport)
  if (usePersonalized && sw > 0 && tr > 0) {
    personalizedSlides.push(
      `You spent ${formatCurrency(sw)} on food delivery vs ${formatCurrency(tr)} on transport in the last three months.`,
    )
  }

  const unclassified = spendRow(spendCat, "UNCLASSIFIED")
  const need = spendRow(spendCat, "NEED")
  const want = spendRow(spendCat, "WANT")
  if (
    usePersonalized &&
    unclassified &&
    need &&
    want &&
    unclassified.percentage < 40
  ) {
    personalizedSlides.push(
      `About ${need.percentage.toFixed(0)}% of your spending went toward needs and ${want.percentage.toFixed(0)}% toward wants.`,
    )
  }

  if (usePersonalized && neg.months_with_deficit >= 1) {
    personalizedSlides.push(
      `You had ${neg.months_with_deficit} month${neg.months_with_deficit === 1 ? "" : "s"} in the last year where spending exceeded income.`,
    )
  }

  let triviaSlides = [...TRIVIA_SLIDES]
  // Light shuffle so repeat visits feel fresh
  triviaSlides = triviaSlides.sort(() => Math.random() - 0.5)

  return { usePersonalized, personalizedSlides, triviaSlides }
}

function buildCarouselSlides(bundle: ReviewInsightsBundle): string[] {
  const out: string[] = [...bundle.personalizedSlides]
  let i = 0
  while (out.length < 3 && i < bundle.triviaSlides.length) {
    out.push(bundle.triviaSlides[i])
    i += 1
  }
  if (out.length === 0) {
    return bundle.triviaSlides.slice(0, 5)
  }
  // Cap total slides for a snappy carousel
  const max = 8
  if (out.length > max) return out.slice(0, max)
  return out
}

/** How many insight cards to show side-by-side in the carousel */
const CAROUSEL_VISIBLE = 3
/** Gap between carousel cards in px — must match the inline gap style */
const CAROUSEL_GAP_PX = 16

export type StepReviewProps = {
  /** Discovery included a broker source — show portfolio cost-basis card */
  hasBrokerSource: boolean
}

export function StepReview({ hasBrokerSource }: StepReviewProps) {
  const queryClient = useQueryClient()
  const { data: gapsData, isLoading: gapsLoading, isError: gapsError, error: gapsErr, refetch } =
    useOnboardingGaps()
  const { refetch: refetchHoldingsCov } = useHoldingsCoverage()

  const onUploadComplete = React.useCallback(() => {
    void refetch()
    void queryClient.invalidateQueries({ queryKey: [...holdingsCoverageKey] })
    void refetchHoldingsCov()
    void queryClient.invalidateQueries({ queryKey: ["onboarding", "review-insights"] })
  }, [queryClient, refetch, refetchHoldingsCov])

  const insightsQ = useQuery({
    queryKey: ["onboarding", "review-insights"] as const,
    queryFn: fetchReviewInsights,
    staleTime: 60_000,
  })

  const slides = React.useMemo(() => {
    if (!insightsQ.data) return []
    return buildCarouselSlides(insightsQ.data)
  }, [insightsQ.data])

  // ── Sliding carousel machinery ──────────────────────────────────────────────
  // We clone the first (CAROUSEL_VISIBLE - 1) slides at the end of the track so
  // the last real slide always has neighbours to its right. When slideIdx reaches
  // slides.length we wait for the CSS transition to finish, then snap back to 0
  // without animation — the clone makes that jump invisible.
  const extendedSlides = React.useMemo(
    () =>
      slides.length >= CAROUSEL_VISIBLE
        ? [...slides, ...slides.slice(0, CAROUSEL_VISIBLE - 1)]
        : slides,
    [slides],
  )

  const [slideIdx, setSlideIdx] = React.useState(0)
  const [noTransition, setNoTransition] = React.useState(false)
  const trackRef = React.useRef<HTMLDivElement>(null)
  const [cardPx, setCardPx] = React.useState(0)

  // Measure the track width so we can compute exact pixel offsets per card
  React.useEffect(() => {
    const el = trackRef.current
    if (!el) return
    const obs = new ResizeObserver(() => {
      setCardPx(
        (el.offsetWidth - CAROUSEL_GAP_PX * (CAROUSEL_VISIBLE - 1)) / CAROUSEL_VISIBLE,
      )
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  React.useEffect(() => { setSlideIdx(0) }, [slides])

  // Auto-advance one card at a time
  React.useEffect(() => {
    if (slides.length <= 1) return
    const t = window.setInterval(() => setSlideIdx((i) => i + 1), 5000)
    return () => window.clearInterval(t)
  }, [slides.length])

  // After the clone zone transition finishes, snap back to real position silently
  React.useEffect(() => {
    if (slides.length === 0 || slideIdx < slides.length) return
    const t = setTimeout(() => {
      setNoTransition(true)
      setSlideIdx((i) => i - slides.length)
      requestAnimationFrame(() =>
        requestAnimationFrame(() => setNoTransition(false)),
      )
    }, 520)
    return () => clearTimeout(t)
  }, [slideIdx, slides.length])

  const activeDot = slides.length > 0 ? slideIdx % slides.length : 0
  // ─────────────────────────────────────────────────────────────────────────────

  const snapQ = useQuery({
    queryKey: ["onboarding", "portfolio-snapshot-review", hasBrokerSource] as const,
    queryFn: fetchOnboardingPortfolioSnapshot,
    enabled: hasBrokerSource,
    staleTime: 15_000,
    refetchInterval: (q) => {
      const n = q.state.dataUpdateCount
      const h = q.state.data?.holding_count ?? 0
      const eq = q.state.data?.equity_count ?? 0
      const mf = q.state.data?.mf_count ?? 0
      if (h > 0 || eq > 0 || mf > 0) return false
      if (n > 12) return false
      return 3000
    },
  })

  const totalTxnsFromGaps = React.useMemo(() => {
    if (!gapsData?.reports?.length) return 0
    return gapsData.reports.reduce((s, r) => s + (r.transaction_count ?? 0), 0)
  }, [gapsData])

  // Top 3 holdings sorted by cost value, descending
  const top3Holdings = React.useMemo(() => {
    if (!snapQ.data?.top_holdings?.length) return []
    return [...snapQ.data.top_holdings]
      .sort((a, b) => (b.current_value ?? 0) - (a.current_value ?? 0))
      .slice(0, 3)
  }, [snapQ.data])

  return (
    <div className="space-y-8 max-w-3xl">

      {/* ── Celebration hero ── */}
      <div className="rounded-2xl bg-gradient-to-br from-emerald-50 via-teal-50 to-cyan-50 dark:from-emerald-950/40 dark:via-teal-950/30 dark:to-cyan-950/20 border border-emerald-100 dark:border-emerald-900/50 p-6 space-y-5">
        <div className="flex items-start gap-3">
          <div className="shrink-0 size-10 rounded-full bg-emerald-100 dark:bg-emerald-900/60 flex items-center justify-center">
            <PartyPopper className="size-5 text-emerald-600 dark:text-emerald-400" />
          </div>
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Your money has a home.</h2>
            <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
              Everything came through cleanly. Here&apos;s a quick look at what Arth pulled in — plus
              any gaps worth filling before you dive in.
            </p>
          </div>
        </div>

        {/* Transactions + sources stat tiles */}
        {totalTxnsFromGaps > 0 && (
          <div className="flex flex-wrap gap-3">
            <div className="bg-white/70 dark:bg-white/5 rounded-xl px-4 py-3 border border-emerald-100 dark:border-emerald-800/60">
              <p className="text-3xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300 leading-none">
                {totalTxnsFromGaps.toLocaleString()}
              </p>
              <p className="text-xs text-muted-foreground mt-1.5">transactions imported</p>
            </div>
            {gapsData && gapsData.reports.length > 0 && (
              <div className="bg-white/70 dark:bg-white/5 rounded-xl px-4 py-3 border border-emerald-100 dark:border-emerald-800/60">
                <p className="text-3xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300 leading-none">
                  {gapsData.reports.length}
                </p>
                <p className="text-xs text-muted-foreground mt-1.5">
                  {gapsData.reports.length === 1 ? "source connected" : "sources connected"}
                </p>
              </div>
            )}
          </div>
        )}

        {/* Broker portfolio — folded into the hero */}
        {hasBrokerSource && (
          <div className="border-t border-emerald-100 dark:border-emerald-800/40 pt-5 space-y-5">
            <p className="text-xs font-semibold text-emerald-700/60 dark:text-emerald-400/60 uppercase tracking-widest">
              Broker-linked portfolio · cost basis
            </p>

            {snapQ.isLoading && (
              <p className="text-xs text-muted-foreground">Loading portfolio…</p>
            )}
            {snapQ.isError && (
              <p className="text-xs text-destructive" role="alert">
                {getUserFacingErrorMessage(snapQ.error) || "Couldn't load portfolio snapshot."}
              </p>
            )}

            {snapQ.data && (
              <>
                {/* Big number tiles: cost value, equity positions, MF count */}
                <div className="flex flex-wrap gap-4">
                  <div className="bg-white/70 dark:bg-white/5 rounded-xl px-4 py-3 border border-emerald-100 dark:border-emerald-800/60">
                    <p className="text-3xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300 leading-none">
                      {formatCurrency(snapQ.data.total_value_inr)}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1.5">cost value</p>
                  </div>
                  {snapQ.data.equity_count > 0 && (
                    <div className="bg-white/70 dark:bg-white/5 rounded-xl px-4 py-3 border border-emerald-100 dark:border-emerald-800/60">
                      <p className="text-3xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300 leading-none">
                        {snapQ.data.equity_count}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1.5">equity positions</p>
                    </div>
                  )}
                  {snapQ.data.mf_count > 0 && (
                    <div className="bg-white/70 dark:bg-white/5 rounded-xl px-4 py-3 border border-emerald-100 dark:border-emerald-800/60">
                      <p className="text-3xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300 leading-none">
                        {snapQ.data.mf_count}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1.5">mutual funds</p>
                    </div>
                  )}
                </div>

                {/* Top 3 holdings by cost value */}
                {top3Holdings.length > 0 && snapQ.data.total_value_inr > 0 && (
                  <ul className="space-y-3 mt-1">
                    {top3Holdings.map((h, i) => {
                      const pct = (100 * (h.current_value ?? 0)) / snapQ.data!.total_value_inr
                      const name = h.name || h.symbol || `Holding ${i + 1}`
                      return (
                        <li
                          key={h.symbol ?? i}
                          className="flex items-start justify-between gap-6 text-sm bg-white/50 dark:bg-white/5 rounded-lg px-4 py-3 border border-emerald-100 dark:border-emerald-800/40"
                        >
                          <span className="flex items-start gap-3 min-w-0">
                            <span className="text-xs font-bold text-emerald-600 dark:text-emerald-400 tabular-nums w-4 shrink-0 mt-px">
                              {i + 1}
                            </span>
                            <span className="font-medium text-foreground leading-snug">{name}</span>
                          </span>
                          <span className="text-xs text-muted-foreground tabular-nums shrink-0 text-right leading-snug mt-px">
                            {formatCurrency(h.current_value ?? 0)}
                            <br />
                            <span className="text-foreground font-semibold">{pct.toFixed(1)}%</span>
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}

                {/* Concentration warning if top holding is >15% */}
                {top3Holdings[0] && snapQ.data.total_value_inr > 0 &&
                  (100 * (top3Holdings[0].current_value ?? 0)) / snapQ.data.total_value_inr >= 15 && (
                    <p className="text-xs text-amber-700 dark:text-amber-400 leading-relaxed">
                      Your largest position is{" "}
                      {(
                        (100 * (top3Holdings[0].current_value ?? 0)) /
                        snapQ.data.total_value_inr
                      ).toFixed(1)}
                      % of cost value — worth keeping an eye on concentration.
                    </p>
                  )}
              </>
            )}
          </div>
        )}

        {/* Per-source pill chips */}
        {gapsData && gapsData.reports.length > 0 && (
          <ul className="flex flex-wrap gap-2.5">
            {gapsData.reports.map((r) => (
              <li
                key={r.source}
                className="text-xs bg-white/60 dark:bg-white/5 border border-emerald-100 dark:border-emerald-900/40 rounded-full px-3 py-1 text-muted-foreground"
              >
                <span className="text-foreground font-medium">{r.source_label}</span>
                {" · "}
                {r.transaction_count.toLocaleString()} txns
              </li>
            ))}
          </ul>
        )}

        {insightsQ.data?.usePersonalized && insightsQ.isSuccess && (
          <p className="text-xs text-emerald-700/70 dark:text-emerald-400/70">
            Insights below draw from roughly the last three months of classified activity.
          </p>
        )}
      </div>

      {/* ── Insights carousel (3-up sliding) ── */}
      <Card className="overflow-hidden border-violet-200 dark:border-violet-800/60 bg-gradient-to-br from-white to-violet-50/60 dark:from-card dark:to-violet-950/20">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Sparkles className="size-4 text-violet-500 dark:text-violet-400" />
            {insightsQ.data?.usePersonalized ? "Your highlights" : "Worth knowing"}
          </CardTitle>
          <CardDescription>
            {insightsQ.data?.usePersonalized
              ? "A few patterns we spotted in your numbers."
              : "Once you have a few months of data, this fills with personalised highlights. For now, here's some context worth having."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {insightsQ.isLoading && (
            <p className="text-sm text-muted-foreground">Crunching a few numbers…</p>
          )}
          {insightsQ.isError && (
            <p className="text-sm text-destructive" role="alert">
              {getUserFacingErrorMessage(insightsQ.error) || "Couldn't load highlights."}
            </p>
          )}
          {slides.length > 0 && (
            <>
              {/* Overflow-hidden track — slides as a translateX'd flex row */}
              <div ref={trackRef} className="overflow-hidden">
                <div
                  className={cn(
                    "flex",
                    !noTransition && "transition-transform duration-500 ease-in-out",
                  )}
                  style={{
                    gap: `${CAROUSEL_GAP_PX}px`,
                    transform:
                      cardPx > 0
                        ? `translateX(-${slideIdx * (cardPx + CAROUSEL_GAP_PX)}px)`
                        : undefined,
                  }}
                >
                  {extendedSlides.map((slide, i) => (
                    <div
                      key={i}
                      className="flex-none rounded-xl border-2 border-violet-200 dark:border-violet-700 bg-gradient-to-br from-violet-50 to-indigo-50/80 dark:from-violet-950/50 dark:to-indigo-950/40 p-4 flex flex-col justify-center"
                      style={{
                        width:
                          cardPx > 0
                            ? `${cardPx}px`
                            : `calc(${100 / CAROUSEL_VISIBLE}% - ${(CAROUSEL_GAP_PX * (CAROUSEL_VISIBLE - 1)) / CAROUSEL_VISIBLE}px)`,
                        minHeight: "148px",
                      }}
                    >
                      <p className="text-sm leading-relaxed text-foreground">{slide}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Dot indicators + prev / next */}
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  {slides.map((_, i) => (
                    <button
                      key={i}
                      type="button"
                      aria-label={`Go to insight ${i + 1}`}
                      className={cn(
                        "rounded-full transition-all duration-200",
                        i === activeDot
                          ? "size-2.5 bg-violet-500"
                          : "size-2 bg-violet-200 dark:bg-violet-800 hover:bg-violet-300 dark:hover:bg-violet-700",
                      )}
                      onClick={() => setSlideIdx(i)}
                    />
                  ))}
                </div>
                <div className="flex gap-1">
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    className="size-8 border-violet-200 dark:border-violet-800 hover:bg-violet-50 dark:hover:bg-violet-950/40"
                    aria-label="Previous insight"
                    onClick={() =>
                      setSlideIdx((i) => (i - 1 + slides.length) % slides.length)
                    }
                  >
                    <ChevronLeft className="size-4" />
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    className="size-8 border-violet-200 dark:border-violet-800 hover:bg-violet-50 dark:hover:bg-violet-950/40"
                    aria-label="Next insight"
                    onClick={() => setSlideIdx((i) => i + 1)}
                  >
                    <ChevronRight className="size-4" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* B — Coverage */}
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-semibold tracking-tight">Coverage gaps</h3>
          <p className="text-sm text-muted-foreground mt-1">
            We flag long stretches with no parsed activity on sources that should be monthly.
            Credit-card gaps only appear after three empty months in a row. Upload any statement —
            we detect the format automatically.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <UploadButton variant="transactions" onImportComplete={onUploadComplete} />
          <span className="text-xs text-muted-foreground">
            One upload covers every gap below — same smart detection as the main dashboard.
          </span>
        </div>

        {gapsLoading && <p className="text-sm text-muted-foreground">Analysing your ledger…</p>}

        {gapsError && (
          <p className="text-sm text-destructive" role="alert">
            {getUserFacingErrorMessage(gapsErr) || "We couldn’t analyse coverage right now. Try again in a moment."}
          </p>
        )}

        {gapsData && gapsData.reports.length === 0 && !gapsLoading && (
          <Card>
            <CardContent className="pt-6 flex items-start gap-2 text-sm text-muted-foreground">
              <AlertCircle className="size-4 mt-0.5 shrink-0 text-amber-500" />
              No source-level history found yet. Finish importing from email or upload a statement,
              then come back here.
            </CardContent>
          </Card>
        )}

        {gapsData && gapsData.reports.length > 0 && (
          <ul className="space-y-4">
            {gapsData.reports.map((r) => (
              <li key={r.source}>
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2">
                      <div>
                        <CardTitle className="text-base">{r.source_label}</CardTitle>
                      </div>
                      <p className="text-xs text-muted-foreground shrink-0">
                        {r.transaction_count} txns · {r.instrument_type} · {r.expected_cadence}
                      </p>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <p className="text-xs text-muted-foreground">
                      Range: {r.date_range_start} → {r.date_range_end}
                      {r.note && (
                        <span className="ml-1 text-amber-600 dark:text-amber-500">· {r.note}</span>
                      )}
                    </p>
                    {r.gaps.length === 0 && !r.note && (
                      <p className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 text-sm font-medium">
                        <CheckCircle2 className="size-4 shrink-0" />
                        Complete coverage — no gaps here.
                      </p>
                    )}
                    {r.gaps.length > 0 && (
                      <ul className="space-y-2 list-none pl-0">
                        {r.gaps.map((g) => (
                          <li
                            key={g.period_label + g.kind}
                            className={cn("rounded-lg border p-3", "bg-muted/30")}
                          >
                            <span className="font-medium text-sm">{g.period_label}</span>
                            <p className="text-xs text-muted-foreground leading-relaxed mt-1">
                              {g.reason}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
