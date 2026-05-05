"use client"

/**
 * Track 2 **onboarding wizard shell** (Phase 5a–b).
 *
 * - Owns high-level step navigation + a progress indicator.
 * - Runs the Gmail → discovery → identity → optional LLM keys → sequential backfill
 *   (with Server-Sent Events live email import) → optional broker portfolio snapshot → gap review → goals → summary.
 * - The same component is mounted full-screen from ``/setup`` **and** inside the
 *   Settings sheet for **Connect account** — pass ``mode`` + ``className`` only.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Check, Loader2 } from "lucide-react"
import * as React from "react"

import { GoalTemplateWizard } from "@/components/onboarding/goal-template-wizard"
import { OnboardingOptionalLlmKeys } from "@/components/onboarding/onboarding-optional-llm-keys"
import { PreClassificationForm } from "@/components/onboarding/pre-classification-form"
import { ClassificationBatchReview } from "@/components/onboarding/classification-batch-review"
import { OnboardingErrorCallout } from "@/components/onboarding/onboarding-error-callout"
import { StepBackfill, type BackfillProgressSnapshot } from "@/components/onboarding/step-backfill"
import { StepDiscovery } from "@/components/onboarding/step-discovery"
import { StepGapDetection } from "@/components/onboarding/step-gap-detection"
import { StepPortfolioSummary } from "@/components/onboarding/step-portfolio-summary"
import { StepPasswordIngredients } from "@/components/onboarding/step-password-ingredients"
import { StepSummary } from "@/components/onboarding/step-summary"
import { StepWelcome } from "@/components/onboarding/step-welcome"
import { Button } from "@/components/ui/button"
import {
  ApiError,
  fetchOnboardingUnknowns,
  patchOnboardingState,
  postOnboardingBackfillResume,
  postOnboardingPersistSources,
  streamOnboardingBackfill,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { humanizeSourceKey } from "@/lib/source-label"
import {
  onboardingBackfillSourcesKey,
  onboardingStateKey,
  useOnboardingBackfillSources,
  useOnboardingClassifierStatus,
  useOnboardingState,
} from "@/hooks/use-onboarding"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"

export type WizardStepId =
  | "welcome"
  | "discovery"
  | "preclass"
  | "apikey"
  | "backfill"
  | "portfolio_summary"
  | "gaps"
  | "goals"
  | "summary"

/** Every ``WizardStepId`` the API might persist — used to safely resume ``current_step``. */
const ALL_WIZARD_STEP_IDS = [
  "welcome",
  "discovery",
  "preclass",
  "apikey",
  "backfill",
  "portfolio_summary",
  "gaps",
  "goals",
  "summary",
] as const satisfies readonly WizardStepId[]

const WIZARD_STEP_IDS = new Set<WizardStepId>(ALL_WIZARD_STEP_IDS)
const LEGACY_WIZARD_STEPS: Record<string, WizardStepId> = {
  /** Merged into Config (``preclass``) — see ``PdfPasswordConfigFields`` in pre-classification. */
  passwords: "preclass",
}

/** Map stream / API JSON into the **Import mail** card snapshot (defensive coercions). */
function recordToBackfillSnapshot(
  p: Record<string, unknown> | null | undefined,
): BackfillProgressSnapshot | null {
  if (!p || typeof p.source !== "string") return null
  return {
    source: p.source,
    status: String(p.status ?? "idle"),
    emails_found: Number(p.emails_found ?? 0),
    emails_processed: Number(p.emails_processed ?? 0),
    transactions_parsed: Number(p.transactions_parsed ?? 0),
    unknowns_pending: Number(p.unknowns_pending ?? 0),
    error_message: p.error_message != null ? String(p.error_message) : null,
    current_phase: p.current_phase != null ? String(p.current_phase) : null,
    password_parser_key: p.password_parser_key != null ? String(p.password_parser_key) : null,
    password_failure_message_id:
      p.password_failure_message_id != null ? String(p.password_failure_message_id) : null,
    current_window_label: p.current_window_label != null ? String(p.current_window_label) : null,
    windows_total: p.windows_total != null ? Number(p.windows_total) : undefined,
    windows_completed: p.windows_completed != null ? Number(p.windows_completed) : undefined,
  }
}

