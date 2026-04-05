"use client";

/**
 * Simplified "timeline": cascade events in order (stretch v1 — not full Git graph).
 */

import { GitMerge } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import type { CascadeEvent } from "@/lib/types";

export function GoalTimeline({ events }: { events: CascadeEvent[] }) {
  const sorted = [...events].sort((a, b) => a.month.localeCompare(b.month));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Cascade timeline</CardTitle>
        <CardDescription>
          When a goal completes, freed surplus flows to other goals (Git-merge style view is
          planned; this list is the same data in chronological order).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="text-sm text-muted-foreground">No completions in this horizon.</p>
        ) : (
          <ol className="relative ml-0 space-y-4 border-l border-border pl-4">
            {sorted.map((ev, i) => (
              <li key={`${ev.month}-${ev.completed_goal}-${i}`} className="text-sm">
                <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-primary" />
                <div className="flex flex-wrap items-center gap-2">
                  <GitMerge className="h-4 w-4 text-muted-foreground" />
                  <span className="font-mono text-xs text-muted-foreground">
                    {ev.month.slice(0, 7)}
                  </span>
                </div>
                <p className="mt-1 font-medium">{ev.completed_goal} completed</p>
                <p className="text-muted-foreground">
                  Freed ~{formatCurrency(ev.freed_surplus)}/mo
                  {ev.beneficiary_goals?.length ? (
                    <>
                      {" "}
                      → {ev.beneficiary_goals.slice(0, 5).join(", ")}
                      {ev.beneficiary_goals.length > 5 ? "…" : ""}
                    </>
                  ) : null}
                </p>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
