/**
 * proxy.ts — Next.js 16 request interceptor (formerly middleware).
 *
 * Local Arth does not enforce sign-in at the edge; FastAPI treats every request as the
 * single installation user. Kept as a no-op pass-through so we can reintroduce checks later.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const DEMO_RAW = (process.env.NEXT_PUBLIC_DEMO_MODE ?? "").trim().toLowerCase();
const DEMO =
  DEMO_RAW === "1" ||
  DEMO_RAW === "true" ||
  DEMO_RAW === "yes" ||
  DEMO_RAW === "on";

export function proxy(request: NextRequest) {
  if (DEMO) {
    const path = request.nextUrl.pathname;
    if (path === "/") {
      return NextResponse.redirect(new URL("/welcome", request.url));
    }
    if (path === "/login" || path === "/setup") {
      return NextResponse.redirect(new URL("/chat", request.url));
    }
  }
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!api-backend|_next/static|_next/image|favicon.ico).*)",
  ],
};
