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
import { CloudUpload } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { uploadHoldingsStatement, uploadStatement } from "@/lib/api"
import { ApiError } from "@/lib/api"
import { buildApiUrl } from "@/lib/api-base"
import { holdingsCoverageKey } from "@/hooks/use-onboarding-gaps"
import { metricsKeys } from "@/hooks/use-metrics"
import { portfolioKeys } from "@/hooks/use-portfolio"
import { cn } from "@/lib/utils"
import type { StatementUploadOption } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// UI state
// ─────────────────────────────────────────────────────────────────────────────

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "polling"; runId: number; sourceKey: string }
  | { phase: "type_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  | { phase: "account_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  /** Encrypted PDF — ask user for password (same file is re-uploaded until unlock succeeds) */
  | {
      phase: "needs_pdf_password"
      file: File
      serverMessage: string
      passwordInvalid: boolean
    }
  | { phase: "soft_fail"; kind: "no_match" | "no_source"; message: string; contact?: boolean }
  | { phase: "done"; txnCount: number; newCount: number; sourceKey: string }
  | { phase: "holdings_done"; summary: string }
  | { phase: "holdings_type_pick"; file: File; options: StatementUploadOption[]; serverMessage: string }
  | { phase: "error"; message: string }

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

// ─────────────────────────────────────────────────────────────────────────────
// DropZone
// ─────────────────────────────────────────────────────────────────────────────

function DropZone({
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
}: {
  onImportComplete?: () => void
  variant: "transactions" | "holdings"
}) {
  const queryClient = useQueryClient()
  const [state, setState] = React.useState<UploadState>({ phase: "idle" })
  /** Password field while user is on the needs_pdf_password step */
  const [pdfPasswordInput, setPdfPasswordInput] = React.useState("")
  /**
   * After a PDF unlocks successfully, the browser still holds the **encrypted** File.
   * Later steps (type/account picker) re-upload that file — we must send the same password again.
   */
  const pdfSessionPasswordRef = React.useRef<string | undefined>(undefined)

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
    setState({ phase: "idle" })
  }

  async function runTxnUpload(
    file: File,
    opts?: { sourceKey?: string; sourceType?: string; pdfPassword?: string },
  ) {
    // Prefer explicit password from the form, then any password that already worked this session.
    const mergedPw = opts?.pdfPassword ?? pdfSessionPasswordRef.current
    setState({ phase: "uploading" })
    try {
      const result = await uploadStatement(file, {
        ...(opts?.sourceKey ? { sourceKey: opts.sourceKey } : {}),
        ...(opts?.sourceType ? { sourceType: opts.sourceType } : {}),
        ...(mergedPw ? { pdfPassword: mergedPw } : {}),
      })
      if (result.outcome === "success" && result.run_id != null && result.source_key) {
        rememberWorkingPdfPassword(mergedPw)
        setState({
          phase: "polling",
          runId: result.run_id,
          sourceKey: result.source_key,
        })
        pollRunStatus(
          result.run_id,
          (txnCount, newCount) => {
            setState({
              phase: "done",
              txnCount,
              newCount,
              sourceKey: result.source_key ?? "",
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

  function handleFile(file: File) {
    pdfSessionPasswordRef.current = undefined
    setPdfPasswordInput("")
    if (variant === "transactions") void runTxnUpload(file)
    else void runHoldingsUpload(file)
  }

  function submitPdfPassword() {
    if (state.phase !== "needs_pdf_password") return
    const pwd = pdfPasswordInput
    if (variant === "transactions") void runTxnUpload(state.file, { pdfPassword: pwd })
    else void runHoldingsUpload(state.file, { pdfPassword: pwd })
  }

  return (
    <div className="space-y-4">
      {state.phase === "idle" && (
        <DropZone onFile={handleFile} disabled={false} helpText={helpText} />
      )}

      {state.phase === "uploading" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm text-muted-foreground">
            {variant === "transactions" ? "Uploading and analysing…" : "Uploading portfolio file…"}
          </p>
        </div>
      )}

      {state.phase === "polling" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm font-medium">
            Running import for <span className="font-mono">{state.sourceKey}</span>…
          </p>
          <p className="text-xs text-muted-foreground">
            Parsing → Rules → LLM → Saving. Usually 30–60 seconds.
          </p>
          <p className="text-xs text-muted-foreground">Run #{state.runId}</p>
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

      {state.phase === "done" && (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <div className="flex size-14 items-center justify-center rounded-full bg-green-500/10 text-green-600 text-2xl">
            ✓
          </div>
          <p className="text-sm font-semibold">Done!</p>
          <p className="text-xs text-muted-foreground">
            Processed {state.txnCount} transactions
            {state.newCount > 0 && ` · ${state.newCount} new`} from{" "}
            <span className="font-mono">{state.sourceKey}</span>.
          </p>
          <p className="text-xs text-muted-foreground">Dashboard metrics have been refreshed.</p>
          <Button size="sm" className="mt-2" onClick={goIdle}>
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
