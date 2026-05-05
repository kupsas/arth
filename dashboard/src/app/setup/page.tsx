"use client";

/**
 * First-run onboarding wizard — Gmail discovery, identity, imports, goals.
 *
 * A default ``local`` user row is created when the API starts; there is no separate registration step.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { OnboardingWizard } from "@/components/onboarding/onboarding-wizard";
import {
  completeSetupWizard,
  fetchSetupStatus,
  SETUP_STATUS_QUERY_KEY,
} from "@/lib/api";

export default function SetupPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await fetchSetupStatus();
        if (cancelled) return;
        if (status.setup_completed) {
          router.replace("/");
          return;
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Couldn't load setup. Try refreshing?");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  React.useEffect(() => {
    const html = document.documentElement;
    const main = document.querySelector("main") as HTMLElement | null;

    const prev = {
      htmlOverflow: html.style.overflow,
      htmlOsb: html.style.overscrollBehavior,
      bodyOverflow: document.body.style.overflow,
      bodyOsb: document.body.style.overscrollBehavior,
      mainOverflow: main?.style.overflow ?? "",
      mainOsb: main?.style.overscrollBehavior ?? "",
    };

    html.style.overflow = "hidden";
    html.style.overscrollBehavior = "none";
    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "none";
    if (main) {
      main.style.overflow = "hidden";
      main.style.overscrollBehavior = "none";
    }

    return () => {
      html.style.overflow = prev.htmlOverflow;
      html.style.overscrollBehavior = prev.htmlOsb;
      document.body.style.overflow = prev.bodyOverflow;
      document.body.style.overscrollBehavior = prev.bodyOsb;
      if (main) {
        main.style.overflow = prev.mainOverflow;
        main.style.overscrollBehavior = prev.mainOsb;
      }
    };
  }, []);

  async function onWizardFinished() {
    setError(null);
    try {
      await completeSetupWizard();
      await queryClient.invalidateQueries({ queryKey: [...SETUP_STATUS_QUERY_KEY] });
      router.replace("/");
      router.refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Couldn't finish setup. Try again?");
    }
  }

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">Loading setup…</p>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto overscroll-none bg-background py-8 px-4">
      <div className="w-full max-w-4xl rounded-xl border bg-card p-6 sm:p-10 shadow-sm">
        {error && (
          <p className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}
        <OnboardingWizard mode="setup" onFinished={() => void onWizardFinished()} />
      </div>
    </div>
  );
}
