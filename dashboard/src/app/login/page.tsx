"use client";

/**
 * Login page — the only page accessible without a session cookie.
 *
 * On submit: POSTs to FastAPI POST /api/auth/login.
 * On success: FastAPI sets the httpOnly "arth_session" cookie, then we
 *             redirect to the original destination (the `from` query param)
 *             or to "/" if none was set.
 * On failure: shows an inline error message.
 *
 * The page intentionally has NO sidebar/header — it uses its own full-screen
 * layout. The root layout.tsx wraps all pages, so we need to opt out of the
 * sidebar shell. We do this by returning early from the layout via Next.js's
 * route segment config (see layout note below), but the simplest approach for
 * a single page is to style it as a full-screen overlay using fixed positioning.
 */

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { buildApiUrl } from "@/lib/api-base";

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("from") ?? "/";

  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch(buildApiUrl("/api/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",   // needed so FastAPI's Set-Cookie is accepted
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? "Login failed — check your credentials.");
        return;
      }

      // Cookie is now set by FastAPI. Navigate to the original destination.
      router.push(redirectTo);
      router.refresh();  // flush the Next.js client-side router cache
    } catch {
      setError("Could not reach the API server. Is it running on port 8000?");
    } finally {
      setLoading(false);
    }
  }

  return (
    /*
     * Full-screen overlay sitting on top of the root layout's sidebar shell.
     * Using fixed + inset-0 means it covers the entire viewport regardless of
     * whatever the layout renders underneath.
     */
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
      <div className="w-full max-w-sm px-4">

        {/* Logo / branding */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Arth</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Personal Finance Dashboard
          </p>
        </div>

        {/* Login card */}
        <div className="rounded-xl border bg-card p-8 shadow-sm">
          <h2 className="mb-6 text-lg font-medium">Sign in</h2>

          <form onSubmit={handleSubmit} className="space-y-4">

            <div className="space-y-1.5">
              <label
                htmlFor="username"
                className="block text-sm font-medium text-foreground"
              >
                Username
              </label>
              <input
                id="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm
                           placeholder:text-muted-foreground
                           focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="sashank"
              />
            </div>

            <div className="space-y-1.5">
              <label
                htmlFor="password"
                className="block text-sm font-medium text-foreground"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm
                           placeholder:text-muted-foreground
                           focus:outline-none focus:ring-2 focus:ring-ring"
                placeholder="••••••••"
              />
            </div>

            {/* Error message */}
            {error && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium
                         text-primary-foreground transition-opacity
                         hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>

      </div>
    </div>
  );
}
