"use client"

/**
 * MobileBlocker — Arth is desktop-first (charts + stacked sheets need width).
 *
 * Below 1024px we show a static full-screen message instead of the app shell.
 * Listens to resize so rotating a tablet past the threshold updates the UI.
 */

import * as React from "react"

const BREAKPOINT_PX = 1024

export function MobileBlocker({ children }: { children: React.ReactNode }) {
  const [ok, setOk] = React.useState<boolean | null>(null)

  React.useEffect(() => {
    const check = () => setOk(window.innerWidth >= BREAKPOINT_PX)
    check()
    window.addEventListener("resize", check)
    return () => window.removeEventListener("resize", check)
  }, [])

  // Avoid flash: don't render children until we know viewport (client-only).
  if (ok === null) {
    return (
      <div className="flex h-full min-h-[200px] items-center justify-center bg-background text-muted-foreground text-sm">
        Loading…
      </div>
    )
  }

  if (!ok) {
    return (
      <div className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-4 bg-background px-6 text-center">
        <p className="text-2xl font-semibold tracking-tight text-foreground">
          Arth works best on a larger screen
        </p>
        <p className="max-w-md text-muted-foreground text-base leading-relaxed">
          Please open this dashboard on a desktop or laptop (or any display at least{" "}
          {BREAKPOINT_PX}px wide). Charts and drill-downs aren&apos;t practical on phones.
        </p>
      </div>
    )
  }

  return <>{children}</>
}
