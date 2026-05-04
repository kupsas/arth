"use client"

/**
 * Track 2 **onboarding wizard shell** (Phase 5a–b).
 *
 * - Owns high-level step navigation + a progress indicator.
 * - Runs the Gmail → discovery → identity → optional LLM keys → sequential backfill
 *   (with automatic chunk polling) → optional broker portfolio snapshot → gap review → goals → summary.
 * - The same component is mounted full-screen from ``/setup`` **and** inside the
 *   Settings sheet for **Connect account** — pass ``mode`` + ``className`` only.
 */

import { useQueryClient } from "@tanstack/react-query"
import * as React from "react"

import { GoalTemplateWizard } from "@/components/onboarding/goal-template-wizard"
import { OnboardingOptionalLlmKeys } from "@/components/onboarding/onboarding-optional-llm-keys"
import { PreClassificationForm } from "@/components/onboarding/pre-classification-form"
import { ClassificationBatchReview } from "@/components/onboarding/classification-batch-review"
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
  fetchOnboardingBackfillProgress,
  fetchOnboardingUnknowns,
  patchOnboardingState,
  postOnboardingBackfillChunk,
  postOnboardingBackfillResume,
  postOnboardingPersistSources,
} from "@/lib/api"
import { cn } from "@/lib/utils"
import { humanizeSourceKey } from "@/lib/source-label"
import {
  onboardingBackfillSourcesKey,
  onboardingStateKey,
  useOnboardingBackfillSources,
  useOnboardingState,
} from "@/hooks/use-onboarding"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"

export type WizardStepId =
  | "welcome"
  | "discovery"
  | "preclass"
  | "passwords"
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
  "passwords",
  "apikey",
  "backfill",
  "portfolio_summary",
  "gaps",
  "goals",
  "summary",
] as const satisfies readonly WizardStepId[]

const WIZARD_STEP_IDS = new Set<WizardStepId>(ALL_WIZARD_STEP_IDS)

