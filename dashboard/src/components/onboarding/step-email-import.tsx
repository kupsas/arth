"use client"

/**
 * Step — **email import progress** (Track 2 Phase 5a).
 *
 * The parent wizard owns the **SSE** connection to ``GET /api/onboarding/backfill/{source}/stream``;
 * this file only renders counters humans care about (emails processed, transactions parsed,
 * unknown backlog). Keeps the UI reusable from Settings.
 */

import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Button } from "@/components/ui/button"
import { humanizeSourceKey } from "@/lib/source-label"
import { cn } from "@/lib/utils"
import posthog from "posthog-js"

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
  /** Transaction-alert windowed import: which slice is active and overall progress. */
  current_window_label?: string | null
  windows_total?: number
  windows_completed?: number
}

/** Minimal shape for each source in the pipeline (from GET /backfill-sources). */
export type BackfillSourceInfo = {
  source_key: string
  /** savings | credit_card | broker — matches GET /backfill-sources */
  instrument_type: string
}

export type StepEmailImportProps = {
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
   * True while the import stream is open — the Import card may show a “working…” hint. This is
   * not the same as blocking the classification queue (see wizard ``mailImportActivelyProcessing``).
   */
  importBusy?: boolean
}

/** Map orchestrator status strings to short, user-facing labels. */
function statusLabel(status: string | undefined): string {
  switch (status) {
    case "idle":
      return "Getting ready"
    case "processing_statements":
      return "Importing statement emails"
    case "processing_alerts":
      return "Importing transaction alerts"
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
      return "That didn't go as planned"
    default:
      return status ? status.replace(/_/g, " ") : "Starting…"
  }
}

/**
 * Visual cue next to the title:
 * - **Green** — server is actively pulling / parsing mail.
 * - **Yellow** — blocked on you (classification pause, manual pause, or PDF password).
 * - **Muted** — idle / finished / unknown.
 * - **Red** — hard error.
 */
function statusDotClass(status: string | undefined): string {
  if (!status) return "bg-muted-foreground/50"
  if (status === "error") return "bg-destructive"
  if (
    status === "needs_classification" ||
    status === "paused" ||
    status === "needs_password"
  ) {
    return "bg-amber-400 shadow-[0_0_0_3px_rgba(251,191,36,0.35)]"
  }
  if (
    status === "processing" ||
    status === "processing_statements" ||
    status === "processing_alerts"
  ) {
    return "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.35)]"
  }
  if (status === "idle") return "bg-muted-foreground/70"
  if (status === "complete") return "bg-muted-foreground/45"
  return "bg-muted-foreground/50"
}

function statusDotAriaLabel(status: string | undefined): string {
  if (!status) return "Status: connecting"
  if (status === "error") return "Status: error"
  if (
    status === "needs_classification" ||
    status === "paused" ||
    status === "needs_password"
  ) {
    return "Status: paused — needs your input"
  }
  if (
    status === "processing" ||
    status === "processing_statements" ||
    status === "processing_alerts"
  ) {
    return "Status: import running"
  }
  return `Status: ${status.replace(/_/g, " ")}`
}

