"use client";

/**
 * Global sandbox controls: surplus, growth, inflation, and a read-only simulation horizon.
 *
 * Horizon is computed elsewhere (latest PIT target year + 2); this panel only displays it.
 * Layout: one summary row for horizon, then three macro fields in a single horizontal row on
 * medium+ screens to keep the card short.
 */

import * as React from "react";

import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  parseGoalDecimalString,
  SIMULATION_INVALID_DECIMAL_MESSAGE,
  SIMULATION_MONTHLY_SURPLUS_MAX_INR,
} from "@/lib/onboarding-input-validation";
import { simulationHorizonEndYearLabel } from "@/lib/simulation-horizon";
import type { SimulationParams } from "@/lib/types";

/** Default salary growth when the server omits the field (matches API SimulationParams default). */
const DEFAULT_SALARY_GROWTH_PCT = 5;

const SALARY_GROWTH_MIN = 0;
const SALARY_GROWTH_MAX = 50;
const SALARY_GROWTH_STEP = 0.5;

const GENERAL_INFLATION_MIN = 0;
const GENERAL_INFLATION_MAX = 15;
const GENERAL_INFLATION_STEP = 0.5;

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n));
}

/** Snap typed values to the same step as the range (₹1k for surplus, 0.5% for macros). */
function snapToStep(n: number, step: number): number {
  if (step <= 0) return n;
  const k = Math.round(n / step);
  const v = k * step;
  return step < 1 ? Math.round(v * 1000) / 1000 : v;
}

/**
 * One editable macro in a **vertical** block (label above, field below) so three blocks can sit
 * side-by-side without eating vertical space.
 */
function MacroParamBlock({
  label,
  value,
  min,
  max,
  step,
  onCommit,
  inputId,
  formatRangeMin,
  formatRangeMax,
  inputSuffix,
  inputMode,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (v: number) => void;
  inputId: string;
  formatRangeMin: () => string;
  formatRangeMax: () => string;
  inputSuffix?: string;
  inputMode?: "decimal" | "numeric";
}) {
  const [parseErr, setParseErr] = React.useState<string | null>(null);

  const handleInputChange = (raw: string) => {
    const t = raw.trim();
    if (t === "" || t === "-") {
      setParseErr(null);
      return;
    }
    const parsed = parseGoalDecimalString(raw);
    if (parsed === null) {
      setParseErr(SIMULATION_INVALID_DECIMAL_MESSAGE);
      return;
    }
    setParseErr(null);
    onCommit(clamp(snapToStep(parsed, step), min, max));
  };

  return (
    <div className="min-w-0 space-y-1.5">
      <Label className="text-xs font-medium leading-snug text-foreground" htmlFor={inputId}>
        {label}
      </Label>
      <div className="relative w-full">
        <Input
          id={inputId}
          type="number"
          min={min}
          max={max}
          step={step}
          inputMode={inputMode}
          value={Number.isFinite(value) ? value : min}
          aria-invalid={!!parseErr}
          aria-describedby={parseErr ? `${inputId}-err ${inputId}-range` : `${inputId}-range`}
          onChange={(e) => handleInputChange(e.target.value)}
          onBlur={() => setParseErr(null)}
          className="h-8 pr-9 text-right font-mono text-sm tabular-nums"
        />
        {inputSuffix ? (
          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
            {inputSuffix}
          </span>
        ) : null}
      </div>
      {parseErr ? (
        <p id={`${inputId}-err`} className="text-[10px] leading-tight text-destructive" role="alert">
          {parseErr}
        </p>
      ) : null}
      <p id={`${inputId}-range`} className="text-[10px] leading-tight text-muted-foreground">
        Min {formatRangeMin()} · Max {formatRangeMax()}
      </p>
    </div>
  );
}

export function SliderPanel({
  draft,
  onChange,
}: {
  draft: SimulationParams;
  onChange: <K extends keyof SimulationParams>(key: K, value: SimulationParams[K]) => void;
}) {
  // Keep surplus inside allowed bounds (e.g. server can return more than ₹10L/mo).
  React.useEffect(() => {
    if (draft.monthly_surplus > SIMULATION_MONTHLY_SURPLUS_MAX_INR) {
      onChange("monthly_surplus", SIMULATION_MONTHLY_SURPLUS_MAX_INR);
    } else if (draft.monthly_surplus < 0) {
      onChange("monthly_surplus", 0);
    }
  }, [draft.monthly_surplus, onChange]);

  const salaryGrowth = draft.salary_growth_rate ?? DEFAULT_SALARY_GROWTH_PCT;
  const generalInflation = draft.general_inflation_rate ?? 6;
  const simMonths = draft.simulation_months ?? 240;
  const horizonYearLabel = simulationHorizonEndYearLabel(draft.goals, draft.as_of_date);

  return (
    <Card>
      <CardContent className="space-y-4 pb-4 pt-4">
        {/* Read-only horizon — latest point-in-time / growth target year + 2 calendar years. */}
        <div className="flex flex-col gap-1 border-b border-border pb-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm font-medium leading-snug">Simulation horizon</p>
          <p className="text-sm text-muted-foreground">
            <span className="font-mono font-medium text-foreground">
              {simMonths.toLocaleString("en-IN")}
            </span>{" "}
            months
            {horizonYearLabel == null ? (
              <span>
                {" "}
                (add a point-in-time or growth goal with a target date to tie this to your plan)
              </span>
            ) : null}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 sm:gap-5">
          <MacroParamBlock
            label="Monthly surplus"
            value={draft.monthly_surplus}
            min={0}
            max={SIMULATION_MONTHLY_SURPLUS_MAX_INR}
            step={1000}
            inputId="sim-monthly-surplus"
            inputSuffix="/ mo"
            inputMode="numeric"
            formatRangeMin={() => "₹0 / mo"}
            formatRangeMax={() =>
              `₹${(SIMULATION_MONTHLY_SURPLUS_MAX_INR / 100_000).toLocaleString("en-IN", { maximumFractionDigits: 0 })} lakh / mo`
            }
            onCommit={(v) => onChange("monthly_surplus", v)}
          />

          <MacroParamBlock
            label="Salary growth (annual)"
            value={salaryGrowth}
            min={SALARY_GROWTH_MIN}
            max={SALARY_GROWTH_MAX}
            step={SALARY_GROWTH_STEP}
            inputId="sim-salary-growth"
            inputSuffix="%"
            formatRangeMin={() => `${SALARY_GROWTH_MIN}%`}
            formatRangeMax={() => `${SALARY_GROWTH_MAX}%`}
            onCommit={(v) => onChange("salary_growth_rate", v)}
          />

          <MacroParamBlock
            label="General inflation (headline)"
            value={generalInflation}
            min={GENERAL_INFLATION_MIN}
            max={GENERAL_INFLATION_MAX}
            step={GENERAL_INFLATION_STEP}
            inputId="sim-general-inflation"
            inputSuffix="%"
            formatRangeMin={() => `${GENERAL_INFLATION_MIN}%`}
            formatRangeMax={() => `${GENERAL_INFLATION_MAX}%`}
            onCommit={(v) => onChange("general_inflation_rate", v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
