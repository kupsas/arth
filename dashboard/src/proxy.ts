/**
 * proxy.ts — Next.js 16 request interceptor (formerly middleware).
 *
 * Local Arth does not enforce sign-in at the edge; FastAPI treats every request as the
 * single installation user. Kept as a no-op pass-through so we can reintroduce checks later.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function proxy(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!api-backend|_next/static|_next/image|favicon.ico).*)",
  ],
};
