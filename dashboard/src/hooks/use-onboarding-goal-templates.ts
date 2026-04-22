/**
 * useOnboardingGoalTemplates — GET /api/onboarding/goal-templates (inflation + defaults, Phase 4c).
 */
"use client"

import { useQuery, type UseQueryOptions } from "@tanstack/react-query"
import { fetchOnboardingGoalTemplates } from "@/lib/api"
import type { OnboardingGoalTemplatesResponse } from "@/lib/types"

export function makeGoalTemplatesKey(params: {
  target_amount?: number
  years?: number
  template_id?: string
}) {
  return ["onboarding", "goal-templates", params] as const
}

export function useOnboardingGoalTemplates(
  params: { target_amount?: number; years?: number; template_id?: string } = {},
  options?: Partial<UseQueryOptions<OnboardingGoalTemplatesResponse>>,
) {
  return useQuery<OnboardingGoalTemplatesResponse>({
    queryKey: makeGoalTemplatesKey(params),
    queryFn: () => fetchOnboardingGoalTemplates(params),
    staleTime: 60_000,
    ...options,
  })
}
