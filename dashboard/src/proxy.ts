/**
 * proxy.ts — Next.js 16 request interceptor for authentication.
 *
 * In Next.js 16, middleware.ts was renamed to proxy.ts and the export
 * function was renamed from `middleware` to `proxy`. The API surface
 * (NextRequest, NextResponse, config matcher) is identical.
 *
 * What this does:
 *   - Runs on every page request matched by `config.matcher`
 *   - Checks for the "arth_session" cookie (set by FastAPI on successful login)
 *   - If the cookie is MISSING → redirect to /login
 *   - If the cookie is PRESENT → allow the request through
 *
 * What this does NOT do:
 *   - Validate the token cryptographically (that's FastAPI's job)
 *   - Read the cookie contents (it's httpOnly — JS can't see the value anyway)
 *   - Block the /login page itself (see matcher below)
 *   - Block /api-backend/* — proxied FastAPI calls (login must reach the server
 *     without a session cookie; FastAPI validates credentials)
 *
 * The proxy does a "presence check" only. The real validation happens when
 * the page makes its first API call to FastAPI — if the token is expired or
 * tampered with, FastAPI returns 401 and api.ts redirects to /login.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const COOKIE_NAME = "arth_session";

export function proxy(request: NextRequest) {
  const session = request.cookies.get(COOKIE_NAME);

  if (!session) {
    // Not logged in — redirect to /login, preserving the original destination
    // so the login page can send the user back after successful login.
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  // Session cookie exists — let the request through.
  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all routes EXCEPT:
     *   - /login              — the login page itself (avoid redirect loop)
     *   - /api-backend        — reverse proxy to FastAPI (same-origin tunnel mode)
     *   - /_next/static       — Next.js static assets (JS, CSS)
     *   - /_next/image        — Next.js image optimisation
     *   - /favicon.ico        — browser favicon request
     *
     * This regex reads as: match everything that does NOT start with
     * login, api-backend, _next/static, _next/image, or favicon.ico.
     */
    "/((?!login|api-backend|_next/static|_next/image|favicon.ico).*)",
  ],
};
