/**
 * React Query helpers for the Track 2 onboarding wizard (Phase 5).
 *
 * The FastAPI routes live under ``/api/onboarding/*``.  We keep query keys in one
 * place so both the full-screen ``/setup`` wizard and the Settings **Connect account**
 * sheet can invalidate the same cache after discovery / backfill / completion.
 */
"use client"

import * as React from "react"
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"

import {
  fetchOnboardingBackfillSources,
  fetchOnboardingClassifierStatus,
  fetchOnboardingState,
  patchOnboardingState,
  postOnboardingBackfillChunk,
  postOnboardingComplete,
  streamOnboardingDiscover,
  SETUP_STATUS_QUERY_KEY,
} from "@/lib/api"
import type { OnboardingDiscoveryStreamRow } from "@/lib/api"
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error"
import type {
  OnboardingBackfillSourceRow,
  OnboardingStateResponse,
} from "@/lib/types"

export const onboardingStateKey = ["onboarding", "state"] as const
export const onboardingBackfillSourcesKey = ["onboarding", "backfill-sources"] as const
export const onboardingClassifierStatusKey = ["onboarding", "classifier-status"] as const

export function useOnboardingState(
  options?: Partial<UseQueryOptions<OnboardingStateResponse>>,
) {
  return useQuery<OnboardingStateResponse>({
    queryKey: [...onboardingStateKey],
    queryFn: () => fetchOnboardingState(),
    staleTime: 10_000,
    ...options,
  })
}

export function useOnboardingBackfillSources(
  options?: Partial<UseQueryOptions<OnboardingBackfillSourceRow[]>>,
) {
  return useQuery<OnboardingBackfillSourceRow[]>({
    queryKey: [...onboardingBackfillSourcesKey],
    queryFn: () => fetchOnboardingBackfillSources(),
    staleTime: 60_000,
    ...options,
  })
}

export function useOnboardingClassifierStatus(
  options?: Partial<
    UseQueryOptions<{
      llm_model: string
      has_any_api_key: boolean
      unknown_threshold: number
    }>
  >,
) {
  return useQuery({
    queryKey: [...onboardingClassifierStatusKey],
    queryFn: () => fetchOnboardingClassifierStatus(),
    staleTime: 30_000,
    ...options,
  })
}

export function usePatchOnboardingState() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: patchOnboardingState,
    onSuccess: () => void qc.invalidateQueries({ queryKey: [...onboardingStateKey] }),
  })
}

export type OnboardingDiscoverRunStatus =
  | "idle"
  | "connecting"
  | "scanning"
  | "done"
  | "error"

/** One persisted row from ``OnboardingState.discovery_results_json`` (same shape as stream ``found``). */
function isPersistedDiscoveryRow(x: unknown): x is OnboardingDiscoveryStreamRow {
  if (x === null || typeof x !== "object") return false
  const o = x as Record<string, unknown>
  return (
    typeof o.sender_email === "string" &&
    typeof o.display_name === "string" &&
    typeof o.source_type === "string" &&
    typeof o.email_count_estimate === "number" &&
    (o.earliest_email_date === null || typeof o.earliest_email_date === "string") &&
    (o.latest_email_date === null || typeof o.latest_email_date === "string")
  )
}

/**
 * Parse ``GET /api/onboarding/state`` → ``discovery_results`` after a **completed** discover run.
 * Returns ``null`` if nothing usable (first visit, corrupt JSON, or mid-scan with nothing saved).
 */
