"use client"

/**
 * Step 2 — Auto-discovery (Track 2 Phase 5a).
 *
 * Calls ``POST /api/onboarding/discover`` which streams NDJSON: we show per-sender
 * progress as each bank sender is probed in Gmail (message ID list only).
 *
 * If ``GET /api/onboarding/state`` already has ``discovery_results`` from a finished
 * scan (e.g. user clicked Back or reopened the tab), we **hydrate** that snapshot and
 * skip Gmail until they tap “Re-scan.”
 */

import * as React from "react"
import { ChevronDown, Loader2, Radar } from "lucide-react"

import { OnboardingErrorCallout } from "@/components/onboarding/onboarding-error-callout"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  parsePersistedDiscoveryResults,
  useOnboardingDiscover,
  useOnboardingState,
} from "@/hooks/use-onboarding"
import type { OnboardingDiscoveryStreamRow } from "@/lib/api"
import {
  discoveryCategoryMeta,
  groupDiscoveryRowsForUi,
  type DiscoveryUiCategory,
} from "@/lib/discovery-account-groups"
import { cn } from "@/lib/utils"

/** Human-readable “last scanned” line from server ISO timestamp. */
function formatLastScannedLabel(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

const DISCOVERY_UI_SECTIONS: DiscoveryUiCategory[] = ["bank", "demat", "credit"]

/** HTML ``id`` / ``aria-*`` targets must not contain spaces — institution labels sometimes do. */
function discoveryPanelSafeFragment(cat: DiscoveryUiCategory, institution: string): string {
  return `${cat}-${institution.replace(/\s+/g, "-").replace(/[^a-zA-Z0-9_-]/g, "")}`
}

/**
 * Per-sender breakdown inside an expanded institution row (Gmail address + volume).
 * Only shown after the user clicks the institution accordion — keeps the first screen calm.
 */
function DiscoverySenderDetailTable({ rows }: { rows: OnboardingDiscoveryStreamRow[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Sender</TableHead>
          <TableHead className="text-right">≈ Msgs</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.sender_email}>
            <TableCell>
              <div className="font-medium">{r.display_name}</div>
              <div className="text-xs text-muted-foreground font-mono">{r.sender_email}</div>
            </TableCell>
            <TableCell className="text-right tabular-nums">{r.email_count_estimate}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export interface StepDiscoveryProps {
  onContinue: () => void
}

export function StepDiscovery({ onContinue }: StepDiscoveryProps) {
  const onboardingState = useOnboardingState()
  const {
    runDiscover,
    hydrateFromPersisted,
    isPending,
    isError,
    isSuccess,
    status,
    total,
    checked,
    rows,
    errorText,
    discoveredAt,
  } = useOnboardingDiscover()

  /** When set, ``GET /state`` had a completed scan — used so we do not re-run discover on every refetch. */
  const persistedScanKey =
    onboardingState.data?.discovery_results &&
    typeof onboardingState.data.discovery_results["discovered_at"] === "string"
      ? onboardingState.data.discovery_results["discovered_at"]
      : ""

  React.useEffect(() => {
    if (!onboardingState.isFetched && !onboardingState.isError) return

    const parsed = parsePersistedDiscoveryResults(onboardingState.data?.discovery_results)
    if (parsed) {
      hydrateFromPersisted(parsed)
      return
    }

    const ac = new AbortController()
    void runDiscover(ac.signal)
    return () => ac.abort()
    // Intentionally omit ``discovery_results`` object identity: refetches swap references and
    // would abort an in-flight scan. ``persistedScanKey`` tracks when a completed snapshot exists.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deps intentionally narrowed; see comment above
  }, [
    onboardingState.isFetched,
    onboardingState.isError,
    persistedScanKey,
    hydrateFromPersisted,
    runDiscover,
  ])

  async function runRescan() {
    await runDiscover()
  }

  const waitingForSavedState =
    !onboardingState.isFetched && !onboardingState.isError && status === "idle"

  const canContinue = isSuccess && !isError

  /** Senders that actually have at least one matching message (rough estimate > 0). */
  const accountsFound = React.useMemo(
    () => rows.filter((r) => r.email_count_estimate > 0).length,
    [rows],
  )

  const progressPercent =
    total > 0 ? Math.min(100, Math.round((checked / total) * 100)) : undefined

  /** Groups scan rows into banks / demat / cards, then by institution (HDFC, ICICI, …). */
  const grouped = React.useMemo(() => groupDiscoveryRowsForUi(rows), [rows])

  /**
   * Which institution accordion panels are open (`"{category}-{institution}"`).
   * Multiple panels can be open at once so users can compare senders side by side.
   */
  const [openKeys, setOpenKeys] = React.useState<Set<string>>(() => new Set())

  function toggleInstitution(key: string) {
    setOpenKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const showResultsCard =
    (rows.length > 0 || (isSuccess && total === 0)) && !isError

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center gap-2">
        <Radar className="size-7 text-primary" aria-hidden />
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Discover sources</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            We scan your mailbox for known bank sender addresses. Each check is a single Gmail
            search (message IDs only) — we do not download full message bodies in this step.
          </p>
        </div>
      </div>

      {waitingForSavedState && (
        <div className="space-y-2" aria-live="polite">
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="size-4 animate-spin shrink-0" aria-hidden />
            Checking for a previous scan…
          </p>
          <Progress value={undefined} className="max-w-md" />
        </div>
      )}

      {isPending && status === "connecting" && (
        <div className="space-y-2" aria-live="polite">
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="size-4 animate-spin shrink-0" aria-hidden />
            Connecting to Gmail…
          </p>
          <Progress value={undefined} className="max-w-md" />
        </div>
      )}

      {isPending && status === "scanning" && (
        <div className="space-y-2" aria-live="polite">
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="size-4 animate-spin shrink-0" aria-hidden />
            {total > 0
              ? `Scanning sender ${checked} of ${total}…`
              : "Scanning your mailbox…"}
          </p>
          <Progress value={total > 0 ? progressPercent : undefined} className="max-w-md" />
        </div>
      )}

      {isSuccess && !isError && (
        <div className="space-y-1" aria-live="polite">
          <p className="text-sm text-muted-foreground">
            {total === 0
              ? "No bank senders are configured to scan yet."
              : `Done — found ${accountsFound} account${accountsFound === 1 ? "" : "s"} with mail across ${total} sender${total === 1 ? "" : "s"}.`}
          </p>
          {discoveredAt ? (
            <p className="text-xs text-muted-foreground">
              Last scanned {formatLastScannedLabel(discoveredAt)}. Use “Re-scan” if your mailbox
              changed since then.
            </p>
          ) : null}
        </div>
      )}

      {errorText && (
        <OnboardingErrorCallout
          title="We couldn’t finish this step"
          hint='Use the “Back” button at the bottom to return to “Connect Gmail,” sign in with Google, then come back here and tap “Re-scan.”'
        >
          {errorText}
        </OnboardingErrorCallout>
      )}

      {showResultsCard && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Accounts we found</CardTitle>
            <CardDescription>
              Expand a row to see individual sender addresses and
              approximate volumes.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-8 overflow-x-auto">
            {rows.length === 0 ? (
              <p className="text-sm text-muted-foreground py-2">No rows to show.</p>
            ) : (
              DISCOVERY_UI_SECTIONS.map((cat) => {
                const meta = discoveryCategoryMeta[cat]
                const institutions = grouped[cat]
                return (
                  <section key={cat} className="space-y-3" aria-labelledby={`discover-section-${cat}`}>
                    <h3 id={`discover-section-${cat}`} className="text-sm font-semibold tracking-tight">
                      {meta.title}
                    </h3>

                    {institutions.length === 0 ? (
                      <p className="text-sm text-muted-foreground border border-dashed rounded-lg px-3 py-4">
                        {meta.emptyHint}
                      </p>
                    ) : (
                      <ul className="space-y-2 list-none p-0 m-0">
                        {institutions.map((g) => {
                          const panelKey = `${cat}-${g.institution}`
                          const safe = discoveryPanelSafeFragment(cat, g.institution)
                          const headingId = `discover-h-${safe}`
                          const regionId = `discover-r-${safe}`
                          const open = openKeys.has(panelKey)
                          return (
                            <li
                              key={panelKey}
                              className={cn(
                                "rounded-lg border bg-card text-card-foreground motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-left-1 duration-300",
                              )}
                            >
                              <button
                                type="button"
                                onClick={() => toggleInstitution(panelKey)}
                                className="flex w-full items-center gap-2 px-3 py-3 text-left text-sm hover:bg-muted/40 rounded-lg transition-colors"
                                aria-expanded={open}
                                aria-controls={regionId}
                                id={headingId}
                              >
                                <ChevronDown
                                  className={cn(
                                    "size-4 shrink-0 text-muted-foreground transition-transform",
                                    open && "rotate-180",
                                  )}
                                  aria-hidden
                                />
                                <span className="font-medium">{g.institution}</span>
                                <span className="ml-auto tabular-nums text-muted-foreground">
                                  ≈ {g.totalMessages} msg{g.totalMessages === 1 ? "" : "s"}
                                </span>
                              </button>

                              {open ? (
                                <div
                                  id={regionId}
                                  role="region"
                                  aria-labelledby={headingId}
                                  className="border-t px-2 pb-3 pt-2 sm:px-3"
                                >
                                  <DiscoverySenderDetailTable rows={g.rows} />
                                </div>
                              ) : null}
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </section>
                )
              })
            )}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => void runRescan()} disabled={isPending}>
          Re-scan
        </Button>
        <Button type="button" onClick={() => onContinue()} disabled={!canContinue}>
          Continue
        </Button>
      </div>
    </div>
  )
}
