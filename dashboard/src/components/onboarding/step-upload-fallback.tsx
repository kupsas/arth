"use client"

/**
 * **Statement upload** screen shown after email import finishes (Phase 2 of "Get your data").
 *
 * Two modes, driven by whether email import produced any transactions:
 * - ``gateBlocked`` (zero transactions): the user *must* upload something to continue.
 * - Not gate-blocked: upload is optional — email import already found transactions;
 *   this is a completeness step the user can skip entirely.
 *
 * **Statement import + review** — After each file, we open an SSE stream so the user sees parse /
 * dedupe / classification progress, then (when needed) the same **Review labels** queue as email
 * import, scoped to that upload run via ``pipelineRunId``. Multiple sequential uploads are
 * supported: finish review (or skip if nothing pending), then drop another file.
 */

import * as React from "react"

import { ClassificationBatchReview } from "@/components/onboarding/classification-batch-review"
import { StatementUploadPanel } from "@/components/dashboard/upload-button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { TRANSACTION_UPLOAD_TYPE_LABELS } from "@/lib/statement-upload-type-labels"
import { humanizeSourceKey } from "@/lib/source-label"
import { cn } from "@/lib/utils"

export type StepUploadFallbackProps = {
  /**
   * When true, the user tried to continue but Arth still has **zero** transactions —
   * we bump visual emphasis so the upload path is obvious and hide the skip/continue button.
   */
  gateBlocked: boolean
  /** Called after a successful statement import (parent refetches has-data and may clear the gate). */
  onImportComplete?: () => void
  /**
   * When the user must finish **Review labels** for this upload (or the panel is waiting on review),
   * the wizard should not offer "Continue to review" ahead of that work.
   */
  onStatementReviewGateChange?: (blocked: boolean) => void
}

export function StepUploadFallback({
  gateBlocked,
  onImportComplete,
  onStatementReviewGateChange,
}: StepUploadFallbackProps) {
  /** When the server reports rows that still need human labels for this upload run, we mount the queue. */
  const [statementReview, setStatementReview] = React.useState<{
    runId: number
    sourceKey: string
    unknownsCount: number
  } | null>(null)
  /** Bumps ``unknownsTrigger`` on the review card when a new run finishes (forces refetch). */
  const [reviewRefreshKey, setReviewRefreshKey] = React.useState(0)
  /**
   * Incremented when the run-scoped review queue clears so ``StatementUploadPanel`` can leave
   * ``review_pending`` and show the drop zone again (no parallel uploads while review is open).
   */
  const [reviewResetNonce, setReviewResetNonce] = React.useState(0)

  return (
    <div className="space-y-4">
      <Card
        id="onboarding-statement-fallback"
        className={cn(
          "scroll-mt-24 border-dashed transition-[box-shadow,border-color]",
          gateBlocked && "border-amber-500/60 shadow-[0_0_0_1px_rgba(245,158,11,0.25)]",
        )}
      >
        <CardHeader className="space-y-1">
          <CardTitle className="text-lg">
            {gateBlocked
              ? "We still need at least one transaction"
              : "Upload statements for completeness"}
          </CardTitle>
          <CardDescription>
            {gateBlocked ? (
              <>
                Email import didn&apos;t add anything yet. Upload a statement you downloaded from
                net banking — we&apos;ll import it the same way as on the main dashboard.
              </>
            ) : (
              <>
                Email import found your transactions. If you also have statement files from net
                banking, you can drop them here for more complete records — this step is entirely
                optional. Transactions won&apos;t be duplicated.
              </>
            )}
          </CardDescription>
          <p className="text-xs text-muted-foreground leading-relaxed pt-1">
            <span className="font-medium text-foreground">Supported today:</span>{" "}
            {TRANSACTION_UPLOAD_TYPE_LABELS.join(" · ")}
          </p>
        </CardHeader>
        <CardContent className="pt-0 space-y-3">
          <StatementUploadPanel
            statementReviewMode
            reviewResetNonce={reviewResetNonce}
            onImportComplete={onImportComplete}
            onStatementRunReady={(info) => {
              setReviewRefreshKey((k) => k + 1)
              if (info.unknownsCount > 0) {
                onStatementReviewGateChange?.(true)
                setStatementReview({
                  runId: info.runId,
                  sourceKey: info.sourceKey,
                  unknownsCount: info.unknownsCount,
                })
              } else {
                onStatementReviewGateChange?.(false)
                setStatementReview(null)
              }
            }}
          />
        </CardContent>
      </Card>

      {statementReview && statementReview.unknownsCount > 0 ? (
        <ClassificationBatchReview
          key={statementReview.runId}
          pipelineRunId={statementReview.runId}
          sourceLabel={humanizeSourceKey(statementReview.sourceKey)}
          unknownsTrigger={reviewRefreshKey}
          importAwaitingClassification={false}
          allMailSourcesImported
          mailImportActivelyProcessing={false}
          hideClassificationRowsForImportLimbo={false}
          onQueueCleared={() => {
            setStatementReview(null)
            onStatementReviewGateChange?.(false)
            setReviewResetNonce((n) => n + 1)
            /** Deferred until review is done so the wizard does not skip ahead while unknowns remain. */
            onImportComplete?.()
          }}
        />
      ) : null}
    </div>
  )
}
