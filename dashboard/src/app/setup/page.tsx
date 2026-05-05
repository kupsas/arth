"use client";

/**
 * First-run setup (DESKTOP_PREREQS item 3) + Track 2 onboarding wizard (Phase 5b).
 *
 * Flow:
 *   1. If the SQLite DB has **no** users yet → simple registration form.
 *   2. If users exist but the browser has **no** session → nudge to ``/login``.
 *   3. Once authenticated, mount ``OnboardingWizard`` — Gmail discovery, identity,
 *      chunk backfill, inline classification pauses, gap detection, goals, and completion.
 *
 *   (PDF statement passwords: deferred — were an optional pre-wizard form; use ``.env`` or
 *   we can add a Settings/late step later. See removed ``saveSetupSecrets`` + step-2 block
 *   in git history if we restore an inline JSON editor here.)
 *
 * The wizard itself lives in ``src/components/onboarding/onboarding-wizard.tsx`` so we
 * can reuse it from Settings → **Connect account** without duplicating logic.
 */

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { OnboardingWizard } from "@/components/onboarding/onboarding-wizard";
import { Button } from "@/components/ui/button";
import {
  completeSetupWizard,
  fetchSetupStatus,
  registerFirstUser,
  SETUP_STATUS_QUERY_KEY,
} from "@/lib/api";
import { buildApiUrl } from "@/lib/api-base";

async function authMeNoRedirect(): Promise<{ authenticated: boolean; username?: string | null }> {
  const res = await fetch(buildApiUrl("/api/auth/me"), { credentials: "include" });
  if (res.status === 401) {
    return { authenticated: false, username: null };
  }
  if (!res.ok) {
    return { authenticated: false, username: null };
  }
  return res.json() as Promise<{ authenticated: boolean; username?: string | null }>;
}

export default function SetupPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [loading, setLoading] = React.useState(true);
  const [step, setStep] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);

  const [regUser, setRegUser] = React.useState("");
  const [regPw, setRegPw] = React.useState("");

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [status, auth] = await Promise.all([
          fetchSetupStatus(),
          authMeNoRedirect(),
        ]);
        if (cancelled) return;
        if (status.setup_completed && auth.authenticated) {
          router.replace("/");
          return;
        }
        if (!status.has_users) {
          setStep(0);
        } else if (!auth.authenticated) {
          setStep(1);
        } else {
          // Skip optional PDF-password screen — go straight to the onboarding wizard.
          setStep(3);
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

  // Full-screen setup: lock every scroll container in the shell for the whole time this page is mounted.
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

  async function onRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await registerFirstUser(regUser.trim(), regPw);
      setStep(1);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Couldn't create that account. Try again?");
    }
  }

  async function onWizardFinished() {
    setError(null);
    try {
      await completeSetupWizard();
      // So SetupGate on ``/`` sees ``needs_setup: false`` instead of stale cache.
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
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto overscroll-none bg-background py-8 px-4"
    >
      <div className="w-full max-w-4xl rounded-xl border bg-card p-6 sm:p-10 shadow-sm">
        {step < 3 && (
          <>
            <h1 className="text-xl font-semibold">Welcome to Arth</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Local-first setup — your data stays on this machine.
            </p>
          </>
        )}

        {error && (
          <p className="mt-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {step === 0 && (
          <form onSubmit={onRegister} className="mt-6 space-y-4 max-w-lg">
            <h2 className="text-sm font-medium">Create your account</h2>
            <input
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              placeholder="Username"
              value={regUser}
              onChange={(e) => setRegUser(e.target.value)}
              required
            />
            <input
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              type="password"
              placeholder="Password"
              value={regPw}
              onChange={(e) => setRegPw(e.target.value)}
              required
            />
            <Button type="submit" className="w-full">
              Register
            </Button>
          </form>
        )}

        {step === 1 && (
          <div className="mt-6 space-y-4 max-w-lg">
            <p className="text-sm text-muted-foreground">
              Sign in with the account you just created (or an existing one).
            </p>
            <Button
              className="w-full"
              variant="secondary"
              onClick={() => router.push("/login?from=/setup")}
            >
              Go to sign in
            </Button>
          </div>
        )}

        {/*
          PDF passwords (optional) — disabled for now. When we reintroduce:
          - add secretsJson state + saveSetupSecrets from @/lib/api
          - on load, use setStep(2) for authenticated users; render form; Skip/Save → setStep(3)
          - pass onExitFirstStep={() => setStep(2)} on OnboardingWizard so "Back" from step 1 works
        */}

        {step === 3 && (
          <div className="mt-4">
            <OnboardingWizard
              mode="setup"
              onFinished={() => void onWizardFinished()}
            />
          </div>
        )}
      </div>
    </div>
  );
}
