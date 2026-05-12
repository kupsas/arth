"use client";

/**
 * Providers wraps the entire app in all the "context providers" it needs.
 *
 * What is a context provider?
 *   React "context" lets you share data/state with every component in your
 *   tree without prop-drilling.  Each library ships a Provider component
 *   that you wrap around your app once, and then any component can access
 *   that library's features via hooks.
 *
 * Providers included here:
 *   - ThemeProvider  : next-themes — manages dark/light mode class on <html>
 *   - QueryClientProvider : TanStack Query — caches API responses so we
 *                           don't re-fetch data that's still fresh
 *   - TooltipProvider : shadcn/Radix — needed globally for all Tooltip components
 */

import { useEffect, useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import posthog from "posthog-js";

import { SetupGate } from "@/components/setup-gate";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuthMe } from "@/hooks/use-auth";

/** Identifies the authenticated user in PostHog once the session resolves. */
function PostHogIdentity() {
  const { data } = useAuthMe();
  useEffect(() => {
    if (data?.authenticated && data.username) {
      posthog.identify(data.username, { username: data.username });
    }
  }, [data]);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  // useState ensures a new QueryClient per browser session, not per render
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Data is "fresh" for 1 minute — won't refetch within that window
            staleTime: 60 * 1_000,
            // Don't re-fetch just because the user tabbed away and came back
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    // attribute="class" means next-themes adds/removes the "dark" CSS class
    // on <html>, which is how shadcn's CSS variables know which palette to use
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delay={300}>
          <PostHogIdentity />
          <SetupGate>{children}</SetupGate>
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
