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

/**
 * Direct WebSocket origin for the FastAPI server.
 *
 * Next.js Route Handlers cannot proxy WebSocket upgrades, so when the dashboard
 * runs in same-origin mode the WS must bypass the ``/api-backend`` proxy and
 * connect straight to FastAPI.
 *
 * Set ``NEXT_PUBLIC_WS_URL`` to the FastAPI ws(s) origin when same-origin is
 * active (defaults to ``ws://127.0.0.1:8000`` for local dev).
 */
const WS_DIRECT =
  (process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000").trim().replace(/\/$/, "");

/**
 * WebSocket URL for the Arth agent chat endpoint.
 *
 * When ``NEXT_PUBLIC_API_URL`` points directly at FastAPI (e.g.
 * ``http://127.0.0.1:8000``) the WS URL is derived from that origin.
 *
 * In same-origin mode the HTTP proxy works but WebSocket upgrades do not, so
 * the connection goes directly to FastAPI via ``NEXT_PUBLIC_WS_URL``.
 */
export function buildChatWebSocketUrl(
  sessionId?: string | null,
  ticket?: string | null,
  arthDemoSid?: string | null,
): string {
  const params = new URLSearchParams();
  if (sessionId?.trim()) params.set("session_id", sessionId.trim());
  if (ticket?.trim()) params.set("ticket", ticket.trim());
  if (arthDemoSid?.trim()) params.set("arth_demo_sid", arthDemoSid.trim());
  const qs = params.toString() ? `?${params.toString()}` : "";
  const path = `/api/chat/ws${qs}`;

  if (typeof window === "undefined") {
    return `ws://127.0.0.1:8000${path}`;
  }

  if (apiViaSameOrigin) {
    return `${WS_DIRECT}${path}`;
  }

  if (API_BASE.startsWith("http")) {
    const wsRoot = API_BASE.replace(/^http/, "ws");
    return `${wsRoot}${path}`;
  }

  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${API_BASE}${path}`;
}
