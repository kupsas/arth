"use client";

/**
 * Simulation sandbox — Sub-Plan H. Full-page client shell; state lives in useSimulation.
 */

import * as React from "react";

import { GoalCards, defaultHypotheticalGoal } from "@/components/simulation/goal-cards";
import { GoalTimeline } from "@/components/simulation/goal-timeline";
import { RunRateChart } from "@/components/simulation/run-rate-chart";
import { SaveSimulationDialog } from "@/components/simulation/save-dialog";
import { SliderPanel } from "@/components/simulation/slider-panel";
import { SurplusWaterfall } from "@/components/simulation/surplus-waterfall";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSimulation } from "@/hooks/use-simulation";

export default function SimulatePage() {
  const {
    baseParams,
    baseResult,
    draftParams,
    meta,
    result,
    isLoading,
    isSimulating,
    isDirty,
    error,
    updateGlobalParam,
    updateGoal,
    addHypotheticalGoal,
    removeGoal,
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
        Failed to load simulation: {error}
      </div>
    );
  }

  if (!draftParams || !result) {
    return (
      <div className="p-6 text-muted-foreground">
        No simulation data.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 pb-16">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Simulation sandbox</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          {isSimulating && (
            <span className="self-center text-xs text-muted-foreground">Updating…</span>
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

      <SurplusWaterfall
        projections={result.projections}
        cascadeEvents={result.cascade_events}
      />

      <RunRateChart
        projections={result.projections}
        netWorthProjection={result.net_worth_projection}
        goals={draftParams.goals}
      />

      <GoalTimeline events={result.cascade_events} />

      <GoalCards
        goals={draftParams.goals}
        projections={result.projections}
        onUpdateGoal={(id, idx, patch) => updateGoal(id, idx, patch)}
        onRemoveGoal={(id, idx) => removeGoal(id, idx)}
        onReorderList={reorderGoalsByList}
        onAddHypothetical={() => addHypotheticalGoal(defaultHypotheticalGoal())}
      />

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
