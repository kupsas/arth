/**
 * useOnboardingGaps — GET /api/onboarding/gaps for coverage gap review (Track 2 Phase 4a).
 */
"use client"

import { useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query"
import { fetchOnboardingGaps } from "@/lib/api"
import type { OnboardingGapsResponse } from "@/lib/types"

export const onboardingGapsKey = ["onboarding", "gaps"] as const

export function useOnboardingGaps(
  options?: Partial<UseQueryOptions<OnboardingGapsResponse>>,
) {
  return useQuery<OnboardingGapsResponse>({
    queryKey: [...onboardingGapsKey],
    queryFn: () => fetchOnboardingGaps(),
    staleTime: 30_000,
    ...options,
  })
}

export function useInvalidateOnboardingGaps() {
  const q = useQueryClient()
  return () => void q.invalidateQueries({ queryKey: [...onboardingGapsKey] })
}
