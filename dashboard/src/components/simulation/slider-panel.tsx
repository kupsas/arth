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
import { formatInrMoneyInput, parseInrMoneyInput } from "@/lib/utils";

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
  /** Plain `type="number"` cannot show Indian grouping; use text + grouped rupees for surplus. */
  rupeeTextField,
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
  rupeeTextField?: boolean;
}) {
  /**
   * `localDraft` holds what the user is actively typing as a raw string.
   * While it's non-null the input shows the draft; on blur we parse + commit + clear it.
   * This prevents every keystroke from snapping to the nearest step (e.g. typing "1"
   * toward "10" was immediately clamped to 1000 for the surplus field).
   */
  const [localDraft, setLocalDraft] = React.useState<string | null>(null);
  const [parseErr, setParseErr] = React.useState<string | null>(null);

  // When the committed value changes from outside (e.g. Reset), clear any stale draft.
  React.useEffect(() => {
    setLocalDraft(null);
    setParseErr(null);
  }, [value]);

  const safeVal = Number.isFinite(value) ? value : min;

  /** What the <input> actually shows: the live draft while typing, the prop value otherwise. */
  const displayValue =
    localDraft !== null
      ? localDraft
      : rupeeTextField
        ? formatInrMoneyInput(safeVal)
        : safeVal;

  const handleChange = (raw: string) => {
    setLocalDraft(raw);
    // Show a parse error inline while typing, but don't commit yet.
    const t = raw.trim();
    if (t === "" || t === "-") { setParseErr(null); return; }
    const parsed = rupeeTextField ? parseInrMoneyInput(t) : parseGoalDecimalString(t);
    setParseErr(parsed === null ? SIMULATION_INVALID_DECIMAL_MESSAGE : null);
  };

  const handleBlur = () => {
    setParseErr(null);
    if (localDraft === null) return;
    const t = localDraft.trim();
    // Empty / sign-only: revert to current prop value.
    if (t === "" || t === "-") { setLocalDraft(null); return; }
    const parsed = rupeeTextField ? parseInrMoneyInput(t) : parseGoalDecimalString(t);
    if (parsed === null) { setLocalDraft(null); return; }
    onCommit(clamp(snapToStep(parsed, step), min, max));
    setLocalDraft(null);
  };

  return (
    <div className="min-w-0 space-y-1.5">
      <Label className="text-xs font-medium leading-snug text-foreground" htmlFor={inputId}>
        {label}
      </Label>
      <div className="relative w-full">
        <Input
          id={inputId}
          type={rupeeTextField ? "text" : "number"}
          {...(rupeeTextField ? {} : { min, max, step })}
          inputMode={inputMode}
          value={displayValue}
          aria-invalid={!!parseErr}
          aria-describedby={parseErr ? `${inputId}-err ${inputId}-range` : `${inputId}-range`}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={handleBlur}
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
    <Card className="h-full">
      <CardContent className="flex h-full flex-col gap-4 pb-4 pt-4">
        {/* Read-only horizon */}
        <div className="border-b border-border pb-3">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Simulation horizon
          </p>
          <p className="mt-0.5 text-sm">
            <span className="font-mono font-semibold text-foreground">
              {simMonths.toLocaleString("en-IN")}
            </span>{" "}
            months
          </p>
          {horizonYearLabel == null ? (
            <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
              Add a goal with a target date to tie this to your plan.
            </p>
          ) : null}
        </div>

        <div className="grid grid-cols-1 gap-4">
          <MacroParamBlock
            label="Monthly surplus"
            value={draft.monthly_surplus}
            min={0}
            max={SIMULATION_MONTHLY_SURPLUS_MAX_INR}
            step={1000}
            inputId="sim-monthly-surplus"
            inputSuffix="/ mo"
            inputMode="numeric"
            rupeeTextField
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

        {/* Spacer so the card fills its column height and the inputs sit at the top */}
        <div className="flex-1" />
      </CardContent>
    </Card>
  );
}
