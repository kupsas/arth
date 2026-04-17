"use client";

/**
 * First-run setup wizard (DESKTOP_PREREQS item 3).
 *
 * Flow: register (if no users) → sign in → optional PDF secrets → Gmail OAuth → finish.
 * Bank sender mappings are seeded from the server on first DB init; edit via
 * GET/POST /api/scraper-config or future settings UI.
 */

import * as React from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import {
  completeSetupWizard,
  fetchSetupStatus,
  registerFirstUser,
  saveSetupSecrets,
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
  const [loading, setLoading] = React.useState(true);
  const [step, setStep] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);

  const [regUser, setRegUser] = React.useState("");
  const [regPw, setRegPw] = React.useState("");
  const [secretsJson, setSecretsJson] = React.useState(
    '{\n  "HDFC_STATEMENT_PASSWORD": ""\n}',
  );

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
          setStep(2);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load setup status");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await registerFirstUser(regUser.trim(), regPw);
      setStep(1);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  }

  async function onSaveSecrets(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const parsed = JSON.parse(secretsJson) as Record<string, string>;
      await saveSetupSecrets(parsed);
      setStep(3);
    } catch {
      setError("Secrets must be valid JSON object mapping env key → password string.");
    }
  }

  async function onOAuth() {
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/api/scraper/oauth/init"), {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError((body as { detail?: string }).detail ?? "OAuth init failed");
        return;
      }
      setStep(4);
    } catch {
      setError("Could not reach the API. Is it running?");
    }
  }

  async function onFinish() {
    setError(null);
    try {
      await completeSetupWizard();
      router.replace("/");
      router.refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not complete setup");
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
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-background p-4">
      <div className="w-full max-w-lg rounded-xl border bg-card p-8 shadow-sm">
        <h1 className="text-xl font-semibold">Welcome to Arth</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Local-first setup — your data stays on this machine.
        </p>

        {error && (
          <p className="mt-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {step === 0 && (
          <form onSubmit={onRegister} className="mt-6 space-y-4">
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
          <div className="mt-6 space-y-4">
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

        {step === 2 && (
          <form onSubmit={onSaveSecrets} className="mt-6 space-y-4">
            <h2 className="text-sm font-medium">PDF passwords (optional)</h2>
            <p className="text-xs text-muted-foreground">
              JSON object whose keys match <code className="rounded bg-muted px-1">.env</code> names
              (e.g. HDFC_STATEMENT_PASSWORD). You can skip and rely on{" "}
              <code className="rounded bg-muted px-1">.env</code> instead.
            </p>
            <textarea
              className="min-h-[120px] w-full rounded-md border bg-background p-2 font-mono text-xs"
              value={secretsJson}
              onChange={(e) => setSecretsJson(e.target.value)}
            />
            <div className="flex gap-2">
              <Button type="button" variant="ghost" onClick={() => setStep(3)}>
                Skip
              </Button>
              <Button type="submit" className="flex-1">
                Save & continue
              </Button>
            </div>
          </form>
        )}

        {step === 3 && (
          <div className="mt-6 space-y-4">
            <h2 className="text-sm font-medium">Connect Gmail</h2>
            <p className="text-xs text-muted-foreground">
              Starts the Google OAuth flow on the API server (browser may open). Requires{" "}
              <code className="rounded bg-muted px-1">data/gmail_credentials.json</code>.
            </p>
            <Button className="w-full" type="button" onClick={onOAuth}>
              Start Gmail OAuth
            </Button>
            <Button variant="ghost" className="w-full" type="button" onClick={() => setStep(4)}>
              Skip (configure later)
            </Button>
          </div>
        )}

        {step === 4 && (
          <div className="mt-6 space-y-4">
            <p className="text-sm text-muted-foreground">
              You can change bank senders and account mappings via{" "}
              <code className="rounded bg-muted px-1">/api/scraper-config</code> or SQLite.
            </p>
            <Button className="w-full" onClick={onFinish}>
              Finish setup
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
