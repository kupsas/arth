/**
 * UploadButton — drag-and-drop + click-to-browse statement file uploader.
 *
 * Phase 4.5d: Statement Upload via API
 *
 * Features:
 *   - Click-to-browse or drag-and-drop file selection
 *   - Auto-detects source key from filename (HDFC, ICICI, 1905, 5778)
 *   - Shows upload progress state (uploading → polling → done/error)
 *   - Polls GET /api/pipeline/runs/{id} until the run completes
 *   - Auto-invalidates metrics + transactions on success
 */

"use client"

import * as React from "react"
import { CloudUpload, X } from "lucide-react"
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
import { Progress } from "@/components/ui/progress"
import { uploadStatement } from "@/lib/api"
import { ApiError } from "@/lib/api"
import { metricsKeys } from "@/hooks/use-metrics"
import { cn } from "@/lib/utils"

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

type UploadState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "polling"; runId: number; sourceKey: string }
  | { phase: "done"; txnCount: number; newCount: number; sourceKey: string }
  | { phase: "error"; message: string }

// ─────────────────────────────────────────────────────────────────────────────
// Poll helper — GET /api/pipeline/runs/{id} until status != "running"
// ─────────────────────────────────────────────────────────────────────────────

async function pollRunStatus(
  runId: number,
  onDone: (txnCount: number, newCount: number) => void,
  onError: (msg: string) => void,
) {
  const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
  for (let attempts = 0; attempts < 60; attempts++) {
    await new Promise((r) => setTimeout(r, 2_000))
    try {
      const res = await fetch(`${BASE_URL}/api/pipeline/runs/${runId}`, {
        credentials: "include",
      })
      if (!res.ok) break
      const data = await res.json()
      if (data.status === "completed") {
        onDone(data.txn_count ?? 0, data.new_count ?? 0)
        return
      }
      if (data.status === "failed") {
        onError(data.error_message ?? "Pipeline run failed")
        return
      }
      // status === "running" → keep polling
    } catch {
      break
    }
  }
  onError("Timed out waiting for pipeline to finish. Check the Runs page for status.")
}

// ─────────────────────────────────────────────────────────────────────────────
// DropZone — the drag-and-drop area inside the dialog
// ─────────────────────────────────────────────────────────────────────────────

function DropZone({
  onFile,
  disabled,
}: {
  onFile: (file: File) => void
  disabled: boolean
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
        <p className="text-sm font-medium">Drop your statement here</p>
        <p className="text-xs text-muted-foreground mt-1">
          or click to browse · .txt · .csv · .pdf
        </p>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Filename must contain HDFC / ICICI / 1905 / 5778 for auto-detection.
        <br />
        Other files: specify the source manually below.
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
// UploadDialog — the full dialog content
// ─────────────────────────────────────────────────────────────────────────────

function UploadDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [state, setState] = React.useState<UploadState>({ phase: "idle" })

  async function handleFile(file: File) {
    setState({ phase: "uploading" })
    try {
      const result = await uploadStatement(file)
      setState({ phase: "polling", runId: result.run_id, sourceKey: result.source_key })
      pollRunStatus(
        result.run_id,
        (txnCount, newCount) => {
          setState({ phase: "done", txnCount, newCount, sourceKey: result.source_key })
          // Invalidate all metrics and transactions so the dashboard refreshes
          queryClient.invalidateQueries({ queryKey: metricsKeys.all })
          queryClient.invalidateQueries({ queryKey: ["transactions"] })
        },
        (msg) => setState({ phase: "error", message: msg }),
      )
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Upload failed. Please try again."
      setState({ phase: "error", message: msg })
    }
  }

  return (
    <div className="space-y-4">
      {state.phase === "idle" && (
        <DropZone onFile={handleFile} disabled={false} />
      )}

      {state.phase === "uploading" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm text-muted-foreground">Uploading file…</p>
        </div>
      )}

      {state.phase === "polling" && (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Progress value={undefined} className="w-full animate-pulse" />
          <p className="text-sm font-medium">
            Running pipeline for <span className="font-mono">{state.sourceKey}</span>…
          </p>
          <p className="text-xs text-muted-foreground">
            Parsing → Rules → LLM → Writing to DB. This may take 30–60 seconds.
          </p>
          <p className="text-xs text-muted-foreground">Run #{state.runId}</p>
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
          <Button size="sm" className="mt-2" onClick={() => setState({ phase: "idle" })}>
            Upload another
          </Button>
        </div>
      )}

      {state.phase === "error" && (
        <div className="flex flex-col items-center gap-3 py-6 text-center">
          <div className="flex size-14 items-center justify-center rounded-full bg-red-500/10 text-red-600 text-2xl">
            ✕
          </div>
          <p className="text-sm font-semibold text-red-600">Upload failed</p>
          <p className="text-xs text-muted-foreground max-w-xs">{state.message}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => setState({ phase: "idle" })}>
            Try again
          </Button>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadButton — the trigger button + dialog wrapper
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  className?: string
}

export function UploadButton({ className }: Props) {
  const [open, setOpen] = React.useState(false)

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm" className={cn("gap-1.5 text-xs", className)}>
            <CloudUpload className="size-3.5" />
            Upload Statement
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload Bank Statement</DialogTitle>
          <DialogDescription>
            Upload a statement file to automatically import and classify transactions.
          </DialogDescription>
        </DialogHeader>
        <UploadDialog onClose={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  )
}