export function StepEmailImport({
  title,
  progress,
  error,
  sources,
  activeSourceIndex,
  onResumeFromPause,
  resumeBusy,
  importBusy,
}: StepEmailImportProps) {
  /**
   * After statement emails finish, the server switches status to ``processing_alerts`` and sets
   * ``current_phase`` to ``listing_alerts`` while Gmail runs a fresh search. Progress counters
   * (``emails_found`` / ``emails_processed``) are still the *statement* totals until that search
   * returns — showing them made the bar sit at ~100% then “reset”. Treat this window like the
   * initial connect: no stale numbers, indeterminate bar (see ``Progress`` with ``value`` unset).
   */
  const isAlertListingReconnect =
    progress?.status === "processing_alerts" &&
    progress?.current_phase === "listing_alerts"

  /**
   * Prefer email counters when the queue size is known. When Gmail uses **date windows** for
   * alerts, ``windows_total`` / ``windows_completed`` can move before ``emails_found`` catches up.
   */
  const pct = (() => {
    if (!progress || isAlertListingReconnect) return null
    const wt = progress.windows_total
    const wc = progress.windows_completed ?? 0
    if (wt != null && wt > 0 && progress.emails_found === 0) {
      return Math.min(100, Math.round((100 * wc) / wt))
    }
    if (progress.emails_found > 0) {
      return Math.min(100, Math.round((100 * progress.emails_processed) / progress.emails_found))
    }
    return null
  })()

  const idx = activeSourceIndex ?? 0
  const activeKey = sources?.[idx]?.source_key
  const activeHuman = activeKey ? humanizeSourceKey(activeKey) : title
  /** Last account in the pipeline can still be the “active” index while status is already ``complete``. */
  const activeAccountFinished = progress?.status === "complete"

  return (
    <div className="w-full space-y-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Import from email</h2>
        <p className="text-sm text-muted-foreground mt-1">
          We read bank alert emails in small batches so the page stays responsive — usually a few
          minutes for a busy inbox.
        </p>
      </div>

      {sources && sources.length > 0 && (
        <div className="w-full rounded-xl border-2 border-primary/20 bg-primary/5 px-4 py-4 shadow-sm">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Import progress for this account
          </p>
          <p className="mt-1 text-lg font-semibold text-foreground">{activeHuman}</p>
          {sources.length > 1 && (
            <div className="mt-4 flex flex-wrap gap-2" aria-label="All accounts in this run">
              {sources.map((s, i) => {
                const done = i < idx || (i === idx && activeAccountFinished)
                const active = i === idx && !activeAccountFinished
                const upcoming = i > idx

                return (
                  <span
                    key={s.source_key}
                    aria-current={active ? "step" : undefined}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
                      /* Currently importing: high-contrast pill + attention dot (respect reduced motion). */
                      active &&
                        "border-neutral-200 bg-white text-neutral-950 shadow-md ring-2 ring-amber-400/40 dark:border-neutral-200",
                      done && "border-border bg-muted/60 text-muted-foreground line-through",
                      upcoming && "border-border/80 bg-background/40 text-muted-foreground opacity-55",
                    )}
                  >
                    <span
                      className={cn(
                        "size-2 shrink-0 rounded-full",
                        done && "bg-emerald-500",
                        upcoming && "bg-muted-foreground/45",
                        active &&
                          "bg-amber-400 shadow-[0_0_0_2px_rgba(251,191,36,0.45)] motion-safe:animate-pulse",
                      )}
                      aria-hidden
                    />
                    {humanizeSourceKey(s.source_key)}
                  </span>
                )
              })}
            </div>
          )}
        </div>
      )}

      <Card className="w-full overflow-hidden">
        <CardHeader className="space-y-3 pb-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-1 items-start gap-2.5">
              <span
                className={cn(
                  "mt-1.5 size-2.5 shrink-0 rounded-full",
                  statusDotClass(progress?.status),
                )}
                aria-hidden
              />
              <div className="min-w-0">
                <p className="sr-only">{statusDotAriaLabel(progress?.status)}</p>
                <h3 className="text-base font-semibold leading-snug">
                  {statusLabel(progress?.status)}
                </h3>
                {progress && !isAlertListingReconnect && progress.emails_found > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {progress.emails_processed.toLocaleString("en-IN")} /{" "}
                    {progress.emails_found.toLocaleString("en-IN")} messages ·{" "}
                    {progress.transactions_parsed.toLocaleString("en-IN")} transactions parsed
                  </p>
                )}
                {progress &&
                  !isAlertListingReconnect &&
                  progress.emails_found === 0 &&
                  (progress.windows_total ?? 0) > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Still pulling message lists from Gmail — numbers appear after each batch.{" "}
                      {progress.transactions_parsed > 0 && (
                        <>
                          {progress.transactions_parsed.toLocaleString("en-IN")} transactions imported so far.
                        </>
                      )}
                    </p>
                  )}
                {(!progress || isAlertListingReconnect) && (
                  <p className="mt-1 text-xs text-muted-foreground">Connecting…</p>
                )}
                {progress &&
                  !isAlertListingReconnect &&
                  progress.emails_found === 0 &&
                  (progress.windows_total ?? 0) === 0 &&
                  progress.status === "idle" && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Scanning your mail — this can take a minute…
                    </p>
                  )}
              </div>
            </div>
            <div className="shrink-0 text-right">
              <span className="text-3xl font-semibold tabular-nums tracking-tight text-foreground">
                {pct != null ? `${pct}%` : "—"}
              </span>
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Mail progress
              </p>
            </div>
          </div>

          {progress?.current_phase === "listing_alerts" && (
            <p className="text-xs text-muted-foreground leading-relaxed">
              Scanning Gmail for alert-style messages (can take several minutes). Message counts will
              jump once this search finishes — your import is still running.
            </p>
          )}
          {importBusy && progress?.current_phase !== "listing_alerts" && (
            <p className="text-xs text-muted-foreground leading-relaxed">
              Working on the current batch on the server — numbers refresh after each batch completes.
            </p>
          )}
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          <Progress
            value={isAlertListingReconnect ? undefined : (pct ?? 0)}
            className="h-2.5 w-full"
          />
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          {progress?.status === "paused" && onResumeFromPause && (
            <Button
              type="button"
              variant="secondary"
              disabled={resumeBusy}
              onClick={() => {
                posthog.capture("email_import_resumed", {
                  source: progress.source,
                  emails_processed: progress.emails_processed,
                  transactions_parsed: progress.transactions_parsed,
                });
                onResumeFromPause();
              }}
            >
              {resumeBusy ? "Resuming…" : "Continue import"}
            </Button>
          )}
        </CardContent>
      </Card>

    </div>
  )
}

/** @deprecated Use StepEmailImport */
export type StepBackfillProps = StepEmailImportProps
/** @deprecated Use StepEmailImport */
export const StepBackfill = StepEmailImport
