"use client";

/**
 * Simulate — what-if surplus and goal funding (Sub-Plan H). Full-page client shell; state lives in useSimulation.
 */

import * as React from "react";

import { GoalExplorer } from "@/components/simulation/goal-explorer";
import { SaveSimulationDialog } from "@/components/simulation/save-dialog";
import { SliderPanel } from "@/components/simulation/slider-panel";
import { SurplusWaterfall } from "@/components/simulation/surplus-waterfall";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSimulation } from "@/hooks/use-simulation";
import { newSimulationClientRowId } from "@/lib/simulation-goal-identity";
import type { SimulationGoal } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Loader2 } from "lucide-react";

/** Default row when the user adds a hypothetical goal in the sandbox (was ``goal-cards``). */
function defaultHypotheticalGoal(): SimulationGoal {
  return {
    id: null,
    client_row_id: newSimulationClientRowId(),
    name: "New what-if goal",
    goal_class: "POINT_IN_TIME",
    target_amount: 500000,
    target_date: new Date(Date.now() + 86400 * 365 * 3).toISOString().slice(0, 10),
    starting_balance: 0,
    allocation_priority: 50,
    expected_return_rate: 10,
    inflation_rate: null,
    goal_subtype: "CUSTOM",
  };
}

export default function SimulatePage() {
  const {
    baseParams,
    baseResult,
    draftParams,
    result,
    isLoading,
    isSimulating,
    isDirty,
    error,
    updateGlobalParam,
    addHypotheticalGoal,
    reorderGoalsByList,
    reset,
    commitDraftAsBase,
  } = useSimulation();

  const [saveOpen, setSaveOpen] = React.useState(false);

  if (isLoading && !draftParams) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (error && !draftParams) {
    return (
      <div className="p-6 text-destructive">
        Couldn&apos;t load your simulation. Try refreshing? {error}
      </div>
    );
  }

  if (!draftParams || !result) {
    return (
      <div className="p-6 text-muted-foreground">
        Nothing to show yet — tweak the sliders or add a goal, then we&apos;ll crunch the numbers.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 pb-16">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Simulate</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isSimulating && (
            <span className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
              Running numbers…
            </span>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={!isDirty}
            onClick={() => reset()}
          >
            Reset
          </Button>
          <Button
            type="button"
            disabled={!isDirty}
            onClick={() => setSaveOpen(true)}
          >
            Save changes
          </Button>
        </div>
      </div>

      <SliderPanel draft={draftParams} onChange={updateGlobalParam} />

      {/* Charts + goal cards dim slightly while POST /api/simulate runs (incl. cascade refinement on server). */}
      <div className="relative space-y-6">
        {isSimulating && (
          <div
            className="pointer-events-none absolute inset-0 z-10 rounded-xl bg-muted/25 animate-pulse"
            aria-hidden
          />
        )}
        <div
          className={cn(
            "relative z-0 space-y-6",
            isSimulating && "opacity-70 transition-opacity duration-200",
          )}
        >
          <SurplusWaterfall
            projections={result.projections}
            netWorthProjection={result.net_worth_projection}
          />

          <GoalExplorer
            goals={draftParams.goals}
            projections={result.projections}
            generalInflationRate={draftParams.general_inflation_rate ?? 6}
            asOfDate={draftParams.as_of_date ?? null}
            onReorderList={reorderGoalsByList}
            onAddHypothetical={() => addHypotheticalGoal(defaultHypotheticalGoal())}
          />
        </div>
      </div>

      <SaveSimulationDialog
        open={saveOpen}
        onOpenChange={setSaveOpen}
        baseParams={baseParams}
        draftParams={draftParams}
        baseResult={baseResult}
        draftResult={result}
        onSuccess={async () => {
          await commitDraftAsBase();
        }}
      />
    </div>
  );
}
