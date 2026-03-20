/**
 * Resolves where browser-side fetch() should send FastAPI requests.
 *
 * Default: direct to http://localhost:8000 (NEXT_PUBLIC_API_URL unset).
 *
 * Cloudflare / two-hostname problem:
 *   If the UI is https://app.trycloudflare.com and the API is
 *   https://api.trycloudflare.com, login sets `arth_session` for the *API* host.
 *   Next.js middleware on the *dashboard* host never sees that cookie → instant
 *   redirect back to /login (looks like "page reload").
 *
 * Fix: set NEXT_PUBLIC_API_URL=same-origin in .env.local. All API calls then go
 * to /api-backend/... on the dashboard origin; the Route Handler in
 * app/api-backend proxies to localhost:8000, so Set-Cookie is stored for the UI host.
 */

const raw = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").trim();

/** True when requests are proxied via Next (same site as the dashboard). */
export const apiViaSameOrigin = raw === "same-origin";

/**
 * Either full origin (e.g. http://localhost:8000) or path prefix (/api-backend).
 * No trailing slash.
 */
export const API_BASE = apiViaSameOrigin
  ? "/api-backend"
  : raw.replace(/\/$/, "");

type QueryParams = Record<string, string | number | boolean | undefined | null>;

/**
 * Absolute URL string for fetch(), including query string when params given.
 * Safe for use in client components (uses window.location.origin for relative bases).
 */
export function buildApiUrl(path: string, params?: QueryParams): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  const origin =
    typeof window !== "undefined"
      ? window.location.origin
      : "http://localhost:3000";

  let u: URL;
  if (API_BASE.startsWith("http")) {
    u = new URL(`${API_BASE}${p}`);
  } else {
    u = new URL(`${API_BASE}${p}`, origin);
  }

  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        u.searchParams.set(key, String(value));
      }
    }
  }

  return u.toString();
}
