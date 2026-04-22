"use client";

/**
 * Summary strip: surplus use, goal status counts, engine warnings.
 */

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatCurrency } from "@/lib/utils";
import type { GoalProjection, SimulationResult } from "@/lib/types";

function bucketPct(
  p: GoalProjection,
): "strong" | "fair" | "weak" | "unknown" {
  const v =
    p.projected_completion_pct != null
      ? p.projected_completion_pct
      : p.periods_met_pct;
  if (v == null) {
    return "unknown";
  }
  if (v >= 90) {
    return "strong";
  }
  if (v >= 60) {
    return "fair";
  }
  return "weak";
}

const BUCKET_TONE: Record<"strong" | "fair" | "weak" | "unknown", "default" | "secondary" | "destructive" | "outline"> = {
  strong: "default",
  fair: "secondary",
  weak: "destructive",
  unknown: "outline",
};

export function RealityCheckCard({
  monthlySurplus,
  result,
}: {
  monthlySurplus: number;
  result: SimulationResult | null;
}) {
  if (!result) return null;

  const projections = result.projections ?? [];
  const bucketCounts: Record<"strong" | "fair" | "weak" | "unknown", number> = {
    strong: 0,
    fair: 0,
    weak: 0,
    unknown: 0,
  };
  for (const p of projections) {
    const b = bucketPct(p);
    bucketCounts[b] += 1;
  }

  const allocated = result.total_surplus_allocated ?? 0;
  const unalloc = result.unallocated_surplus ?? 0;
  const gap = Math.max(0, allocated + unalloc - monthlySurplus);
  const comfortable = gap < 1 && (result.warnings?.length ?? 0) === 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Reality check</CardTitle>
        <CardDescription>
          Sandbox uses{" "}
          <span className="font-medium text-foreground">
            {formatCurrency(monthlySurplus)}
          </span>{" "}
          / month as investable surplus. Allocations are model outputs, not bank
          transfers.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <p className="text-xs text-muted-foreground">Avg allocated / mo</p>
            <p className="font-mono text-lg font-semibold tabular-nums">
              {formatCurrency(allocated)}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-muted/30 p-3">
            <p className="text-xs text-muted-foreground">Avg unallocated / mo</p>
            <p className="font-mono text-lg font-semibold tabular-nums">
              {formatCurrency(unalloc)}
            </p>
          </div>
          <div
            className={`rounded-lg border p-3 ${
              comfortable
                ? "border-emerald-500/40 bg-emerald-500/5"
                : "border-amber-500/40 bg-amber-500/5"
            }`}
          >
            <p className="text-xs text-muted-foreground">Headroom vs surplus</p>
            <p className="flex items-center gap-1.5 font-mono text-lg font-semibold tabular-nums">
              {comfortable ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              ) : (
                <Info className="h-4 w-4 text-amber-600" />
              )}
              {comfortable ? "OK" : "Review warnings"}
            </p>
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Goal progress (simulation %)
          </p>
          <div className="flex flex-wrap gap-2">
            {(
              [
                { key: "strong" as const, label: "≥90%" },
                { key: "fair" as const, label: "60–90%" },
                { key: "weak" as const, label: "<60%" },
                { key: "unknown" as const, label: "n/a" },
              ] as const
            ).map(({ key, label }) => {
              const n = bucketCounts[key];
              if (n === 0) {
                return null;
              }
              return (
                <Badge key={key} variant={BUCKET_TONE[key]}>
                  {label} · {n}
                </Badge>
              );
            })}
          </div>
        </div>

        {result.warnings && result.warnings.length > 0 && (
          <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
            <p className="flex items-center gap-2 text-sm font-medium text-amber-900 dark:text-amber-100">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              Engine notes
            </p>
            <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
