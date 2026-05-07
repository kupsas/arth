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
 *
 * **Earlier imports** — One compact card below the review queue; rows come from sessionStorage
 * and are appended by the upload panel via ``onStatementSessionHistoryAppend``.
 */

import * as React from "react"

import { ClassificationBatchReview } from "@/components/onboarding/classification-batch-review"
import {
  StatementUploadPanel,
  formatStatementHistoryDate,
  loadStatementUploadHistoryFromStorage,
  newStatementUploadHistoryId,
  saveStatementUploadHistoryToStorage,
  type StatementUploadHistoryItem,
} from "@/components/dashboard/upload-button"
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

/** One tight row inside the shared “Earlier imports” card (no nested cards). */
function SessionHistoryListRow({ row }: { row: StatementUploadHistoryItem }) {
  const autoAtImport = Math.max(0, row.newCount - row.unknownsCount)
  return (
    <li className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-2.5 py-1.5 text-[11px] leading-snug sm:px-3">
      <span className="min-w-0 max-w-[min(100%,14rem)] truncate font-medium text-foreground">
        {row.fileName}
      </span>
      <span className="shrink-0 text-muted-foreground">→ {humanizeSourceKey(row.sourceKey)}</span>
      <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
        {row.newCount} new · {autoAtImport} auto · {row.unknownsCount} review
      </span>
      <span className="w-full shrink-0 text-[10px] text-muted-foreground sm:ml-2 sm:w-auto">
        {formatStatementHistoryDate(row.importedAt)}
      </span>
    </li>
  )
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

  /** Live pending count from the review card — passed into ``StatementUploadPanel`` for the summary strip. */
  const [liveStatementPendingTotal, setLiveStatementPendingTotal] = React.useState<number | undefined>(
    undefined,
  )

  const [uploadSessionHistory, setUploadSessionHistory] = React.useState<StatementUploadHistoryItem[]>([])
  const sessionHistoryHydratedRef = React.useRef(false)
  React.useEffect(() => {
    if (sessionHistoryHydratedRef.current) return
    sessionHistoryHydratedRef.current = true
    setUploadSessionHistory(loadStatementUploadHistoryFromStorage())
  }, [])

  const appendSessionHistory = React.useCallback(
    (fields: Omit<StatementUploadHistoryItem, "id" | "importedAt">) => {
      const item: StatementUploadHistoryItem = {
        ...fields,
        id: newStatementUploadHistoryId(),
        importedAt: new Date().toISOString(),
      }
      setUploadSessionHistory((prev) => {
        const next = [item, ...prev]
        saveStatementUploadHistoryToStorage(next)
        return next
      })
    },
    [],
  )

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
          <div className="pt-1">
            <p className="text-xs font-medium text-foreground">Supported today</p>
            {/*
              Two-column grid so formats scan as a list, not one long paragraph.
              Single column on very narrow screens so lines don’t feel squeezed.
            */}
            <ul
              className="mt-1.5 grid grid-cols-1 gap-x-6 gap-y-1 text-xs leading-snug text-muted-foreground sm:grid-cols-2"
              aria-label="Statement formats supported today"
            >
              {TRANSACTION_UPLOAD_TYPE_LABELS.map((label) => (
                <li key={label} className="min-w-0 break-words">
                  {label}
                </li>
              ))}
            </ul>
          </div>
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
                setLiveStatementPendingTotal(info.unknownsCount)
                setStatementReview({
                  runId: info.runId,
                  sourceKey: info.sourceKey,
                  unknownsCount: info.unknownsCount,
                })
              } else {
                onStatementReviewGateChange?.(false)
                setLiveStatementPendingTotal(undefined)
                setStatementReview(null)
              }
            }}
            statementRunLiveUnknowns={
              statementReview ? liveStatementPendingTotal : undefined
            }
            statementSessionHistory={uploadSessionHistory}
            onStatementSessionHistoryAppend={appendSessionHistory}
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
          onPendingTotalChange={setLiveStatementPendingTotal}
          onQueueCleared={() => {
            setStatementReview(null)
            setLiveStatementPendingTotal(undefined)
            onStatementReviewGateChange?.(false)
            setReviewResetNonce((n) => n + 1)
            /** Deferred until review is done so the wizard does not skip ahead while unknowns remain. */
            onImportComplete?.()
          }}
        />
      ) : null}

      {uploadSessionHistory.length > 0 ? (
        <Card
          size="sm"
          className="border-dashed border-border/80 shadow-none"
          aria-label="Earlier statement imports this session"
        >
          <CardHeader className="py-2 px-3 pb-0">
            <CardTitle className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Earlier imports this session
            </CardTitle>
          </CardHeader>
          <CardContent className="px-0 pb-1 pt-1">
            <ul className="m-0 list-none divide-y divide-border/70">
              {uploadSessionHistory.map((row) => (
                <SessionHistoryListRow key={row.id} row={row} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
