"use client";

/**
 * Global sandbox controls: surplus, growth, inflation, horizon, one-off cash flows.
 *
 * Layout: two columns on large screens — compact inline label + number inputs on the left,
 * horizon and one-time flows on the right.
 */

import * as React from "react";
import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { OneTimeEvent, SimulationParams } from "@/lib/types";

const HORIZON_MONTHS = [
  { label: "5 years", value: 60 },
  { label: "10 years", value: 120 },
  { label: "15 years", value: 180 },
  { label: "20 years", value: 240 },
  { label: "30 years", value: 360 },
];

/** Upper bound for monthly surplus (10 lakh INR / month). */
const MONTHLY_SURPLUS_MAX_INR = 1_000_000;

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
 * Single macro row: label on the left, number field + allowed range on the right.
 * Values are snapped to step and clamped to [min, max] (same rules as before sliders existed).
 */
function MacroParamRow({
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
  const handleInputChange = (raw: string) => {
    if (raw === "" || raw === "-") return;
    const parsed = Number.parseFloat(raw);
    if (Number.isNaN(parsed)) return;
    onCommit(clamp(snapToStep(parsed, step), min, max));
  };

  return (
    <div className="flex items-center justify-between gap-3">
      <Label className="min-w-0 flex-1 text-sm leading-snug" htmlFor={inputId}>
        {label}
      </Label>
      <div className="flex w-38 shrink-0 flex-col items-end gap-0.5">
        <div className="relative w-full">
          <Input
            id={inputId}
            type="number"
            min={min}
            max={max}
            step={step}
            inputMode={inputMode}
            value={Number.isFinite(value) ? value : min}
            onChange={(e) => handleInputChange(e.target.value)}
            className="h-8 pr-9 text-right font-mono text-sm tabular-nums"
          />
          {inputSuffix ? (
            <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
              {inputSuffix}
            </span>
          ) : null}
        </div>
        <p className="max-w-38 text-right text-[10px] leading-tight text-muted-foreground">
          Min {formatRangeMin()} · Max {formatRangeMax()}
        </p>
      </div>
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
    if (draft.monthly_surplus > MONTHLY_SURPLUS_MAX_INR) {
      onChange("monthly_surplus", MONTHLY_SURPLUS_MAX_INR);
    } else if (draft.monthly_surplus < 0) {
      onChange("monthly_surplus", 0);
    }
  }, [draft.monthly_surplus, onChange]);

  const salaryGrowth = draft.salary_growth_rate ?? DEFAULT_SALARY_GROWTH_PCT;
  const generalInflation = draft.general_inflation_rate ?? 6;

  const addOneTime = (list: "inflows" | "outflows") => {
    const key = list === "inflows" ? "one_time_inflows" : "one_time_outflows";
    const ev: OneTimeEvent = {
      amount: 100000,
      date: new Date().toISOString().slice(0, 10),
      description: "",
    };
    onChange(key, [...(draft[key] ?? []), ev]);
  };

  const removeOneTime = (list: "inflows" | "outflows", index: number) => {
    const key = list === "inflows" ? "one_time_inflows" : "one_time_outflows";
    const arr = [...(draft[key] ?? [])];
    arr.splice(index, 1);
    onChange(key, arr);
  };

  const patchOneTime = (
    list: "inflows" | "outflows",
    index: number,
    patch: Partial<OneTimeEvent>,
  ) => {
    const key = list === "inflows" ? "one_time_inflows" : "one_time_outflows";
    const arr = [...(draft[key] ?? [])];
    arr[index] = { ...arr[index], ...patch };
    onChange(key, arr);
  };

  return (
    <Card>
      <CardContent className="pb-4 pt-0">
        <div className="grid gap-5 lg:grid-cols-2 lg:gap-8">
          {/* Left: inline label + number fields only (short vertical stack) */}
          <div className="min-w-0 space-y-2 lg:max-w-lg">
            <MacroParamRow
              label="Monthly surplus"
              value={draft.monthly_surplus}
              min={0}
              max={MONTHLY_SURPLUS_MAX_INR}
              step={1000}
              inputId="sim-monthly-surplus"
              inputSuffix="/ mo"
              inputMode="numeric"
              formatRangeMin={() => "₹0 / mo"}
              formatRangeMax={() =>
                `₹${(MONTHLY_SURPLUS_MAX_INR / 100_000).toLocaleString("en-IN", { maximumFractionDigits: 0 })} lakh / mo`
              }
              onCommit={(v) => onChange("monthly_surplus", v)}
            />

            <MacroParamRow
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

            <MacroParamRow
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

          {/* Right: horizon + one-time flows */}
          <div className="min-w-0 space-y-4 border-t border-border pt-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
            <div className="flex max-w-md items-center justify-between gap-3">
              <Label className="text-sm leading-snug">Simulation horizon</Label>
              <Select
                value={String(draft.simulation_months ?? 240)}
                onValueChange={(v) => onChange("simulation_months", Number(v))}
              >
                <SelectTrigger className="h-8 min-w-[10.5rem] shrink-0">
                  <SelectValue>
                    {HORIZON_MONTHS.find((h) => h.value === (draft.simulation_months ?? 240))
                      ?.label ?? `${draft.simulation_months ?? 240} months`}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {HORIZON_MONTHS.map((h) => (
                    <SelectItem key={h.value} value={String(h.value)}>
                      {h.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2 border-t border-border pt-3">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-sm font-medium">One-time inflows</Label>
                <Button type="button" variant="outline" size="sm" onClick={() => addOneTime("inflows")}>
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  Add
                </Button>
              </div>
              {(draft.one_time_inflows ?? []).map((ev, i) => (
                <div
                  key={`in-${i}`}
                  className="flex flex-wrap items-end gap-2 rounded-md border border-border p-2"
                >
                  <div className="grid min-w-[120px] flex-1 gap-1">
                    <Label className="text-xs">Amount (INR)</Label>
                    <Input
                      type="number"
                      value={ev.amount}
                      onChange={(e) =>
                        patchOneTime("inflows", i, { amount: Number(e.target.value) })
                      }
                    />
                  </div>
                  <div className="grid min-w-[140px] flex-1 gap-1">
                    <Label className="text-xs">Date</Label>
                    <Input
                      type="date"
                      value={ev.date}
                      onChange={(e) => patchOneTime("inflows", i, { date: e.target.value })}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => removeOneTime("inflows", i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>

            <div className="space-y-2 border-t border-border pt-3">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-sm font-medium">One-time outflows</Label>
                <Button type="button" variant="outline" size="sm" onClick={() => addOneTime("outflows")}>
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  Add
                </Button>
              </div>
              {(draft.one_time_outflows ?? []).map((ev, i) => (
                <div
                  key={`out-${i}`}
                  className="flex flex-wrap items-end gap-2 rounded-md border border-border p-2"
                >
                  <div className="grid min-w-[120px] flex-1 gap-1">
                    <Label className="text-xs">Amount (INR)</Label>
                    <Input
                      type="number"
                      value={ev.amount}
                      onChange={(e) =>
                        patchOneTime("outflows", i, { amount: Number(e.target.value) })
                      }
                    />
                  </div>
                  <div className="grid min-w-[140px] flex-1 gap-1">
                    <Label className="text-xs">Date</Label>
                    <Input
                      type="date"
                      value={ev.date}
                      onChange={(e) => patchOneTime("outflows", i, { date: e.target.value })}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => removeOneTime("outflows", i)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