/** Progress pills: inserts **Portfolio** after Import mail when discovery included a broker source. */
function buildOnboardingStepMeta(
  includePortfolio: boolean,
): { id: WizardStepId; label: string }[] {
  const rows: { id: WizardStepId; label: string }[] = [
    { id: "welcome", label: "Gmail" },
    { id: "discovery", label: "Find accounts" },
    { id: "preclass", label: "Config" },
    { id: "apikey", label: "Smart labels (opt.)" },
    { id: "backfill", label: "Import mail" },
  ]
  if (includePortfolio) {
    rows.push({ id: "portfolio_summary", label: "Portfolio" })
  }
  rows.push(
    { id: "gaps", label: "Coverage" },
    { id: "goals", label: "Goals" },
    { id: "summary", label: "Done" },
  )
  return rows
}

/**
 * Map persisted ``OnboardingState.current_step`` to the in-memory panel id.
 * ``classification`` used to be a separate step; we now embed review under **Import mail**.
 * ``completed`` means the user finished — start a fresh connect-account flow at welcome.
 */
function panelFromServerStep(step: string): WizardStepId {
  if (step === "classification") {
    return "backfill"
  }
  if (step === "completed") {
    return "welcome"
  }
  if (step in LEGACY_WIZARD_STEPS) {
    return LEGACY_WIZARD_STEPS[step] as WizardStepId
  }
  if (WIZARD_STEP_IDS.has(step as WizardStepId)) {
    return step as WizardStepId
  }
  return "welcome"
}

export type OnboardingWizardProps = {
  mode: "setup" | "settings"
  className?: string
  /** Fires after ``POST /api/onboarding/complete`` succeeds. */
  onFinished: () => void
  /** Optional — first-step **Back** (e.g. return from discovery on ``/setup``). */
  onExitFirstStep?: () => void
}

