"use client";

/**
 * Demo-only: send first-time visitors to ``/welcome`` from any route (not just ``/``).
 *
 * Always renders children immediately (matching server output — no SSR mismatch).
 * The redirect happens in a useEffect on the client, following the same pattern as
 * SetupGate. This avoids a hydration mismatch that was resetting the chat
 * session-not-found circuit breaker counter on every page load.
 */

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";

import { isDemoMode } from "@/lib/demo";
import { hasSeenDemoWelcome } from "@/lib/demo-welcome";

function pathIsExempt(pathname: string | null): boolean {
  if (!pathname) return true;
  if (pathname === "/welcome") return true;
  if (pathname.startsWith("/_next")) return true;
  return false;
}

export function DemoWelcomeGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  React.useEffect(() => {
    if (!isDemoMode) return;
    if (pathIsExempt(pathname)) return;
    if (!hasSeenDemoWelcome()) {
      router.replace("/welcome");
    }
  }, [pathname, router]);

  return <>{children}</>;
}
