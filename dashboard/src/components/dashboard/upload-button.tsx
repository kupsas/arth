/**
 * UploadButton — drag-and-drop + click-to-browse for bank statements or portfolio files.
 *
 * The API **sniffs file content** (not the filename) to pick the right parser. You may
 * see a type picker (ambiguous format) or account picker (two cards, same format).
 *
 * `variant="holdings"` → POST /api/pipeline/upload/holdings (sync import, no run polling).
 */

"use client"

import * as React from "react"
import { CloudUpload, FileText } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { fetchPipelineRun, streamPipelineRunProgress, uploadHoldingsStatement, uploadStatement } from "@/lib/api"
import { ApiError } from "@/lib/api"
import { buildApiUrl } from "@/lib/api-base"
import { holdingsCoverageKey } from "@/hooks/use-onboarding-gaps"
import { metricsKeys } from "@/hooks/use-metrics"
import { portfolioKeys } from "@/hooks/use-portfolio"
import { humanizeSourceKey } from "@/lib/source-label"
import { cn } from "@/lib/utils"
import type { StatementUploadOption } from "@/lib/types"

/**
 * Footer layout for statement account confirmation dialogs — matches the Uber bulk-confirm row in
 * ``ClassificationBatchReview`` so onboarding and review flows feel consistent.
 */
const STATEMENT_ACCOUNT_DIALOG_FOOTER_CLASS =
  "-mx-4 -mb-4 flex flex-col-reverse gap-3 rounded-b-xl border-t border-border/50 bg-muted/25 px-6 pb-6 pt-5 sm:flex-row sm:justify-end sm:gap-4"

// ─────────────────────────────────────────────────────────────────────────────
// UI state
// ─────────────────────────────────────────────────────────────────────────────

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "polling"; runId: number; sourceKey: string }
  /** Onboarding: live SSE from ``/api/pipeline/runs/{id}/stream`` while the server classifies. */
  | {
      phase: "sse_wait"
      runId: number
      sourceKey: string
      progress: Record<string, unknown> | null
    }
  | { phase: "type_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  | { phase: "account_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  | {
      phase: "account_mismatch";
      file: File;
      serverMessage: string;
      detectedHint: string;
      existingHints: Record<string, string>;
      accountOptions: StatementUploadOption[];
      pendingSourceType: string;
    }
  | {
      phase: "confirm_account";
      file: File;
      serverMessage: string;
      existingHints: Record<string, string>;
      pendingSourceType: string;
      continueOptions: StatementUploadOption[] | null;
    }
  | { phase: "confirm_account_need_digits"; file: File; pendingSourceType: string }
  /** Encrypted PDF — ask user for password (same file is re-uploaded until unlock succeeds) */
  | {
      phase: "needs_pdf_password"
      file: File
      serverMessage: string
      passwordInvalid: boolean
    }
  | { phase: "soft_fail"; kind: "no_match" | "no_source"; message: string; contact?: boolean }
  /**
   * Onboarding statement step: import finished but some rows still need labels in the queue below.
   * No "Upload another" until the parent clears the queue and bumps ``reviewResetNonce`` (we then go idle).
   */
  | {
      phase: "review_pending"
      fileName: string
      txnCount: number
      newCount: number
      unknownsCount: number
      sourceKey: string
    }
  | { phase: "done"; fileName: string; txnCount: number; newCount: number; unknownsCount: number; sourceKey: string }
  | { phase: "holdings_done"; summary: string }
  | { phase: "holdings_type_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  | { phase: "error"; message: string }

/** One finished onboarding statement import — kept in sessionStorage so it survives wizard navigation. */
export type StatementUploadHistoryItem = {
  id: string
  fileName: string
  sourceKey: string
  txnCount: number
  newCount: number
  unknownsCount: number
  /** ISO timestamp when this row was added to history */
  importedAt: string
}

const ONBOARDING_STATEMENT_UPLOAD_HISTORY_KEY =
  "arth:onboarding:statement-upload-history-v1"

function normalizeStatementFileName(name: string): string {
  return name.trim().toLowerCase()
}

export function loadStatementUploadHistoryFromStorage(): StatementUploadHistoryItem[] {
  if (typeof window === "undefined") return []
  try {
    const raw = sessionStorage.getItem(ONBOARDING_STATEMENT_UPLOAD_HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((row): row is StatementUploadHistoryItem => {
      if (row == null || typeof row !== "object") return false
      const o = row as Record<string, unknown>
      return (
        typeof o.id === "string" &&
        typeof o.fileName === "string" &&
        typeof o.sourceKey === "string" &&
        typeof o.txnCount === "number" &&
        typeof o.newCount === "number" &&
        typeof o.unknownsCount === "number" &&
        typeof o.importedAt === "string"
      )
    })
  } catch {
    return []
  }
}

export function saveStatementUploadHistoryToStorage(items: StatementUploadHistoryItem[]) {
  try {
    sessionStorage.setItem(ONBOARDING_STATEMENT_UPLOAD_HISTORY_KEY, JSON.stringify(items))
  } catch {
    /* private mode / quota — history stays in memory for this session */
  }
}

export function newStatementUploadHistoryId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function formatStatementHistoryDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      year: "numeric",
    })
  } catch {
    return ""
  }
}

type ImportSummaryFields = {
  fileName: string
  sourceKey: string
  txnCount: number
  newCount: number
  unknownsCount: number
}