export function parsePersistedDiscoveryResults(
  discovery_results: Record<string, unknown> | undefined,
): { rows: OnboardingDiscoveryStreamRow[]; discoveredAt: string } | null {
  if (!discovery_results || typeof discovery_results !== "object") return null
  const discoveredAt = discovery_results["discovered_at"]
  if (typeof discoveredAt !== "string" || !discoveredAt.trim()) return null
  const sources = discovery_results["sources"]
  if (!Array.isArray(sources)) return null
  const rows: OnboardingDiscoveryStreamRow[] = []
  for (const item of sources) {
    if (!isPersistedDiscoveryRow(item)) return null
    rows.push({
      sender_email: item.sender_email,
      display_name: item.display_name,
      source_type: item.source_type,
      email_count_estimate: item.email_count_estimate,
      earliest_email_date: item.earliest_email_date,
      latest_email_date: item.latest_email_date,
    })
  }
  return { rows, discoveredAt: discoveredAt.trim() }
}

/**
 * Runs streaming ``POST /api/onboarding/discover`` (NDJSON) and exposes row-by-row progress
 * for the “Discover sources” step (no React Query mutation — the stream drives state).
 *
 * Call ``hydrateFromPersisted`` when ``GET /state`` already has ``discovery_results`` from a
 * finished scan so the user does not wait through Gmail again (Back / tab restore).
 */
export function useOnboardingDiscover() {
  const qc = useQueryClient()
  const [status, setStatus] = React.useState<OnboardingDiscoverRunStatus>("idle")
  const [total, setTotal] = React.useState(0)
  const [checked, setChecked] = React.useState(0)
  const [rows, setRows] = React.useState<OnboardingDiscoveryStreamRow[]>([])
  const [errorText, setErrorText] = React.useState<string | null>(null)
  /** ISO timestamp from server when the snapshot was produced (stream ``done`` or hydrated DB row). */
  const [discoveredAt, setDiscoveredAt] = React.useState<string | null>(null)

  const isPending = status === "connecting" || status === "scanning"
  const isError = status === "error"
  const isSuccess = status === "done"

  const hydrateFromPersisted = React.useCallback(
    (parsed: { rows: OnboardingDiscoveryStreamRow[]; discoveredAt: string }) => {
      setErrorText(null)
      setRows(parsed.rows)
      const n = parsed.rows.length
      setTotal(n)
      setChecked(n)
      setDiscoveredAt(parsed.discoveredAt)
      setStatus("done")
    },
    [],
  )

  const runDiscover = React.useCallback(async (signal?: AbortSignal) => {
    setErrorText(null)
    setRows([])
    setTotal(0)
    setChecked(0)
    setDiscoveredAt(null)
    setStatus("connecting")
    try {
      await streamOnboardingDiscover((ev) => {
        if (ev.type === "start") {
          setTotal(ev.total)
          setStatus("scanning")
        }
        if (ev.type === "found") {
          setRows((prev) => [...prev, ev.source])
          setChecked(ev.index + 1)
        }
        if (ev.type === "done") {
          setDiscoveredAt(ev.discovered_at)
          void qc.invalidateQueries({ queryKey: [...onboardingStateKey] })
        }
      }, { signal })
      setStatus("done")
    } catch (e) {
      const aborted =
        (typeof e === "object" &&
          e !== null &&
          "name" in e &&
          (e as { name: string }).name === "AbortError") ||
        (e instanceof DOMException && e.name === "AbortError")
      if (aborted) return
      setStatus("error")
      setErrorText(getUserFacingErrorMessage(e))
      setRows([])
      setDiscoveredAt(null)
    }
  }, [qc])

  return {
    runDiscover,
    hydrateFromPersisted,
    status,
    total,
    checked,
    rows,
    errorText,
    discoveredAt,
    isPending,
    isError,
    isSuccess,
  }
}

export function useOnboardingBackfillChunk() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (args: {
      source: string
      body?: {
        chunk_size?: number
        resume_after_classification?: boolean
        resume_from_pause?: boolean
      }
    }) => postOnboardingBackfillChunk(args.source, args.body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...onboardingStateKey] })
    },
  })
}

export function useOnboardingComplete() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: postOnboardingComplete,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...onboardingStateKey] })
      void qc.invalidateQueries({ queryKey: SETUP_STATUS_QUERY_KEY })
    },
  })
}
