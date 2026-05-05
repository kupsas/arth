"use client"

/**
 * Onboarding — **Portfolio snapshot** (broker sources only).
 *
 * After Gmail imports ICICI Direct PDFs, investment *transactions* exist first; this step
 * asks the server to (1) link any orphan ledger rows to holdings, (2) derive FIFO-style
 * equity/MF positions from that history, and (3) return a compact summary for the UI.
 *
 * We call ``POST /portfolio-derive`` once on mount, then ``GET /portfolio-snapshot`` to
 * display counts and the largest positions. The parent wizard owns the **Continue** button
 * that advances to gap detection.
 */

import * as React from "react"
import { Loader2 } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  fetchOnboardingPortfolioSnapshot,
  postOnboardingPortfolioDerive,
} from "@/lib/api"
import { formatCurrency } from "@/lib/utils"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"
import type { OnboardingPortfolioSnapshotResponse } from "@/lib/types"
import { portfolioKeys } from "@/hooks/use-portfolio"
import { useQueryClient } from "@tanstack/react-query"

export function StepPortfolioSummary() {
  const queryClient = useQueryClient()
  const [errorText, setErrorText] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(true)
  const [deriveHint, setDeriveHint] = React.useState<string | null>(null)
  const [snapshot, setSnapshot] = React.useState<OnboardingPortfolioSnapshotResponse | null>(
    null,
  )

  React.useEffect(() => {
    let cancelled = false

    async function run() {
      setBusy(true)
      setErrorText(null)
      setDeriveHint(null)
      try {
        // One round-trip that links MF/equity ledger rows and upserts derived holdings.
        const derived = await postOnboardingPortfolioDerive()
        if (cancelled) return
        const parts: string[] = []
        if (derived.derived_equity_positions || derived.derived_mf_positions) {
          parts.push(
            `Derived ${derived.derived_equity_positions} equity and ${derived.derived_mf_positions} MF positions from your ledger.`,
          )
        }
        if (derived.ingest_inserted || derived.ingest_updated) {
          parts.push(
            `Saved ${derived.ingest_inserted} new / ${derived.ingest_updated} updated holding rows.`,
          )
        }
        setDeriveHint(parts.length ? parts.join(" ") : null)

        const snap = await fetchOnboardingPortfolioSnapshot()
        if (cancelled) return
        setSnapshot(snap)
        // So the main /portfolio page shows fresh data if they open it mid-wizard.
        void queryClient.invalidateQueries({ queryKey: portfolioKeys.all })
      } catch (e) {
        if (cancelled) return
        setErrorText(
          getUserFacingErrorMessage(e) ||
            "We couldn’t build your portfolio summary. You can still continue — holdings may appear later.",
        )
        setSnapshot(null)
      } finally {
        if (!cancelled) setBusy(false)
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [queryClient])

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Your imported portfolio</h2>
        <p className="text-sm text-muted-foreground mt-1">
          We combined your broker email imports into holdings (ICICI Direct equity + mutual
          funds). This is a quick sanity check before we look for gaps in bank coverage.
        </p>
      </div>

      {busy && (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Linking trades and building positions…
        </p>
      )}

      {errorText && (
        <p className="text-sm text-destructive" role="alert">
          {errorText}
        </p>
      )}

      {deriveHint && !busy && (
        <p className="text-xs text-muted-foreground border-l-2 border-primary/30 pl-3">
          {deriveHint}
        </p>
      )}

      {snapshot && !busy && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Broker-linked holdings</CardTitle>
            <p className="text-xs font-normal text-muted-foreground">
              {snapshot.holding_count} positions · {snapshot.equity_count} equity ·{" "}
              {snapshot.mf_count} mutual funds · total{" "}
              <span className="text-foreground font-medium tabular-nums">
                {formatCurrency(snapshot.total_value_inr)}
              </span>
            </p>
            <p className="text-xs text-amber-600 dark:text-amber-400">
              Values shown are <strong>purchase price</strong> (cost basis from your transaction history), not current market value.
            </p>
          </CardHeader>
          <CardContent className="space-y-2">
            {snapshot.top_holdings.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No ICICI Direct equity or MF positions yet. If you just finished mail import,
                try again later or continue — coverage checks are next.
              </p>
            ) : (
              <ul className="divide-y rounded-md border">
                {snapshot.top_holdings.map((h) => (
                  <li
                    key={`${h.id ?? "x"}-${h.symbol ?? h.name}`}
                    className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2 text-sm"
                  >
                    <div className="min-w-0">
                      <span className="font-medium truncate block">{h.name || h.symbol || "—"}</span>
                      <span className="text-xs text-muted-foreground">
                        {[h.symbol, h.asset_class, h.account_platform].filter(Boolean).join(" · ")}
                      </span>
                    </div>
                    <span className="tabular-nums shrink-0">{formatCurrency(h.current_value)}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
