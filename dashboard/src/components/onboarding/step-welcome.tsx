"use client"

/**
 * Step 1 — Welcome + connect Gmail (Track 2 Phase 5a).
 *
 * Gmail tokens live on your machine with the API; this button starts the same
 * browser sign-in flow as before. After Google finishes, return here and tap continue.
 */

import * as React from "react"

import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { buildApiUrl } from "@/lib/api-base"
import { userMessageFromApiResponseBody } from "@/lib/user-facing-api-error"

export type StepWelcomeProps = {
  /** Fires once the user confirms Gmail is connected (they clicked Continue). */
  onContinue: () => void
}

/** How fast the “Connecting…” ellipsis cycles (3 → 2 → 1 dot). */
const CONNECTING_DOT_CYCLE_MS = 450

export function StepWelcome({ onContinue }: StepWelcomeProps) {
  const AUTO_POLL_INTERVAL_MS = 2000
  const AUTO_POLL_TIMEOUT_MS = 120000
  const [error, setError] = React.useState<string | null>(null)
  const [busyConnect, setBusyConnect] = React.useState(false)
  const [busyContinue, setBusyContinue] = React.useState(false)
  /** True after OAuth init succeeds until we advance, or until the poll window times out. */
  const [oauthStarted, setOauthStarted] = React.useState(false)
  /** Drives the primary button spinner while the auto-poll interval is active. */
  const [pollingForOAuth, setPollingForOAuth] = React.useState(false)
  /**
   * Cycles 3 → 2 → 1 dot after "Connecting" while polling (visual “typing” ellipsis).
   * While ``pollingForOAuth`` is false we **derive** a display count of 3 so we never reset this
   * state inside an effect (see ``react-hooks/set-state-in-effect``). When OAuth starts, we set
   * the count to 3 in the click handler together with ``setPollingForOAuth(true)``.
   */
  const [connectingDotCount, setConnectingDotCount] = React.useState<1 | 2 | 3>(3)
  const hasAutoAdvancedRef = React.useRef(false)
  /** When we are not polling, always show a full ellipsis — avoids setState in an effect. */
  const displayDotCount = pollingForOAuth ? connectingDotCount : 3

  React.useEffect(() => {
    if (!pollingForOAuth) return
    const id = window.setInterval(() => {
      setConnectingDotCount((n) => (n === 1 ? 3 : ((n - 1) as 1 | 2 | 3)))
    }, CONNECTING_DOT_CYCLE_MS)
    return () => window.clearInterval(id)
  }, [pollingForOAuth])

  /**
   * Shared status checker used by both the manual Continue button and
   * automatic checks (focus / visibility / short polling).
   */
  const checkOauthStatusAndContinue = React.useCallback(
    async function ({
      showBusy = false,
      showNotConnectedError = false,
    }: {
      showBusy?: boolean
      showNotConnectedError?: boolean
    } = {}): Promise<boolean> {
      if (hasAutoAdvancedRef.current) {
        return true
      }
      if (showBusy) setBusyContinue(true)
      try {
        const res = await fetch(buildApiUrl("/api/scraper/oauth/status"), {
          credentials: "include",
        })
        const t = await res.text()
        if (!res.ok) {
          if (showNotConnectedError) {
            setError(
              userMessageFromApiResponseBody(t) ||
                "We could not confirm your Gmail connection. Please try again.",
            )
          }
          return false
        }

        const payload = (JSON.parse(t || "{}") ?? {}) as {
          is_authenticated?: boolean
        }
        if (!payload.is_authenticated) {
          if (showNotConnectedError) {
            setError(
              "Gmail is not connected yet. Click “Connect Gmail”, finish the Google sign-in in your browser, then tap continue.",
            )
          }
          return false
        }

        hasAutoAdvancedRef.current = true
        setPollingForOAuth(false)
        setOauthStarted(false)
        onContinue()
        return true
      } catch {
        if (showNotConnectedError) {
          setError("We could not check Gmail right now. Please try again.")
        }
        return false
      } finally {
        if (showBusy) setBusyContinue(false)
      }
    },
    [onContinue],
  )

  async function startOAuth() {
    setError(null)
    setBusyConnect(true)
    try {
      const res = await fetch(buildApiUrl("/api/scraper/oauth/init"), {
        method: "POST",
        credentials: "include",
      })
      const t = await res.text()
      if (!res.ok) {
        setError(userMessageFromApiResponseBody(t) || "Couldn't start sign-in. Try again.")
        return
      }
      const payload = (JSON.parse(t || "{}") ?? {}) as {
        status?: string
        auth_url?: string
      }
      if (payload.auth_url) {
        window.open(payload.auth_url, "_blank", "noopener,noreferrer")
      }
      // Start lightweight auto-checks after OAuth launch; primary button shows poll spinner.
      setConnectingDotCount(3)
      setPollingForOAuth(true)
      setOauthStarted(true)
    } catch {
      setError("We could not reach Arth. Make sure the app is running, then try again.")
    } finally {
      setBusyConnect(false)
    }
  }

  async function continueIfConnected() {
    setError(null)
    await checkOauthStatusAndContinue({
      showBusy: true,
      showNotConnectedError: true,
    })
  }

  React.useEffect(() => {
    if (!oauthStarted) return

    // Poll briefly while user is in/out of the browser OAuth flow.
    const timer = window.setInterval(() => {
      void checkOauthStatusAndContinue()
    }, AUTO_POLL_INTERVAL_MS)

    // Hard stop: no more polling, spinner off, primary button back to normal "Connect Gmail".
    const timeout = window.setTimeout(() => {
      window.clearInterval(timer)
      setPollingForOAuth(false)
      setOauthStarted(false)
    }, AUTO_POLL_TIMEOUT_MS)

    // Also re-check immediately when tab becomes active again.
    const onFocus = () => void checkOauthStatusAndContinue()
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void checkOauthStatusAndContinue()
      }
    }

    window.addEventListener("focus", onFocus)
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => {
      window.clearInterval(timer)
      window.clearTimeout(timeout)
      window.removeEventListener("focus", onFocus)
      document.removeEventListener("visibilitychange", onVisibilityChange)
    }
  }, [oauthStarted, AUTO_POLL_INTERVAL_MS, AUTO_POLL_TIMEOUT_MS, checkOauthStatusAndContinue])

  return (
    <Card className="max-w-lg border-muted">
      <CardHeader>
        <CardTitle>Connect Gmail</CardTitle>
        <CardDescription>
          Arth reads <strong>bank alert emails</strong> you already get (HDFC, ICICI, etc.) to build
          your ledger. Your data stays on this computer — we do not send your mail to analytics
          services. Tap the button below, then complete Google sign-in in the new tab and return here.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <Button
          type="button"
          className="w-full"
          disabled={busyConnect || busyContinue || pollingForOAuth}
          aria-busy={busyConnect || pollingForOAuth}
          aria-label={
            busyConnect
              ? "Starting Google sign-in"
              : pollingForOAuth
                ? "Connecting to Gmail, waiting for sign-in to finish"
                : "Connect Gmail"
          }
          onClick={() => void startOAuth()}
        >
          {busyConnect ? (
            "Starting…"
          ) : pollingForOAuth ? (
            <span className="inline-flex items-center justify-center gap-2">
              <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
              <span>
                Connecting
                {/* Fixed width so “...” → “.” does not resize the button */}
                <span className="inline-block min-w-[3ch] text-left font-mono" aria-hidden>
                  {".".repeat(displayDotCount)}
                </span>
              </span>
            </span>
          ) : (
            "Connect Gmail"
          )}
        </Button>
        <p className="text-xs text-muted-foreground">
          After you tap Allow in Google, return to this tab — we detect the connection automatically.
        </p>
        <Button
          type="button"
          variant="secondary"
          className="w-full"
          disabled={busyConnect || busyContinue}
          onClick={() => void continueIfConnected()}
        >
          {busyContinue ? "Checking Gmail…" : "I already connected Gmail — continue"}
        </Button>
      </CardContent>
    </Card>
  )
}
