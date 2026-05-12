"use client";

/**
 * Thin strip at the top of the demo deployment explaining sample data,
 * showing remaining Ask Arth messages, and offering a one-click DB reset.
 *
 * Message count comes from localStorage (30-minute sliding window) so the
 * rate limit survives DB resets — resetting data should not grant free messages.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw, Sparkles } from "lucide-react";

import { fetchDemoStatus, postDemoReset } from "@/lib/api";
import { isDemoMode } from "@/lib/demo";
import {
  DEMO_RATE_LIMIT_MAX,
  formatCountdown,
  getDemoRateLimitState,
  type DemoRateLimitState,
} from "@/lib/demo-rate-limit";
import { Button } from "@/components/ui/button";
import posthog from "posthog-js";

/** Poll rate-limit state from localStorage; ticks every second when limited, every 5s otherwise. */
function useDemoRateLimit(): DemoRateLimitState {
  const [state, setState] = useState<DemoRateLimitState>(() => getDemoRateLimitState());

  useEffect(() => {
    const tick = () => setState(getDemoRateLimitState());
    const interval = setInterval(tick, state.isLimited ? 1_000 : 5_000);
    return () => clearInterval(interval);
  }, [state.isLimited]);

  return state;
}

export function DemoBanner() {
  const qc = useQueryClient();
  const rateLimit = useDemoRateLimit();

  const { data, isError } = useQuery({
    queryKey: ["demo-status"],
    queryFn: fetchDemoStatus,
    enabled: isDemoMode,
    refetchInterval: 60_000,
    staleTime: 15_000,
  });

  const reset = useMutation({
    mutationFn: postDemoReset,
    onSuccess: async () => {
      // Wipe every cached API slice so charts/tables re-fetch from the fresh SQLite copy.
      // Intentionally does NOT clear the localStorage rate-limit window — the 30-minute
      // clock keeps ticking so resetting data cannot be used to bypass the chat limit.
      await qc.invalidateQueries();
      if (typeof window !== "undefined") {
        window.location.reload();
      }
    },
  });

  if (!isDemoMode) return null;

  const seedOk = data?.seed_exists !== false;

  return (
    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-950 dark:border-amber-400/25 dark:bg-amber-400/10 dark:text-amber-50">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <Sparkles className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300" aria-hidden />
        <p className="min-w-0 leading-snug">
          <span className="font-medium">Demo mode</span>
          {" — "}
          you&apos;re exploring sample data. Use the button on the right to reset data in the demo (chat has a 15 message limit per 30 minutes window)
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {/* Rate-limit display */}
        {rateLimit.isLimited ? (
          <span className="hidden whitespace-nowrap text-xs sm:inline">
            <span className="font-medium text-destructive">Message limit reached.</span>{" "}
            <span className="text-muted-foreground">
              Try again in{" "}
              <span className="font-mono font-semibold text-foreground">
                {formatCountdown(rateLimit.msUntilReset)}
              </span>
            </span>
          </span>
        ) : (
          <span className="hidden whitespace-nowrap text-xs text-muted-foreground sm:inline">
            Ask Arth messages left:{" "}
            <span className="font-mono font-semibold text-foreground">
              {rateLimit.remaining} / {DEMO_RATE_LIMIT_MAX}
            </span>
          </span>
        )}

        {!seedOk && (
          <span className="text-xs font-medium text-destructive">Seed DB missing on server</span>
        )}
        {isError && (
          <span className="text-xs text-destructive">Could not reach demo API</span>
        )}

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 border-amber-600/40 bg-background/80 hover:bg-background"
          disabled={reset.isPending || !seedOk}
          title="Deletes your temporary copy of the sample database and downloads a fresh clone. Resets all your data changes but the 30-minute chat window keeps ticking."
          onClick={() => {
            posthog.capture("demo_reset");
            reset.mutate();
          }}
        >
          {reset.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          )}
          <span className="ml-1.5 hidden sm:inline">Reset demo</span>
        </Button>
      </div>
    </div>
  );
}
