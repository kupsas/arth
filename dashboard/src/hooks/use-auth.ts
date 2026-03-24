/**
 * use-auth.ts — session identity for the logged-in user.
 *
 * The API stores the username in an httpOnly cookie, so the browser cannot read it
 * directly. GET /api/auth/me tells us the username, which we reuse as ``user_id``
 * on portfolio endpoints (holdings, investment txns, liabilities) so two users
 * never see each other's asset layer data.
 */

"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchAuthMe } from "@/lib/api";

export const authKeys = {
  me: ["auth", "me"] as const,
};

export function useAuthMe() {
  return useQuery({
    queryKey: authKeys.me,
    queryFn: fetchAuthMe,
    staleTime: 5 * 60_000,
  });
}
