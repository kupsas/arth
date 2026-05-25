"use client";

/**
 * First-run gate: if the installation has not finished setup (no user with
 * ``setup_completed_at``), keep users on ``/setup`` instead of an empty dashboard.
 *
 * Uses the public ``GET /api/setup/status`` (same signal as the setup page).
 * Paths ``/setup`` and ``/login`` are exempt so registration and sign-in work.
 */

import * as React from "react";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { SETUP_STATUS_QUERY_KEY, fetchSetupStatus } from "@/lib/api";

function pathIsExempt(pathname: string | null): boolean {
  if (!pathname) return true;
  if (pathname === "/setup" || pathname === "/login" || pathname === "/welcome")
    return true;
  if (pathname.startsWith("/_next")) return true;
  return false;
}

export function SetupGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();

  const { data: status, isLoading, isError } = useQuery({
    queryKey: SETUP_STATUS_QUERY_KEY,
    queryFn: fetchSetupStatus,
    staleTime: 60_000,
    retry: 1,
  });

  React.useEffect(() => {
    if (isLoading || isError || !status) return;
    if (pathIsExempt(pathname)) return;
    if (status.needs_setup) {
      router.replace("/setup");
    }
  }, [pathname, router, status, isLoading, isError]);

  if (pathIsExempt(pathname)) {
    return <>{children}</>;
  }

  // If the API is unreachable, do not trap the user — show the app and let pages surface errors.
  if (isError) {
    return <>{children}</>;
  }

  // Do not flash the main shell until we know whether setup is required.
  if (isLoading || !status) {
    return (
      <div className="flex h-full min-h-[40vh] items-center justify-center">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  if (status.needs_setup) {
    return (
      <div className="flex h-full min-h-[40vh] items-center justify-center">
        <p className="text-sm text-muted-foreground">Opening setup…</p>
      </div>
    );
  }

  return <>{children}</>;
}