export function OnboardingWizard({
  mode,
  className,
  onFinished,
  onExitFirstStep,
}: OnboardingWizardProps) {
  const stateQ = useOnboardingState()
  const sourcesQ = useOnboardingBackfillSources()
  const classifierStatusQ = useOnboardingClassifierStatus()
  const hasBrokerSource = React.useMemo(
    () => (sourcesQ.data ?? []).some((s) => (s.source_type || "").toLowerCase() === "broker"),
    [sourcesQ.data],
  )
  const stepMeta = React.useMemo(
    () => buildOnboardingStepMeta(hasBrokerSource),
    [hasBrokerSource],
  )
  /**
   * Server-resumed step from ``GET /state`` (null while the query is still loading).
   * We derive the visible step below so we **do not** need a hydration effect that calls
   * setState — that pattern triggers ``react-hooks/set-state-in-effect``.
   */
  const serverPanel = React.useMemo((): WizardStepId | null => {
    if (stateQ.isLoading) return null
    const step = stateQ.data?.current_step ?? "welcome"
    if (step === "portfolio_summary" && !hasBrokerSource) {
      return "gaps"
    }
    return panelFromServerStep(step)
  }, [stateQ.isLoading, stateQ.data, hasBrokerSource])
  /**
   * Once the user moves forward/back in the wizard, this override wins over ``serverPanel``
   * for the rest of the session (same as “we already hydrated from the server” before).
   */
  const [userPanel, setUserPanel] = React.useState<WizardStepId | null>(null)
  const panel: WizardStepId = userPanel ?? serverPanel ?? "welcome"
  const prevPanelRef = React.useRef<WizardStepId | null>(null)

  const [bfSourceIdx, setBfSourceIdx] = React.useState(0)
  const [bfTick, setBfTick] = React.useState(0)
  const [bfProgress, setBfProgress] = React.useState<BackfillProgressSnapshot | null>(null)
  /**
   * True while the SSE import stream is connected — drives the Import mail card hint only.
   * Do **not** use this to lock the classification queue (see ``mailImportActivelyProcessing``).
   */
  const [bfChunkPosting, setBfChunkPosting] = React.useState(false)
  const [bfError, setBfError] = React.useState<string | null>(null)
  const [resumeBusy, setResumeBusy] = React.useState(false)
  const [persistRetryBusy, setPersistRetryBusy] = React.useState(false)

  const queryClient = useQueryClient()

  const handleDiscoveryContinue = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: [...onboardingBackfillSourcesKey] })
    void queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
    setUserPanel("preclass")
  }, [queryClient])

  /** When background persist-sources finishes, refresh pipeline source list for Import mail. */
  React.useEffect(() => {
    if (stateQ.data?.persist_sources_status !== "done") return
    void queryClient.invalidateQueries({ queryKey: [...onboardingBackfillSourcesKey] })
  }, [stateQ.data?.persist_sources_status, queryClient])

  const handlePersistSourcesRetry = React.useCallback(async () => {
    setPersistRetryBusy(true)
    try {
      await postOnboardingPersistSources()
      await queryClient.invalidateQueries({ queryKey: [...onboardingBackfillSourcesKey] })
      await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
    } catch (e) {
      setBfError(getUserFacingErrorMessage(e) || "Could not finish setting up sources.")
    } finally {
      setPersistRetryBusy(false)
    }
  }, [queryClient])

  const activeSourceKey = sourcesQ.data?.[bfSourceIdx]?.source_key ?? null
  const activeSourceLabel = activeSourceKey ? humanizeSourceKey(activeSourceKey) : null

  const persistSourcesStatus = stateQ.data?.persist_sources_status ?? "idle"
  const persistSourcesWait = persistSourcesStatus === "running"
  const persistSourcesFailed = persistSourcesStatus === "error"

  /**
   * Last email source finished ingesting (no more chunk work for this account). The combined
   * classification queue can still list rows from *any* prior source — ``unknowns_pending`` on
   * ``GET …/progress`` is only for the active source, so we must not treat “this source: 0” as
   * “nothing left to review” when deciding auto-advance or read-only overlays.
   */
  const backfillSourcesLen = sourcesQ.data?.length ?? 0
  const allMailSourcesFinished =
    backfillSourcesLen > 0 &&
    bfSourceIdx === backfillSourcesLen - 1 &&
    bfProgress?.status === "complete"

  /** Matches server ``effective_onboarding_unknown_threshold`` (pause ~20). */
  const unknownPauseThreshold = classifierStatusQ.data?.unknown_threshold ?? 20

  /**
   * Global unknown backlog (all email-linked sources). Used so we do not dim the review table
   * or strip the Uber shortcut when the user already has a pause-sized backlog between SSE sources.
   */
  const globalPendingUnknownsQ = useQuery({
    queryKey: ["onboarding", "unknowns-pending-total", bfSourceIdx, bfTick] as const,
    queryFn: async () =>
      (await fetchOnboardingUnknowns({ limit: 1, offset: 0 })).pending_total,
    enabled: panel === "backfill",
    staleTime: 3_000,
  })
  const globalPendingUnknowns = globalPendingUnknownsQ.data

  /** First SSE events not applied yet for this source — Import card shows “Connecting…”. */
  const importStreamAwaitingSnapshot = bfChunkPosting && bfProgress === null

  /**
   * Between accounts the backlog may still be tiny — hide the txn rows until we know it is worth
   * showing or the stream has attached (avoids flashing rows then “Connecting…” above).
   */
  const hideClassificationRowsForImportLimbo =
    importStreamAwaitingSnapshot &&
    (globalPendingUnknowns === undefined || globalPendingUnknowns < unknownPauseThreshold)

  /**
   * Dim the classification queue while mail is **actively being parsed** into the DB, so saves
   * do not race with the importer. With SSE, ``bfChunkPosting`` stays true for the whole HTTP
   * stream — do **not** key off that flag here. Never block during gates. If the combined queue
   * is already at/over the pause threshold, keep the table interactive while the next source
   * connects so rows do not “vanish” when the stream attaches.
   */
  const mailImportActivelyProcessing = React.useMemo(() => {
    // Never dim once all sources are done.
    if (allMailSourcesFinished) return false

    // User-action gates: import has paused and is waiting for the user to act.
    // These are the ONLY states that legitimately drop the overlay.
    const st = bfProgress?.status
    if (
      st === "needs_classification" ||
      st === "needs_password" ||
      st === "paused"
    ) {
      return false
    }

    // Bug fix 1: source-switch limbo — bfProgress is null while the new SSE stream
    // starts (setBfProgress(null) fires on bfSourceIdx change before the first event).
    // importStreamAwaitingSnapshot = bfChunkPosting && bfProgress === null; keep the
    // overlay so it never blinks out between accounts.
    if (importStreamAwaitingSnapshot) return true

    // Bug fix 2: listing_alerts phase — Gmail alert scanning is slow but the importer
    // is still writing to the DB; removing the overlay here caused visible flicker.
    // Bug fix 3: pending count >= pause-threshold no longer drops the overlay — the
    // overlay is a DB-race guard, not a "you have enough to review" hint.
    return (
      bfProgress != null &&
      (st === "processing" ||
        st === "processing_statements" ||
        st === "processing_alerts")
    )
  }, [allMailSourcesFinished, bfProgress, importStreamAwaitingSnapshot])

  // Persist coarse wizard position so a refresh mid-flow still shows the same step name.
  // Skip while onboarding state is still loading and the user has not navigated yet — otherwise
  // we would PATCH the default ``welcome`` over the real server step.
  React.useEffect(() => {
    if (stateQ.isLoading && userPanel === null) return
    void patchOnboardingState({ current_step: panel }).catch(() => {
      /* non-fatal */
    })
  }, [panel, stateQ.isLoading, userPanel])

  // When entering backfill from earlier setup steps, restart the source queue.
  React.useEffect(() => {
    const prev = prevPanelRef.current
    prevPanelRef.current = panel
    if (panel !== "backfill") return
    if (prev === "backfill") return
    setBfSourceIdx(0)
    setBfProgress(null)
    setBfError(null)
  }, [panel])

  // Avoid showing the previous source's numbers on the new tab until the first SSE events arrive.
  React.useEffect(() => {
    setBfProgress(null)
  }, [bfSourceIdx])

  // ── Gmail import: one SSE stream per effect run (per-email progress; gates end the stream) ──
  React.useEffect(() => {
    if (panel !== "backfill") return
    if (!sourcesQ.data?.length) return

    const ac = new AbortController()
    const { signal } = ac

    async function run() {
      setBfError(null)
      const currentList = sourcesQ.data
      if (!currentList?.length) {
        setUserPanel("gaps")
        return
      }
      const sk = currentList[bfSourceIdx]?.source_key
      if (!sk) {
        setUserPanel("gaps")
        return
      }

      /**
       * When this run advances to the next source (setBfSourceIdx), the finally block must
       * NOT clear bfChunkPosting — the new source's run() sets it true and the old finally
       * fires after, clobbering it (race confirmed by debug session 0f1d46). Track intent here.
       */
      let advancingSource = false
      setBfChunkPosting(true)
      try {
        const streamResult = await streamOnboardingBackfill(sk, {
          signal,
          onProgress: (snap) => {
            const shot = recordToBackfillSnapshot(snap)
            if (shot) setBfProgress(shot)
          },
        })
        if (signal.aborted) return

        const shot = recordToBackfillSnapshot(streamResult.lastProgress)
        if (shot) setBfProgress(shot)

        const st = shot?.status ?? ""

        if (st === "needs_password" || st === "paused") {
          await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
          return
        }

        if (st === "needs_classification") {
          await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
          return
        }

        if (st === "error") {
          setBfError(
            shot?.error_message
              ? getUserFacingErrorMessage(shot.error_message)
              : "We couldn't import from email for this account. You can go back, check Gmail, and try again.",
          )
          await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
          return
        }

        if (st === "complete" || streamResult.endReason === "complete") {
          if (bfSourceIdx >= currentList.length - 1) {
            let pendingGlobal = shot?.unknowns_pending ?? 0
            try {
              const unknownSnap = await fetchOnboardingUnknowns({
                limit: 1,
                offset: 0,
                signal,
              })
              pendingGlobal = unknownSnap.pending_total
            } catch {
              if (signal.aborted) return
              pendingGlobal = shot?.unknowns_pending ?? 0
            }
            if (signal.aborted) return
            if (pendingGlobal > 0) {
              await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
              return
            }
            setUserPanel(hasBrokerSource ? "portfolio_summary" : "gaps")
            await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
            return
          }
          advancingSource = true   // tell finally not to clear bfChunkPosting
          setBfSourceIdx((i) => i + 1)
          await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
          return
        }

        await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
      } catch (e) {
        if (signal.aborted) return
        if (e instanceof ApiError && e.status === 409) {
          await new Promise((r) => setTimeout(r, 2000))
          setBfTick((t) => t + 1)
          return
        }
        setBfError(getUserFacingErrorMessage(e) || "We couldn't import from email. Try again.")
        await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
      } finally {
        // Do NOT clear bfChunkPosting when we intentionally advanced to the next source.
        // The new source's run() has already called setBfChunkPosting(true) by this point;
        // clearing it here would clobber that and leave the overlay in a gap state.
        if (!advancingSource) setBfChunkPosting(false)
      }
    }

    void run()
    return () => {
      ac.abort()
    }
  }, [panel, bfSourceIdx, bfTick, sourcesQ.data, hasBrokerSource, queryClient])


  const stepIndex = stepMeta.findIndex((s) => s.id === panel)

  async function handleResumePause() {
    const sk = activeSourceKey
    if (!sk) return
    setResumeBusy(true)
    setBfError(null)
    try {
      await postOnboardingBackfillResume(sk)
      setBfChunkPosting(true)
      await streamOnboardingBackfill(sk, {
        resume_from_pause: true,
        onProgress: (snap) => {
          const shot = recordToBackfillSnapshot(snap)
          if (shot) setBfProgress(shot)
        },
      })
      setBfTick((t) => t + 1)
      void queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
    } catch (e) {
      setBfError(getUserFacingErrorMessage(e) || "We couldn't resume the import. Try again.")
    } finally {
      setBfChunkPosting(false)
      setResumeBusy(false)
    }
  }

  async function handlePasswordGateResolved() {
    const sk = activeSourceKey
    if (!sk) return
    setBfChunkPosting(true)
    setBfError(null)
    try {
      await streamOnboardingBackfill(sk, {
        resume_after_password: true,
        onProgress: (snap) => {
          const shot = recordToBackfillSnapshot(snap)
          if (shot) setBfProgress(shot)
        },
      })
      setBfTick((t) => t + 1)
      void queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
    } catch (e) {
      setBfError(getUserFacingErrorMessage(e) || "Could not retry import.")
    } finally {
      setBfChunkPosting(false)
    }
  }

  function goBack() {
    if (panel === "welcome") {
      onExitFirstStep?.()
      return
    }
    if (panel === "gaps") {
      setUserPanel(hasBrokerSource ? "portfolio_summary" : "backfill")
      return
    }
    if (panel === "portfolio_summary") {
      setUserPanel("backfill")
      return
    }
    const prevMap: Partial<Record<WizardStepId, WizardStepId>> = {
      summary: "goals",
      goals: "gaps",
      apikey: "preclass",
      preclass: "discovery",
      discovery: "welcome",
      backfill: "apikey",
    }
    const prev = prevMap[panel]
    if (prev) setUserPanel(prev)
  }

  const canBack = panel !== "summary"

  return (
    <div
      className={cn(
        "flex flex-col min-h-[60vh]",
        mode === "setup" && "max-w-4xl mx-auto w-full",
        className,
      )}
    >
      <header>
        {/*
          Vertical rhythm: keep eyebrow + title as one tight block, then the same nominal gap
          above and below the stepper. ``space-y-3`` on the whole header used to sit between the
          h1 line box and the circles; the h1’s default line-height also left a lot of empty space
          under the words, so the stepper felt farther from the title than from the next section.
        */}
        <div className="space-y-3">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {mode === "setup" ? "First-run onboarding" : "Connect account"}
          </p>
          <h1 className="text-3xl font-semibold tracking-tight leading-tight">
            {mode === "setup" ? "Set up Arth" : "Add mail-driven accounts"}
          </h1>
        </div>
        {/* Step progress stepper — equal vertical padding so title ↔ stepper ↔ page content line up */}
        <ol className="flex w-full items-start py-8" aria-label="Onboarding steps">
          {stepMeta.map((s, idx) => {
            const isCompleted = idx < stepIndex
            const isActive = idx === stepIndex
            const isUpcoming = idx > stepIndex
            return (
              <React.Fragment key={s.id}>
                <li className="flex flex-col items-center gap-1.5 shrink-0">
                  {/* Circle indicator */}
                  <div
                    className={cn(
                      "size-7 rounded-full flex items-center justify-center text-[11px] font-semibold border-2 transition-all duration-300",
                      isCompleted && "bg-primary border-primary text-primary-foreground",
                      isActive && "border-primary text-primary bg-primary/10 shadow-sm",
                      isUpcoming && "border-border text-muted-foreground/40 bg-transparent",
                    )}
                  >
                    {isCompleted ? (
                      <Check className="size-3.5" strokeWidth={2.5} />
                    ) : (
                      <span>{idx + 1}</span>
                    )}
                  </div>
                  {/* Step label */}
                  <span
                    className={cn(
                      "text-[10px] font-medium text-center leading-tight w-14 transition-colors duration-300",
                      isActive && "text-foreground",
                      isCompleted && "text-primary",
                      isUpcoming && "text-muted-foreground/35",
                    )}
                  >
                    {s.label}
                  </span>
                </li>
                {/* Connector line between steps */}
                {idx < stepMeta.length - 1 && (
                  <div
                    className={cn(
                      "flex-1 h-0.5 mt-3.5 mx-1 rounded-full transition-all duration-500",
                      idx < stepIndex ? "bg-primary" : "bg-border",
                    )}
                  />
                )}
              </React.Fragment>
            )
          })}
        </ol>
      </header>

      <div className="flex-1">
        {panel === "welcome" && (
          <StepWelcome onContinue={() => setUserPanel("discovery")} />
        )}
        {panel === "discovery" && (
          <StepDiscovery onContinue={handleDiscoveryContinue} />
        )}
        {panel === "preclass" && (
          <div className="space-y-4">
            <PreClassificationForm />
          </div>
        )}
        {panel === "apikey" && (
          <div className="mx-auto w-full max-w-2xl space-y-6">
            <OnboardingOptionalLlmKeys />
          </div>
        )}
        {panel === "backfill" && (
          <div className="space-y-4">
            {persistSourcesFailed && (
              <OnboardingErrorCallout
                title="Could not finish setting up email sources"
                hint="Check Gmail is still connected, then try again."
              >
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => void handlePersistSourcesRetry()}
                    disabled={persistRetryBusy}
                  >
                    {persistRetryBusy ? "Retrying…" : "Retry setup"}
                  </Button>
                </div>
              </OnboardingErrorCallout>
            )}
            {persistSourcesWait && (
              <p className="text-sm text-muted-foreground flex items-center gap-2" aria-live="polite">
                <Loader2 className="size-4 animate-spin shrink-0" aria-hidden />
                Setting up your accounts from Gmail… This usually finishes in a few seconds (under
                about 10 seconds).
              </p>
            )}
            {sourcesQ.isLoading && (
              <p className="text-sm text-muted-foreground">Loading your email sources…</p>
            )}
            {!sourcesQ.data?.length && !sourcesQ.isLoading && !persistSourcesWait && (
              <p className="text-sm text-muted-foreground">
                No bank email sources were found yet. Go back to <strong>Connect Gmail</strong> and
                make sure your inbox is linked, then try <strong>Find accounts</strong> again. If you
                just connected, wait a moment and refresh this page.
              </p>
            )}
            {!!sourcesQ.data?.length && (
              <>
                {/* TODO: Add a "paste exact PDF password" path when ingredients are not enough; see
                    ``onboarding_orchestrator`` needs_password (UserSecrets override, not .env/DB). */}
                {bfProgress?.status === "needs_password" && activeSourceKey && (
                  <StepPasswordIngredients
                    blockingParserKey={bfProgress.password_parser_key ?? undefined}
                    onSaved={() => void handlePasswordGateResolved()}
                  />
                )}
                <StepBackfill
                  title={activeSourceLabel ?? activeSourceKey ?? "…"}
                  progress={bfProgress}
                  error={bfError}
                  sources={sourcesQ.data}
                  activeSourceIndex={bfSourceIdx}
                  onResumeFromPause={bfProgress?.status === "paused" ? handleResumePause : undefined}
                  resumeBusy={resumeBusy}
                  importBusy={bfChunkPosting}
                />
                <ClassificationBatchReview
                  importAwaitingClassification={bfProgress?.status === "needs_classification"}
                  allMailSourcesImported={allMailSourcesFinished}
                  mailImportActivelyProcessing={mailImportActivelyProcessing}
                  hideClassificationRowsForImportLimbo={hideClassificationRowsForImportLimbo}
                  pauseThresholdForShortcuts={unknownPauseThreshold}
                  unknownsTrigger={`${bfSourceIdx}:${bfProgress?.status ?? "none"}:${bfProgress?.unknowns_pending ?? 0}`}
                  onSubmitted={() => {
                    setBfTick((t) => t + 1)
                  }}
                  onImportProgress={(snap) => {
                    const shot = recordToBackfillSnapshot(snap)
                    if (shot) setBfProgress(shot)
                  }}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setUserPanel(hasBrokerSource ? "portfolio_summary" : "gaps")
                  }
                >
                  Skip remaining mail → gap check
                </Button>
              </>
            )}
            {!sourcesQ.data?.length && !persistSourcesWait && (
              <Button
                type="button"
                variant="secondary"
                onClick={() => setUserPanel(hasBrokerSource ? "portfolio_summary" : "gaps")}
              >
                Skip to gap check
              </Button>
            )}
          </div>
        )}
        {panel === "portfolio_summary" && <StepPortfolioSummary />}
        {panel === "gaps" && <StepGapDetection />}
        {panel === "goals" && <GoalTemplateWizard />}
        {panel === "summary" && <StepSummary onDone={onFinished} />}
      </div>

      {panel !== "welcome" && panel !== "discovery" && (
        <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t pt-6">
          <Button type="button" variant="ghost" onClick={() => goBack()} disabled={!canBack}>
            Back
          </Button>
          <div className="flex flex-wrap gap-2">
            {panel === "preclass" && (
              <Button type="button" onClick={() => setUserPanel("apikey")}>
                Continue
              </Button>
            )}
            {panel === "apikey" && (
              <Button type="button" onClick={() => setUserPanel("backfill")}>
                Start importing mail
              </Button>
            )}
            {panel === "portfolio_summary" && (
              <Button type="button" onClick={() => setUserPanel("gaps")}>
                Continue to coverage
              </Button>
            )}
            {panel === "gaps" && (
              <Button type="button" onClick={() => setUserPanel("goals")}>
                Continue to goals
              </Button>
            )}
            {panel === "goals" && (
              <Button type="button" onClick={() => setUserPanel("summary")}>
                Continue
              </Button>
            )}
          </div>
        </footer>
      )}

      {(panel === "welcome" || panel === "discovery") && (
        <footer className="mt-8 flex justify-start">
          <Button
            type="button"
            variant="ghost"
            onClick={() => goBack()}
            disabled={panel === "welcome" && !onExitFirstStep}
          >
            Back
          </Button>
        </footer>
      )}
    </div>
  )
}
