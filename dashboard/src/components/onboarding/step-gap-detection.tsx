"use client"

/**
 * Onboarding: **Gap detection** (legacy standalone step).
 *
 * The main wizard now uses {@link StepReview} instead. This file remains for any
 * out-of-band reuse and mirrors the same UX: one transaction upload + read-only gap rows.
 */

import { useQueryClient } from "@tanstack/react-query"
import { AlertCircle, CheckCircle2 } from "lucide-react"
import * as React from "react"

import { UploadButton } from "@/components/dashboard/upload-button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { holdingsCoverageKey, useHoldingsCoverage, useOnboardingGaps } from "@/hooks/use-onboarding-gaps"
import { cn } from "@/lib/utils"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"

export function StepGapDetection() {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, error, refetch } = useOnboardingGaps()
  const { refetch: refetchHoldingsCov } = useHoldingsCoverage()

  const onUploadComplete = React.useCallback(() => {
    void refetch()
    void queryClient.invalidateQueries({ queryKey: [...holdingsCoverageKey] })
    void refetchHoldingsCov()
  }, [queryClient, refetch, refetchHoldingsCov])

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Check coverage</h2>
        <p className="text-sm text-muted-foreground mt-1">
          We look for long stretches of months with <strong>no</strong> parsed activity on sources
          that are supposed to be monthly. Credit-card gaps only show if{" "}
          <strong>three or more</strong> consecutive months are empty. Use the upload below — we
          detect statement types automatically.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <UploadButton variant="transactions" onImportComplete={onUploadComplete} />
        <span className="text-xs text-muted-foreground">
          Files are analysed automatically — your statement never leaves your browser session until upload.
        </span>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Analysing your ledger…</p>}

      {isError && (
        <p className="text-sm text-destructive" role="alert">
          {getUserFacingErrorMessage(error) || "We couldn’t analyse coverage right now. Try again in a moment."}
        </p>
      )}

      {data && data.reports.length === 0 && !isLoading && (
        <Card>
          <CardContent className="pt-6 flex items-start gap-2 text-sm text-muted-foreground">
            <AlertCircle className="size-4 mt-0.5 shrink-0" />
            No source-level transaction history found yet. Finish importing from email or upload a
            statement, then return here.
          </CardContent>
        </Card>
      )}

      {data && data.reports.length > 0 && (
        <ul className="space-y-4">
          {data.reports.map((r) => (
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
                    <p className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400 text-sm">
                      <CheckCircle2 className="size-4" />
                      No month-level gaps in that window.
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
                          <p className="text-xs text-muted-foreground leading-relaxed mt-1">{g.reason}</p>
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
  )
}
