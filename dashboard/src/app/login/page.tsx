"use client";

/**
 * Legacy /login route — immediately sends you back into the app.
 *
 * Older builds required a password here; local installs no longer gate on credentials.
 */

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Skeleton } from "@/components/ui/skeleton";

function LoginRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("from") ?? "/";

  useEffect(() => {
    router.replace(redirectTo);
  }, [router, redirectTo]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
      <p className="text-sm text-muted-foreground">Opening Arth…</p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
          <Skeleton className="h-6 w-40" />
        </div>
      }
    >
      <LoginRedirectInner />
    </Suspense>
  );
}