/** Progress pills: inserts **Portfolio** after Import mail when discovery included a broker source. */
function buildOnboardingStepMeta(
  includePortfolio: boolean,
): { id: WizardStepId; label: string }[] {
  const rows: { id: WizardStepId; label: string }[] = [
    { id: "welcome", label: "Gmail" },
    { id: "discovery", label: "Find accounts" },
    { id: "preclass", label: "Your name" },
    { id: "passwords", label: "PDF secrets" },
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
  /** Optional — first-step **Back** (e.g. return to PDF secrets on ``/setup``). */
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
  /** True during ``POST /backfill`` — counts stay stale until the request returns; see StepBackfill. */
  const [bfChunkPosting, setBfChunkPosting] = React.useState(false)
  const [bfError, setBfError] = React.useState<string | null>(null)
  const [resumeBusy, setResumeBusy] = React.useState(false)

  const queryClient = useQueryClient()
  const [discPersistBusy, setDiscPersistBusy] = React.useState(false)
  const [discPersistError, setDiscPersistError] = React.useState<string | null>(null)

  const handleDiscoveryContinue = React.useCallback(async () => {
    setDiscPersistError(null)
    setDiscPersistBusy(true)
    try {
      await postOnboardingPersistSources()
      await queryClient.invalidateQueries({ queryKey: [...onboardingBackfillSourcesKey] })
      await queryClient.invalidateQueries({ queryKey: [...onboardingStateKey] })
      setUserPanel("preclass")
    } catch (e) {
      setDiscPersistError(getUserFacingErrorMessage(e))
    } finally {
      setDiscPersistBusy(false)
    }
  }, [queryClient])

  const activeSourceKey = sourcesQ.data?.[bfSourceIdx]?.source_key ?? null
  const activeSourceLabel = activeSourceKey ? humanizeSourceKey(activeSourceKey) : null
  const activeSourceType = sourcesQ.data?.[bfSourceIdx]?.source_type ?? null

  /** Coarse section label for the import pipeline (bank cash vs broker portfolio). */
  const importSectionPhase = React.useMemo((): "banking" | "portfolio" | null => {
    const st = (activeSourceType || "").toLowerCase()
    if (st === "broker") return "portfolio"
    if (st === "savings" || st === "credit_card") return "banking"
    return null
  }, [activeSourceType])

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

  // Avoid showing the previous source's numbers on the new tab while the first GET loads.
  React.useEffect(() => {
    setBfProgress(null)
  }, [bfSourceIdx])

  // ── Automated chunk loop (only while the backfill panel is visible) ─────────
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

      while (!signal.aborted) {
        let prog: BackfillProgressSnapshot
        try {
          prog = await fetchOnboardingBackfillProgress(sk, { signal })
        } catch {
          if (signal.aborted) return
          await new Promise((r) => setTimeout(r, 1000))
          continue
        }
        if (signal.aborted) return
        setBfProgress(prog)

        if (prog.status === "needs_classification") {
          await new Promise((r) => setTimeout(r, 2000))
          continue
        }

        if (prog.status === "needs_password") {
          return
        }

        if (prog.status === "complete") {
          if (bfSourceIdx >= currentList.length - 1) {
            // ``GET …/progress`` unknowns are per-source; the review card loads **all** email-linked
            // unknowns. Stay on Import mail until that global queue is empty so rows are not hidden.
            let pendingGlobal = prog.unknowns_pending ?? 0
            try {
              const snap = await fetchOnboardingUnknowns({
                limit: 1,
                offset: 0,
                signal,
              })
              pendingGlobal = snap.pending_total
            } catch {
              if (signal.aborted) return
              // If the global check fails, fall back to this source’s count only.
              pendingGlobal = prog.unknowns_pending ?? 0
            }
            if (signal.aborted) return
            if (pendingGlobal > 0) {
              return
            }
            setUserPanel(hasBrokerSource ? "portfolio_summary" : "gaps")
            return
          }
          setBfSourceIdx((i) => i + 1)
          return
        }

        if (prog.status === "paused") {
          return
        }

        if (prog.status === "error") {
          setBfError(
            prog.error_message
              ? getUserFacingErrorMessage(prog.error_message)
              : "We couldn’t import from email for this account. You can go back, check Gmail, and try again.",
          )
          return
        }

        try {
          // Do **not** pass ``signal`` into the POST: aborting the fetch does not stop the
          // server, so the in-memory backfill lock would still be held → 409 spam and a UI
          // that looks frozen until the server finishes anyway.
          setBfChunkPosting(true)
          await postOnboardingBackfillChunk(sk, { chunk_size: 10 })
        } catch (e) {
          if (signal.aborted) return
          if (e instanceof ApiError && e.status === 409) {
            await new Promise((r) => setTimeout(r, 2000))
            continue
          }
          setBfError(getUserFacingErrorMessage(e) || "We couldn’t start the next batch. Try again.")
          return
        } finally {
          setBfChunkPosting(false)
        }
        await new Promise((r) => setTimeout(r, 400))
      }
    }

    void run()
    return () => {
      ac.abort()
    }
  }, [panel, bfSourceIdx, bfTick, sourcesQ.data, hasBrokerSource])

  const stepIndex = stepMeta.findIndex((s) => s.id === panel)
  const progressPct = Math.max(5, Math.round(((stepIndex + 1) / stepMeta.length) * 100))

  async function handleResumePause() {
    const sk = activeSourceKey
    if (!sk) return
    setResumeBusy(true)
    setBfError(null)
    try {
      await postOnboardingBackfillResume(sk)
      setBfChunkPosting(true)
      await postOnboardingBackfillChunk(sk, { resume_from_pause: true, chunk_size: 10 })
      setBfTick((t) => t + 1)
    } catch (e) {
      setBfError(getUserFacingErrorMessage(e) || "We couldn’t resume the import. Try again.")
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
      await postOnboardingBackfillChunk(sk, { resume_after_password: true, chunk_size: 10 })
      setBfTick((t) => t + 1)
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
      apikey: "passwords",
      passwords: "preclass",
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
      <header className="mb-8 space-y-3">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {mode === "setup" ? "First-run onboarding" : "Connect account"}
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">
          {mode === "setup" ? "Set up Arth" : "Add mail-driven accounts"}
        </h1>
        <div className="h-2 w-full max-w-md rounded-full bg-muted overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-300"
            style={{ width: `${progressPct}%` }}
          />
        </div>
        <ol className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {stepMeta.map((s, idx) => (
            <li
              key={s.id}
              className={cn(
                "rounded-full border px-2 py-0.5",
                idx === stepIndex && "border-primary text-foreground bg-primary/5",
              )}
            >
              {s.label}
            </li>
          ))}
        </ol>
      </header>

      <div className="flex-1">
        {panel === "welcome" && (
          <StepWelcome onContinue={() => setUserPanel("discovery")} />
        )}
        {panel === "discovery" && (
          <StepDiscovery
            onContinue={() => {
              void handleDiscoveryContinue()
            }}
            persistBusy={discPersistBusy}
            persistError={discPersistError}
          />
        )}
        {panel === "preclass" && (
          <div className="space-y-4">
            <PreClassificationForm />
          </div>
        )}
        {panel === "passwords" && (
          <StepPasswordIngredients mode="wizard" onContinue={() => setUserPanel("apikey")} />
        )}
        {panel === "apikey" && (
          <div className="mx-auto w-full max-w-2xl space-y-6">
            <OnboardingOptionalLlmKeys />
          </div>
        )}
        {panel === "backfill" && (
          <div className="space-y-4">
            {sourcesQ.isLoading && (
              <p className="text-sm text-muted-foreground">Loading your email sources…</p>
            )}
            {!sourcesQ.data?.length && !sourcesQ.isLoading && (
              <p className="text-sm text-muted-foreground">
                No bank email sources were found yet. Go back to <strong>Connect Gmail</strong> and
                make sure your inbox is linked, then try <strong>Find accounts</strong> again. If you
                just connected, wait a moment and refresh this page.
              </p>
            )}
            {!!sourcesQ.data?.length && (
              <>
                {bfProgress?.status === "needs_password" && activeSourceKey && (
                  <StepPasswordIngredients
                    mode="resume-import"
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
                  wizardSection={importSectionPhase}
                />
                <ClassificationBatchReview
                  importAwaitingClassification={bfProgress?.status === "needs_classification"}
                  allMailSourcesImported={allMailSourcesFinished}
                  mailImportActivelyProcessing={
                    !allMailSourcesFinished &&
                    (bfChunkPosting ||
                      bfProgress?.current_phase === "listing_alerts" ||
                      (bfProgress != null &&
                        (bfProgress.status === "processing" ||
                          bfProgress.status === "processing_statements" ||
                          bfProgress.status === "processing_alerts")))
                  }
                  unknownsTrigger={`${bfSourceIdx}:${bfProgress?.status ?? "none"}:${bfProgress?.unknowns_pending ?? 0}`}
                  onSubmitted={() => {
                    setBfTick((t) => t + 1)
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
            {!sourcesQ.data?.length && (
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
              <Button type="button" onClick={() => setUserPanel("passwords")}>
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