/** Shared layout for import-complete stats (live summary + history cards). */
function StatementImportSummaryBody({
  fileName,
  sourceKey,
  txnCount,
  newCount,
  unknownsCount,
  /**
   * When set (statement review while the queue drains), the green **auto-categorized** number
   * stays at this import-time snapshot. Manual fixes in Review labels are **not** auto-categorization.
   * Omit to derive from ``newCount - unknownsCount`` (done state + history cards).
   */
  autoCategorizedAtImport,
  reviewHighlight,
}: ImportSummaryFields & {
  autoCategorizedAtImport?: number
  reviewHighlight?: boolean
}) {
  const autoCategorized =
    autoCategorizedAtImport !== undefined
      ? autoCategorizedAtImport
      : Math.max(0, newCount - unknownsCount)
  return (
    <>
      <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground flex-wrap">
        <FileText className="size-3.5 shrink-0" aria-hidden />
        <span className="font-medium text-foreground truncate max-w-[min(220px,100%)]">{fileName}</span>
        <span aria-hidden>→</span>
        <span className="font-medium text-foreground">{humanizeSourceKey(sourceKey)}</span>
      </div>
      <div className="grid grid-cols-3 gap-3 sm:gap-4 w-full max-w-xs mx-auto text-center">
        <div>
          <p className="text-lg font-semibold tabular-nums">{newCount}</p>
          <p className="text-[11px] text-muted-foreground leading-tight">
            {newCount === 1 ? "transaction" : "transactions"}
            {txnCount > newCount && (
              <span className="block text-[10px]">({txnCount - newCount} dupes skipped)</span>
            )}
          </p>
        </div>
        <div>
          <p className="text-lg font-semibold tabular-nums text-green-600">{autoCategorized}</p>
          <p className="text-[11px] text-muted-foreground leading-tight">auto-categorized</p>
        </div>
        <div>
          <p
            className={cn(
              "text-lg font-semibold tabular-nums",
              reviewHighlight && unknownsCount > 0 ? "text-amber-600" : "",
            )}
          >
            {unknownsCount}
          </p>
          <p className="text-[11px] text-muted-foreground leading-tight">needs review</p>
        </div>
      </div>
    </>
  )
}

function isStatementFileNameAlreadyImported(
  fileName: string,
  history: StatementUploadHistoryItem[],
  live: UploadState,
): boolean {
  const n = normalizeStatementFileName(fileName)
  if (history.some((h) => normalizeStatementFileName(h.fileName) === n)) return true
  if (live.phase === "done" && normalizeStatementFileName(live.fileName) === n) return true
  if (live.phase === "review_pending" && normalizeStatementFileName(live.fileName) === n) {
    return true
  }
  return false
}

async function pollRunStatus(
  runId: number,
  onDone: (txnCount: number, newCount: number) => void,
  onError: (msg: string) => void,
) {
  for (let attempts = 0; attempts < 60; attempts++) {
    await new Promise((r) => setTimeout(r, 2_000))
    try {
      const res = await fetch(buildApiUrl(`/api/pipeline/runs/${runId}`), {
        credentials: "include",
      })
      if (!res.ok) break
      const data = await res.json()
      if (data.status === "completed") {
        onDone(data.txn_count ?? 0, data.new_count ?? 0)
        return
      }
      if (data.status === "failed") {
        onError(data.error_message ?? "That import didn't finish. Try again?")
        return
      }
    } catch {
      break
    }
  }
  onError("Timed out waiting for the import to finish. Check the Runs page for status.")
}

function statementSseProgressUi(progress: Record<string, unknown> | null): {
  percent: number | undefined
  caption: string
} {
  const ph = (progress?.phase as string) || ""
  if (ph === "parsing") {
    const total = Number(progress?.total_count ?? progress?.parsed_count ?? 0)
    return {
      percent: undefined,
      caption:
        total > 0
          ? `Read ${total} transaction row(s) from your statement…`
          : "Reading your statement…",
    }
  }
  if (ph === "deduping") {
    const u = Number(progress?.unique_count ?? 0)
    const t = Number(progress?.total_count ?? 0)
    return {
      percent: undefined,
      caption: t > 0 ? `Found ${t} row(s) · ${u} new after dedupe` : "Removing duplicates…",
    }
  }
  if (ph === "classifying") {
    const done = Number(progress?.classified_count ?? 0)
    const tot = Number(progress?.total_classify ?? 0)
    const pct = tot > 0 ? Math.min(100, Math.round((done / tot) * 100)) : undefined
    return {
      percent: pct,
      caption: tot > 0 ? `Classifying: ${done} / ${tot}` : "Applying rules and AI labels…",
    }
  }
  return { percent: undefined, caption: "Working on your file…" }
}

// ─────────────────────────────────────────────────────────────────────────────
// DropZone
// ─────────────────────────────────────────────────────────────────────────────

export function DropZone({
  onFile,
  disabled,
  helpText,
}: {
  onFile: (file: File) => void
  disabled: boolean
  helpText: string
}) {
  const [dragging, setDragging] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    const file = e.dataTransfer.files[0]
    if (file) onFile(file)
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors",
        dragging ? "border-primary bg-primary/5" : "border-border",
        disabled && "opacity-50 pointer-events-none",
      )}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => !disabled && inputRef.current?.click()}
      style={{ cursor: disabled ? "not-allowed" : "pointer" }}
    >
      <CloudUpload className="size-10 text-muted-foreground" />
      <div>
        <p className="text-sm font-medium">Drop your file here</p>
        <p className="text-xs text-muted-foreground mt-1">
          or click to browse · .txt · .csv · .pdf
        </p>
      </div>
      <p className="text-[11px] text-muted-foreground max-w-sm leading-relaxed">
        {helpText}
      </p>
      <input
        ref={inputRef}
        type="file"
        accept=".txt,.csv,.pdf"
        className="hidden"
        disabled={disabled}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f) }}
      />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadDialog
