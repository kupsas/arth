"use client"

/**
 * Step — **chunk backfill progress** (Track 2 Phase 5a).
 *
 * The *parent* wizard owns the polling / ``POST /backfill/{source}`` loop; this file
 * is only responsible for rendering numbers humans care about (emails processed,
 * transactions parsed, unknown backlog).  Keeps the UI reusable from Settings.
 */

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Button } from "@/components/ui/button"
import { humanizeSourceKey } from "@/lib/source-label"
import { cn } from "@/lib/utils"

export type BackfillProgressSnapshot = {
  source: string
  status: string
  emails_found: number
  emails_processed: number
  transactions_parsed: number
  unknowns_pending: number
  error_message: string | null
  /** ``statements`` / ``alerts`` when the server runs statement-first import (optional). */
  current_phase?: string | null
  /** When the API pauses for PDF decryption. */
  password_parser_key?: string | null
  password_failure_message_id?: string | null
  /** InstaAlert windowed import (WS4): which slice is active and overall progress. */
  current_window_label?: string | null
  windows_total?: number
  windows_completed?: number
}

/** Minimal shape for each source in the pipeline (from GET /backfill-sources). */
export type BackfillSourceInfo = {
  source_key: string
  source_type: string
}

export type StepBackfillProps = {
  /** Human-readable label (usually the pipeline ``source_key``). */
  title: string
  progress: BackfillProgressSnapshot | null
  error: string | null
  /** Full ordered list of sources — renders the source pipeline above the progress card. */
  sources?: BackfillSourceInfo[]
  /** 0-based index of the source currently being imported. */
  activeSourceIndex?: number
  /** Shown when the orchestrator reports ``paused`` — calls resume endpoint + chunk. */
  onResumeFromPause?: () => void
  resumeBusy?: boolean
  /**
   * True while ``POST /backfill/{source}`` is in flight. The server only commits progress
   * after each batch finishes, so counts can look frozen during slow Gmail work — this flag
   * tells the user the import is still running.
   */
  importBusy?: boolean
  /**
   * Coarse multi-account phase: **banking** = savings + cards; **portfolio** = broker mail.
   * Drives a friendly banner so “Section A / B” is visible without new API fields.
   */
  wizardSection?: "banking" | "portfolio" | null
}

/** Map orchestrator status strings to short, user-facing labels. */
function statusLabel(status: string | undefined): string {
  switch (status) {
    case "idle":
      return "Getting ready"
    case "processing_statements":
      return "Importing statement emails"
    case "processing_alerts":
      return "Filling gaps with alerts"
    case "processing":
      return "Working through your mail"
    case "needs_classification":
      return "Waiting for your review"
    case "needs_password":
      return "PDF password needed"
    case "paused":
      return "Paused"
    case "complete":
      return "Finished this account"
    case "error":
      return "Something went wrong"
    default:
      return status ? status.replace(/_/g, " ") : "Starting…"
  }
}

export function StepBackfill({
  title,
  progress,
  error,
  sources,
  activeSourceIndex,
  onResumeFromPause,
  resumeBusy,
  importBusy,
  wizardSection,
}: StepBackfillProps) {
  const pct =
    progress && progress.emails_found > 0
      ? Math.min(100, Math.round((100 * progress.emails_processed) / progress.emails_found))
      : 0

  const idx = activeSourceIndex ?? 0

  return (
    <div className="max-w-xl space-y-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Import from email</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Reading bank alert emails for{" "}
          <span className="font-medium text-foreground">{title}</span>. This can take a few minutes —
          we work in small batches so the screen stays responsive.
        </p>
      </div>

      {sources && sources.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {sources.map((s, i) => {
            const done = i < idx
            const active = i === idx
            return (
              <Badge
                key={s.source_key}
                variant={active ? "default" : "secondary"}
                className={cn(
                  "text-xs transition-colors",
                  done && "line-through opacity-60",
                  active && "ring-2 ring-primary/30",
                )}
              >
                {humanizeSourceKey(s.source_key)}
                {done && " ✓"}
              </Badge>
            )
          })}
        </div>
      )}

      {wizardSection && (
        <p
          className="text-xs font-medium text-muted-foreground border border-dashed rounded-md px-3 py-2 bg-muted/40"
          role="status"
        >
          {wizardSection === "banking"
            ? "Section A — Banking transactions (cash ledger)"
            : "Section B — Portfolio & broker mail (investments)"}
        </p>
      )}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{statusLabel(progress?.status)}</CardTitle>
          <CardDescription>
            {progress
              ? `${progress.emails_processed} / ${progress.emails_found} messages · ${progress.transactions_parsed} transactions parsed`
              : "Connecting to the API…"}
          </CardDescription>
          {progress?.current_phase === "listing_alerts" && (
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
              Scanning Gmail for alert-style messages (can take several minutes). Message counts will
              jump once this search finishes — your import is still running.
            </p>
          )}
          {importBusy && progress?.current_phase !== "listing_alerts" && (
            <p className="text-xs text-muted-foreground mt-2 leading-relaxed">
              Working on the current batch on the server — numbers refresh after each batch completes.
            </p>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          <Progress value={pct} className="h-2" />
          {progress && progress.unknowns_pending > 0 && (
            <p className="text-xs text-muted-foreground">
              Transactions still needing your input:{" "}
              <span className="font-medium text-foreground">{progress.unknowns_pending}</span>
            </p>
          )}
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          {progress?.status === "paused" && onResumeFromPause && (
            <Button type="button" variant="secondary" disabled={resumeBusy} onClick={onResumeFromPause}>
              {resumeBusy ? "Resuming…" : "Continue import"}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