// ─────────────────────────────────────────────────────────────────────────────

function UploadDialog({
  onImportComplete,
  variant,
  disabled = false,
  compactProgressCopy = false,
  statementReviewMode = false,
  onStatementRunReady,
  /** Increment when the onboarding review queue is cleared so we can return the drop zone to idle. */
  reviewResetNonce = 0,
  /**
   * Live ``pending_total`` from the run-scoped ``ClassificationBatchReview`` below — keeps the
   * ``review_pending`` summary counts in sync as the user clears rows (including bulk Uber).
   */
  statementRunLiveUnknowns,
  /** Parent owns session history UI (single list below Review labels) — append-only from this dialog. */
  statementSessionHistory = [],
  onStatementSessionHistoryAppend,
}: {
  onImportComplete?: () => void
  variant: "transactions" | "holdings"
  /** When true (e.g. onboarding mail import is writing to the DB), block starting a new upload. */
  disabled?: boolean
  /** Onboarding: hide internal run ids and use shorter progress copy. */
  compactProgressCopy?: boolean
  /**
   * Onboarding statement step: after upload, follow SSE progress then surface the run id for the
   * inline review queue (parent renders ``ClassificationBatchReview``).
   */
  statementReviewMode?: boolean
  onStatementRunReady?: (info: {
    runId: number
    sourceKey: string
    txnCount: number
    newCount: number
    unknownsCount: number
  }) => void
  reviewResetNonce?: number
  statementRunLiveUnknowns?: number
  statementSessionHistory?: StatementUploadHistoryItem[]
  onStatementSessionHistoryAppend?: (
    fields: Omit<StatementUploadHistoryItem, "id" | "importedAt">,
  ) => void
}) {
  const queryClient = useQueryClient()
  const [state, setState] = React.useState<UploadState>({ phase: "idle" })
  /** Password field while user is on the needs_pdf_password step */
  const [pdfPasswordInput, setPdfPasswordInput] = React.useState("")
  /** Four digits when confirm_account flow asks for a new account number */
  const [newAccountDigits, setNewAccountDigits] = React.useState("")
  /**
   * After a PDF unlocks successfully, the browser still holds the **encrypted** File.
   * Later steps (type/account picker) re-upload that file — we must send the same password again.
   */
  const pdfSessionPasswordRef = React.useRef<string | undefined>(undefined)

  /** Always mirrors ``state`` so effects (e.g. review queue cleared) read the latest snapshot safely. */
  const stateRef = React.useRef(state)
  stateRef.current = state

  const [duplicateDialogOpen, setDuplicateDialogOpen] = React.useState(false)
  const [duplicateDialogFile, setDuplicateDialogFile] = React.useState<File | null>(null)

  /** Prevents double history rows when React Strict Mode runs effects twice with the same nonce. */
  const lastHandledReviewResetNonceRef = React.useRef(-1)

  const appendHistoryItem = React.useCallback(
    (fields: Omit<StatementUploadHistoryItem, "id" | "importedAt">) => {
      onStatementSessionHistoryAppend?.(fields)
    },
    [onStatementSessionHistoryAppend],
  )

  const helpText =
    variant === "transactions"
      ? "We detect HDFC / ICICI statement types automatically from the file contents — any filename works."
      : "We detect ICICI Direct / NSE / PPF portfolio formats from the file itself."

  function rememberWorkingPdfPassword(usedPassword?: string) {
    if (usedPassword) pdfSessionPasswordRef.current = usedPassword
  }

  function goIdle() {
    pdfSessionPasswordRef.current = undefined
    setPdfPasswordInput("")
    setNewAccountDigits("")
    setState({ phase: "idle" })
  }

  /**
   * Parent bumps ``reviewResetNonce`` after the user finishes the statement-run review queue.
   * Persist that import in history, then return the drop zone to idle (same hygiene as ``goIdle``).
   */
  React.useEffect(() => {
    if (!statementReviewMode || reviewResetNonce <= 0) return
    if (lastHandledReviewResetNonceRef.current === reviewResetNonce) return

    const snap = stateRef.current
    if (snap.phase !== "review_pending") return

    lastHandledReviewResetNonceRef.current = reviewResetNonce

    appendHistoryItem({
      fileName: snap.fileName,
      sourceKey: snap.sourceKey,
      txnCount: snap.txnCount,
      newCount: snap.newCount,
      unknownsCount: snap.unknownsCount,
    })
    pdfSessionPasswordRef.current = undefined
    setPdfPasswordInput("")
    setNewAccountDigits("")
    setState({ phase: "idle" })
  }, [reviewResetNonce, statementReviewMode, appendHistoryItem])

  async function runTxnUpload(
    file: File,
    opts?: {
      sourceKey?: string
      sourceType?: string
      pdfPassword?: string
      mismatchAction?: "new_account"
      newAccountLast4?: string
    },
  ) {
    // Prefer explicit password from the form, then any password that already worked this session.
    const mergedPw = opts?.pdfPassword ?? pdfSessionPasswordRef.current
    setState({ phase: "uploading" })
    try {
      const result = await uploadStatement(file, {
        ...(opts?.sourceKey ? { sourceKey: opts.sourceKey } : {}),
        ...(opts?.sourceType ? { sourceType: opts.sourceType } : {}),
        ...(mergedPw ? { pdfPassword: mergedPw } : {}),
        ...(opts?.mismatchAction ? { mismatchAction: opts.mismatchAction } : {}),
        ...(opts?.newAccountLast4 ? { newAccountLast4: opts.newAccountLast4 } : {}),
      })
      if (result.outcome === "success" && result.run_id != null && result.source_key) {
        const runId = result.run_id
        const srcKey = result.source_key
        rememberWorkingPdfPassword(mergedPw)
        if (statementReviewMode) {
          setState({
            phase: "sse_wait",
            runId,
            sourceKey: srcKey,
            progress: null,
          })
          try {
            const { last, endReason } = await streamPipelineRunProgress(runId, {
              onProgress: (snap) => {
                setState((prev) =>
                  prev.phase === "sse_wait" && prev.runId === runId
                    ? { ...prev, progress: snap }
                    : prev,
                )
              },
            })
            if (endReason === "error" || String(last?.phase) === "error") {
              setState({
                phase: "error",
                message: String(last?.error_message ?? "That import didn't finish. Try again?"),
              })
              return
            }
            let txnCount = Number(last?.txn_count ?? last?.total_count ?? 0)
            let newCount = Number(last?.new_count ?? 0)
            let unknownsCount = Number(last?.unknowns_count ?? 0)
            if (endReason !== "complete" || !txnCount) {
              const detail = await fetchPipelineRun(runId)
              txnCount = detail.txn_count
              newCount = detail.new_count
              unknownsCount = detail.unknowns_count ?? 0
              if (detail.status === "failed") {
                setState({
                  phase: "error",
                  message: detail.error_message ?? "That import didn't finish. Try again?",
                })
                return
              }
            }
            onStatementRunReady?.({
              runId,
              sourceKey: srcKey,
              txnCount,
              newCount,
              unknownsCount,
            })
            queryClient.invalidateQueries({ queryKey: metricsKeys.all })
            queryClient.invalidateQueries({ queryKey: ["transactions"] })
            /**
             * If anything still needs labels, the parent mounts the review card. Do **not** call
             * ``onImportComplete`` yet (wizard would treat mail as quiet and could jump ahead).
             * Parent calls ``onImportComplete`` from ``onQueueCleared`` after the queue is empty.
             */
            if (unknownsCount > 0) {
              setState({
                phase: "review_pending",
                fileName: file.name,
                txnCount,
                newCount,
                unknownsCount,
                sourceKey: srcKey,
              })
            } else {
              setState({
                phase: "done",
                fileName: file.name,
                txnCount,
                newCount,
                unknownsCount,
                sourceKey: srcKey,
              })
              onImportComplete?.()
            }
          } catch (err) {
            const msg = err instanceof ApiError ? err.message : "Couldn't follow import progress."
            setState({ phase: "polling", runId, sourceKey: srcKey })
            pollRunStatus(
              runId,
              (txnCount, newCount) => {
                void (async () => {
                  let unknownsCount = 0
                  try {
                    const detail = await fetchPipelineRun(runId)
                    unknownsCount = detail.unknowns_count ?? 0
                  } catch {
                    unknownsCount = 0
                  }
                  onStatementRunReady?.({
                    runId,
                    sourceKey: srcKey,
                    txnCount,
                    newCount,
                    unknownsCount,
                  })
                  queryClient.invalidateQueries({ queryKey: metricsKeys.all })
                  queryClient.invalidateQueries({ queryKey: ["transactions"] })
                  if (unknownsCount > 0) {
                    setState({
                      phase: "review_pending",
                      fileName: file.name,
                      txnCount,
                      newCount,
                      unknownsCount,
                      sourceKey: srcKey,
                    })
                  } else {
                    setState({
                      phase: "done",
                      fileName: file.name,
                      txnCount,
                      newCount,
                      unknownsCount,
                      sourceKey: srcKey,
                    })
                    onImportComplete?.()
                  }
                })()
              },
              (e2) => setState({ phase: "error", message: e2 || msg }),
            )
          }
          return
        }
        setState({
          phase: "polling",
          runId,
          sourceKey: srcKey,
        })
        pollRunStatus(
          runId,
          (txnCount, newCount) => {
            setState({
              phase: "done",
              fileName: file.name,
              txnCount,
              newCount,
              unknownsCount: 0,
              sourceKey: srcKey,
            })
            queryClient.invalidateQueries({ queryKey: metricsKeys.all })
            queryClient.invalidateQueries({ queryKey: ["transactions"] })
            onImportComplete?.()
          },
          (msg) => setState({ phase: "error", message: msg }),
        )
        return
      }
      if (result.outcome === "type_picker" && result.type_options?.length) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "type_pick",
          file,
          options: result.type_options,
          serverMessage: result.message,
        })
        return
      }
      if (result.outcome === "account_picker" && result.account_options?.length) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "account_pick",
          file,
          options: result.account_options,
          serverMessage: result.message,
        })
        return
      }
      if (result.outcome === "account_mismatch" && result.detected_hint && result.pending_source_type) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "account_mismatch",
          file,
          serverMessage: result.message,
          detectedHint: result.detected_hint,
          existingHints: result.existing_hints ?? {},
          accountOptions: result.account_options ?? [],
          pendingSourceType: result.pending_source_type,
        })
        return
      }
      if (result.outcome === "confirm_account" && result.pending_source_type) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "confirm_account",
          file,
          serverMessage: result.message,
          existingHints: result.existing_hints ?? {},
          pendingSourceType: result.pending_source_type,
          continueOptions: result.account_options ?? null,
        })
        return
      }
      if (result.outcome === "needs_password") {
        // Fresh prompt clears the field; wrong password keeps what they typed so they can fix it.
        if (!result.password_invalid) setPdfPasswordInput("")
        setState({
          phase: "needs_pdf_password",
          file,
          serverMessage: result.message,
          passwordInvalid: result.password_invalid ?? false,
        })
        return
      }
      if (result.outcome === "holdings_success") {
        rememberWorkingPdfPassword(mergedPw)
        const stats = result.import_stats
        const summary = stats ? JSON.stringify(stats, null, 2) : result.message
        setState({ phase: "holdings_done", summary })
        queryClient.invalidateQueries({ queryKey: portfolioKeys.all })
        queryClient.invalidateQueries({ queryKey: [...holdingsCoverageKey] })
        onImportComplete?.()
        return
      }
      if (result.outcome === "no_match") {
        setState({
          phase: "soft_fail",
          kind: "no_match",
          message: result.message,
          contact: result.contact_prompt,
        })
        return
      }
      if (result.outcome === "no_source") {
        setState({
          phase: "soft_fail",
          kind: "no_source",
          message: result.message,
        })
        return
      }
      setState({
        phase: "error",
        message: result.message || "Unexpected response from the server.",
      })
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Couldn't upload that file. Try again?"
      setState({ phase: "error", message: msg })
    }
  }

  async function runHoldingsUpload(
    file: File,
    opts?: { sourceType?: string; pdfPassword?: string },
  ) {
    const mergedPw = opts?.pdfPassword ?? pdfSessionPasswordRef.current
    setState({ phase: "uploading" })
    try {
      const result = await uploadHoldingsStatement(file, {
        ...(opts?.sourceType ? { sourceType: opts.sourceType } : {}),
        ...(mergedPw ? { pdfPassword: mergedPw } : {}),
      })
      if (result.outcome === "success") {
        rememberWorkingPdfPassword(mergedPw)
        const stats = result.import_stats
        const summary = stats
          ? JSON.stringify(stats, null, 2)
          : result.message
        setState({ phase: "holdings_done", summary })
        queryClient.invalidateQueries({ queryKey: portfolioKeys.all })
        queryClient.invalidateQueries({ queryKey: [...holdingsCoverageKey] })
        onImportComplete?.()
        return
      }
      if (result.outcome === "type_picker" && result.type_options?.length) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "holdings_type_pick",
          file,
          options: result.type_options,
          serverMessage: result.message,
        })
        return
      }
      if (result.outcome === "needs_password") {
        if (!result.password_invalid) setPdfPasswordInput("")
        setState({
          phase: "needs_pdf_password",
          file,
          serverMessage: result.message,
          passwordInvalid: result.password_invalid ?? false,
        })
        return
      }
      if (result.outcome === "no_match") {
        setState({
          phase: "soft_fail",
          kind: "no_match",
          message: result.message,
          contact: result.contact_prompt,
        })
        return
      }
      setState({ phase: "error", message: result.message || "That import didn't go through." })
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Couldn't upload that file. Try again?"
      setState({ phase: "error", message: msg })
    }
  }

  function cancelDuplicateReupload() {
    setDuplicateDialogOpen(false)
    setDuplicateDialogFile(null)
  }

  function confirmDuplicateReupload() {
    const f = duplicateDialogFile
    setDuplicateDialogOpen(false)
    setDuplicateDialogFile(null)
    if (!f) return
    pdfSessionPasswordRef.current = undefined
    setPdfPasswordInput("")
    setNewAccountDigits("")
    void runTxnUpload(f)
  }

  /**
   * After a clean import with nothing left to label, move the summary into history and show
   * the drop zone again so the user can add another file.
   */
  function finishDoneAndShowDropZoneAgain() {
    if (state.phase !== "done" || !statementReviewMode) return
    appendHistoryItem({
      fileName: state.fileName,
      sourceKey: state.sourceKey,
      txnCount: state.txnCount,
      newCount: state.newCount,
      unknownsCount: state.unknownsCount,
    })
    goIdle()
  }

  function handleFile(file: File) {
    if (disabled) return
    pdfSessionPasswordRef.current = undefined
    setPdfPasswordInput("")
    setNewAccountDigits("")
    if (variant === "transactions") {
      if (
        statementReviewMode &&
        isStatementFileNameAlreadyImported(file.name, statementSessionHistory, stateRef.current)
      ) {
        setDuplicateDialogFile(file)
        setDuplicateDialogOpen(true)
        return
      }
      void runTxnUpload(file)
      return
    }
    void runHoldingsUpload(file)
  }

  function submitPdfPassword() {
    if (state.phase !== "needs_pdf_password") return
    const pwd = pdfPasswordInput
    if (variant === "transactions") void runTxnUpload(state.file, { pdfPassword: pwd })
    else void runHoldingsUpload(state.file, { pdfPassword: pwd })
  }

  return (
    <>
      <div className="space-y-4">
      {state.phase === "idle" && (
        <DropZone onFile={handleFile} disabled={disabled} helpText={helpText} />
      )}

      {state.phase === "uploading" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm text-muted-foreground">
            {variant === "transactions" ? "Uploading and analysing…" : "Uploading portfolio file…"}
          </p>
        </div>
      )}

      {state.phase === "sse_wait" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          {(() => {
            const ui = statementSseProgressUi(state.progress)
            return (
              <>
                <Progress value={ui.percent} className="w-full" />
                <p className="text-sm font-medium">{ui.caption}</p>
              </>
            )
          })()}
          <p className="text-xs text-muted-foreground">
            {compactProgressCopy
              ? "Hang tight — large statements can take a minute."
              : "Hang tight — we parse, dedupe, then classify each row."}
          </p>
          <p className="text-xs text-muted-foreground">
            Account:{" "}
            <span className="font-medium text-foreground">{humanizeSourceKey(state.sourceKey)}</span>
          </p>
        </div>
      )}

      {state.phase === "polling" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm font-medium">
            Finishing import for your{" "}
            <span className="text-foreground">{humanizeSourceKey(state.sourceKey)}</span> account…
          </p>
          <p className="text-xs text-muted-foreground">
            {compactProgressCopy
              ? "Usually about half a minute while we finish saving."
              : "Usually about half a minute while we finish sorting and saving your transactions."}
          </p>
          {!compactProgressCopy && (
            <p className="text-xs text-muted-foreground">Run #{state.runId}</p>
          )}
        </div>
      )}

      {(state.phase === "type_pick" || state.phase === "account_pick") && (
        <div className="space-y-3 py-2">
          <p className="text-sm">{state.serverMessage}</p>
          <div className="flex flex-col gap-2">
            {state.options.map((opt) => (
              <Button
                key={`${opt.source_type ?? ""}-${opt.source_key ?? ""}-${opt.label}`}
                variant="outline"
                className="justify-start text-left h-auto py-3 whitespace-normal"
                onClick={() => {
                  if (state.phase === "type_pick") {
                    void runTxnUpload(state.file, { sourceType: opt.source_type ?? undefined })
                  } else {
                    void runTxnUpload(state.file, { sourceKey: opt.source_key ?? undefined })
                  }
                }}
              >
                {opt.label}
              </Button>
            ))}
          </div>
          <Button variant="ghost" size="sm" onClick={goIdle}>
            Cancel
          </Button>
        </div>
      )}

      {state.phase === "account_mismatch" && (
        <div className="space-y-3 py-2">
          <p className="text-sm">{state.serverMessage}</p>
          <div className="flex flex-col gap-2">
            {(
              state.accountOptions.length > 0
                ? state.accountOptions
                : Object.entries(state.existingHints).map(([source_key, tail]) => ({
                    source_key,
                    label: `No — same account, use the one ending ${tail}`,
                  }))
            ).map((opt) => (
              <Button
                key={`mismatch-same-${opt.source_key ?? ""}`}
                variant="outline"
                className="justify-start text-left h-auto py-3 whitespace-normal"
                onClick={() => {
                  void runTxnUpload(state.file, { sourceKey: opt.source_key ?? undefined })
                }}
              >
                {opt.label}
              </Button>
            ))}
            <Button
              variant="outline"
              className="justify-start text-left h-auto py-3 whitespace-normal"
              onClick={() => {
                void runTxnUpload(state.file, {
                  mismatchAction: "new_account",
                  newAccountLast4: state.detectedHint,
                  sourceType: state.pendingSourceType,
                })
              }}
            >
              Yes — this is a new account (ending {state.detectedHint})
            </Button>
          </div>
          <Button variant="ghost" size="sm" onClick={goIdle}>
            Cancel
          </Button>
        </div>
      )}

      {state.phase === "holdings_type_pick" && (
        <div className="space-y-3 py-2">
          <p className="text-sm">{state.serverMessage}</p>
          <div className="flex flex-col gap-2">
            {state.options.map((opt) => (
              <Button
                key={`${opt.source_type}-${opt.label}`}
                variant="outline"
                className="justify-start text-left h-auto py-3 whitespace-normal"
                onClick={() => {
                  void runHoldingsUpload(state.file, {
                    sourceType: opt.source_type ?? undefined,
                  })
                }}
              >
                {opt.label}
              </Button>
            ))}
          </div>
          <Button variant="ghost" size="sm" onClick={goIdle}>
            Cancel
          </Button>
        </div>
      )}

      {state.phase === "needs_pdf_password" && (
        <div className="space-y-4 py-2">
          <p className="text-sm text-muted-foreground">{state.serverMessage}</p>
          <div className="space-y-2">
            <Label htmlFor="upload-pdf-password">PDF password</Label>
            <Input
              id="upload-pdf-password"
              type="password"
              autoComplete="current-password"
              value={pdfPasswordInput}
              onChange={(e) => setPdfPasswordInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitPdfPassword()
              }}
              placeholder="Enter the password for this PDF"
              aria-invalid={state.passwordInvalid}
            />
            {state.passwordInvalid && (
              <p className="text-xs text-red-600">That password did not unlock this file. Try again.</p>
            )}
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={submitPdfPassword}>
              Unlock &amp; continue
            </Button>
            <Button variant="ghost" size="sm" onClick={goIdle}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {state.phase === "soft_fail" && (
        <div className="flex flex-col gap-3 py-4 text-center">
          <p className="text-sm text-muted-foreground">{state.message}</p>
          {state.contact && (
            <p className="text-xs text-muted-foreground">
              Reach out to the team — we can add support for new banks and formats.
            </p>
          )}
          <Button size="sm" variant="outline" onClick={goIdle}>
            Try another file
          </Button>
        </div>
      )}

      {state.phase === "review_pending" && (() => {
        const pendingReview =
          statementReviewMode && statementRunLiveUnknowns !== undefined
            ? statementRunLiveUnknowns
            : state.unknownsCount
        return (
        <div className="flex flex-col items-center gap-4 py-6">
          <div className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary text-2xl">
            ✓
          </div>
          <p className="text-sm font-semibold">Imported — a few labels need you</p>
          <StatementImportSummaryBody
            fileName={state.fileName}
            sourceKey={state.sourceKey}
            txnCount={state.txnCount}
            newCount={state.newCount}
            unknownsCount={pendingReview}
            autoCategorizedAtImport={Math.max(0, state.newCount - state.unknownsCount)}
            reviewHighlight
          />
          <p className="text-xs text-muted-foreground max-w-xs text-center">
            {pendingReview} {pendingReview === 1 ? "row" : "rows"} still
            {pendingReview === 1 ? " needs" : " need"} a quick look in{" "}
            <strong>Review labels</strong> below — finish that, then you can upload another file.
          </p>
        </div>
        )
      })()}

      {state.phase === "done" && (
        <div className="flex flex-col items-center gap-4 py-6">
          <div className="flex size-14 items-center justify-center rounded-full bg-green-500/10 text-green-600 text-2xl">
            ✓
          </div>
          <p className="text-sm font-semibold">Import complete</p>
          <StatementImportSummaryBody
            fileName={state.fileName}
            sourceKey={state.sourceKey}
            txnCount={state.txnCount}
            newCount={state.newCount}
            unknownsCount={state.unknownsCount}
          />
          <Button
            size="sm"
            className="mt-1"
            type="button"
            onClick={() => {
              if (statementReviewMode) finishDoneAndShowDropZoneAgain()
              else goIdle()
            }}
          >
            Upload another
          </Button>
        </div>
      )}

      {state.phase === "holdings_done" && (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <div className="flex size-14 items-center justify-center rounded-full bg-green-500/10 text-green-600 text-2xl">
            ✓
          </div>
          <p className="text-sm font-semibold">Portfolio import complete</p>
          <pre className="text-[10px] text-left bg-muted/40 rounded-md p-3 max-h-40 overflow-auto w-full whitespace-pre-wrap">
            {state.summary}
          </pre>
          <Button size="sm" className="mt-2" onClick={goIdle}>
            Upload another
          </Button>
        </div>
      )}

      {state.phase === "error" && (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <div className="flex size-14 items-center justify-center rounded-full bg-red-500/10 text-red-600 text-2xl">
            ✕
          </div>
          <p className="text-sm font-semibold text-red-600">That didn&apos;t work</p>
          <p className="text-xs text-muted-foreground max-w-xs">{state.message}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={goIdle}>
            Try again
          </Button>
        </div>
      )}
      </div>

      {/* Session import history lives in the parent (below Review labels). */}
      {/* Account disambiguation: same modal shell as Uber bulk confirm in Review labels */}
      <Dialog
        open={
          state.phase === "confirm_account" ||
          state.phase === "confirm_account_need_digits"
        }
        onOpenChange={(open) => {
          if (!open) goIdle()
        }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          {state.phase === "confirm_account" ? (
            <>
              <DialogHeader>
                <DialogTitle>Which account is this statement for?</DialogTitle>
                <DialogDescription className="text-left text-foreground/90">
                  {state.serverMessage}
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-col gap-2 sm:px-1">
                {state.continueOptions && state.continueOptions.length > 0 ? (
                  state.continueOptions.map((opt) => (
                    <Button
                      key={`confirm-cont-${opt.source_key ?? ""}`}
                      type="button"
                      variant="outline"
                      size="lg"
                      className="min-h-10 h-auto justify-start whitespace-normal px-4 py-3 text-left"
                      onClick={() => {
                        void runTxnUpload(state.file, { sourceKey: opt.source_key ?? undefined })
                      }}
                    >
                      {opt.label}
                    </Button>
                  ))
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="lg"
                    className="min-h-10 h-auto justify-start whitespace-normal px-4 py-3 text-left"
                    onClick={() => {
                      const keys = Object.keys(state.existingHints)
                      const sk = keys[0]
                      if (sk) void runTxnUpload(state.file, { sourceKey: sk })
                    }}
                  >
                    Continue with the account we already have on file
                  </Button>
                )}
                <Button
                  type="button"
                  variant="outline"
                  size="lg"
                  className="min-h-10 h-auto justify-start whitespace-normal px-4 py-3 text-left"
                  onClick={() => {
                    setNewAccountDigits("")
                    setState({
                      phase: "confirm_account_need_digits",
                      file: state.file,
                      pendingSourceType: state.pendingSourceType,
                    })
                  }}
                >
                  No — this is a different account
                </Button>
              </div>
              <DialogFooter className={STATEMENT_ACCOUNT_DIALOG_FOOTER_CLASS}>
                <Button type="button" variant="outline" size="lg" className="min-h-10 px-6" onClick={goIdle}>
                  Cancel
                </Button>
              </DialogFooter>
            </>
          ) : null}
          {state.phase === "confirm_account_need_digits" ? (
            <>
              <DialogHeader>
                <DialogTitle>Different account</DialogTitle>
                <DialogDescription className="text-left">
                  Enter the last four digits of the account this statement belongs to.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-2 sm:px-1">
                <Label htmlFor="new-acct-last4">Last four digits</Label>
                <Input
                  id="new-acct-last4"
                  inputMode="numeric"
                  maxLength={4}
                  autoComplete="off"
                  value={newAccountDigits}
                  onChange={(e) =>
                    setNewAccountDigits(e.target.value.replace(/\D/g, "").slice(0, 4))
                  }
                  placeholder="e.g. 3703"
                />
              </div>
              <DialogFooter className={STATEMENT_ACCOUNT_DIALOG_FOOTER_CLASS}>
                <Button type="button" variant="outline" size="lg" className="min-h-10 px-6" onClick={goIdle}>
                  Cancel
                </Button>
                <Button
                  type="button"
                  size="lg"
                  className="min-h-10 px-6"
                  disabled={newAccountDigits.length !== 4}
                  onClick={() => {
                    void runTxnUpload(state.file, {
                      mismatchAction: "new_account",
                      newAccountLast4: newAccountDigits,
                      sourceType: state.pendingSourceType,
                    })
                  }}
                >
                  Continue import
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog
        open={duplicateDialogOpen}
        onOpenChange={(open) => {
          if (!open) cancelDuplicateReupload()
        }}
      >
        <DialogContent showCloseButton={false} className="gap-5 p-6 sm:max-w-md sm:p-7">
          <DialogHeader className="gap-3 text-left sm:text-left">
            <DialogTitle className="pr-2 leading-snug">Import this file again?</DialogTitle>
            <DialogDescription className="leading-relaxed">
              You already uploaded a file named{" "}
              <span className="mt-1 block rounded-md border border-border/60 bg-muted/40 px-3 py-2 font-medium text-foreground wrap-break-word">
                {duplicateDialogFile?.name ?? "this file"}
              </span>
              <span className="mt-2 block">Still add it again?</span>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="mt-1 border-0 bg-transparent p-0 pt-2 sm:justify-end sm:gap-3">
            <Button type="button" variant="outline" onClick={cancelDuplicateReupload}>
              No
            </Button>
            <Button type="button" onClick={confirmDuplicateReupload}>
              Yes, upload again
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

/**
 * Same transaction upload UI as inside **Upload statement**, without the dialog wrapper —
 * used by onboarding fallback so users can drop a file inline during setup.
 */
export function StatementUploadPanel({
  onImportComplete,
  disabled = false,
  statementReviewMode = false,
  onStatementRunReady,
  reviewResetNonce = 0,
  statementRunLiveUnknowns,
  statementSessionHistory = [],
  onStatementSessionHistoryAppend,
}: {
  onImportComplete?: () => void
  /** True while onboarding mail import is actively writing — avoids racing SQLite with upload import. */
  disabled?: boolean
  /**
   * When true (onboarding statement step), use SSE progress and notify the parent when the run is
   * ready so it can show the classification review card below this panel.
   */
  statementReviewMode?: boolean
  onStatementRunReady?: (info: {
    runId: number
    sourceKey: string
    txnCount: number
    newCount: number
    unknownsCount: number
  }) => void
  /** Parent increments this after the run-scoped review queue is cleared so the drop zone returns. */
  reviewResetNonce?: number
  /** Live pending count from ``ClassificationBatchReview`` — updates the import summary while reviewing. */
  statementRunLiveUnknowns?: number
  /** Rows shown in the parent's compact "Earlier imports" list (below Review labels). */
  statementSessionHistory?: StatementUploadHistoryItem[]
  /** Append one finished import snapshot to ``statementSessionHistory`` + sessionStorage. */
  onStatementSessionHistoryAppend?: (
    fields: Omit<StatementUploadHistoryItem, "id" | "importedAt">,
  ) => void
}) {
  return (
    <UploadDialog
      onImportComplete={onImportComplete}
      variant="transactions"
      disabled={disabled}
      compactProgressCopy
      statementReviewMode={statementReviewMode}
      onStatementRunReady={onStatementRunReady}
      reviewResetNonce={reviewResetNonce}
      statementRunLiveUnknowns={statementRunLiveUnknowns}
      statementSessionHistory={statementSessionHistory}
      onStatementSessionHistoryAppend={onStatementSessionHistoryAppend}
    />
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadButton
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  className?: string
  onImportComplete?: () => void
  /** Bank transactions pipeline vs portfolio PDF/CSV fallback */
  variant?: "transactions" | "holdings"
}

export function UploadButton({
  className,
  onImportComplete,
  variant = "transactions",
}: Props) {
  const [open, setOpen] = React.useState(false)

  const title =
    variant === "transactions" ? "Upload Bank Statement" : "Upload Portfolio Statement"
  const description =
    variant === "transactions"
      ? "Import and classify transactions from your statement file."
      : "Import holdings or broker ledger rows when email discovery found nothing."

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm" className={cn("gap-1.5 text-xs", className)}>
            <CloudUpload className="size-3.5" />
            {variant === "transactions" ? "Upload Statement" : "Upload Portfolio File"}
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <UploadDialog onImportComplete={onImportComplete} variant={variant} />
      </DialogContent>
    </Dialog>
  )
}
