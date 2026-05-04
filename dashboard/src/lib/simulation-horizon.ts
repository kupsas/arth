/**
 * Derives the sandbox simulation length from goals (no manual horizon).
 *
 * Rule (product): run through **December of (latest goal end calendar year + 2)**.
 * Example: a goal with a deadline in 2046 → horizon includes all of 2048 (ends Dec 2048).
 *
 * Only **POINT_IN_TIME** goals participate — we take each such goal’s `target_date` and use
 * the latest year among them. **RECURRING** goals are ignored here (they may have no end date
 * or would skew the horizon awkwardly).
 *
 * This mirrors how the Python engine counts months from `as_of_date` (month 0 = first
 * simulated month) so the last simulated month is the last month we need for that horizon.
 */

import type { SimulationGoal } from "@/lib/types";

/** API allows at most 600 months (~50 years). */
export const SIMULATION_MONTHS_MAX = 600;

/** When no goal has an end date, keep the previous default horizon (20 years). */
const FALLBACK_SIMULATION_MONTHS = 240;

const GC_POINT = "POINT_IN_TIME";

/**
 * Target date used for horizon sizing — only PIT with a `target_date`.
 * Recurring goals are not used.
 */
function pitTargetDateIso(g: SimulationGoal): string | null {
  const gc = String(g.goal_class ?? "").toUpperCase();
  if (gc !== GC_POINT) {
    return null;
  }
  return g.target_date ?? null;
}

/** Whole months from start (inclusive) to end (exclusive), aligned with Python `months_between`. */
function monthsBetween(start: Date, endExclusive: Date): number {
  if (endExclusive <= start) return 0;
  const y1 = start.getFullYear();
  const m1 = start.getMonth() + 1;
  const d1 = start.getDate();
  const y2 = endExclusive.getFullYear();
  const m2 = endExclusive.getMonth() + 1;
  const d2 = endExclusive.getDate();
  let total = (y2 - y1) * 12 + (m2 - m1);
  if (d2 < d1) total -= 1;
  return Math.max(0, total);
}

/** First day of the calendar month for the simulation anchor (matches engine `start_month`). */
function startMonthFromAsOf(asOfIso: string | null | undefined): Date {
  let d: Date;
  if (asOfIso && /^\d{4}-\d{2}-\d{2}/.test(asOfIso)) {
    const y = Number(asOfIso.slice(0, 4));
    const mo = Number(asOfIso.slice(5, 7));
    const day = Number(asOfIso.slice(8, 10));
    d = new Date(y, mo - 1, day);
  } else {
    d = new Date();
  }
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

/**
 * Number of simulated months so the run ends in **December** of `(max PIT target year) + 2`.
 * Falls back to 240 months when no such goal has a `target_date`.
 */
export function computeSimulationHorizonMonths(
  goals: SimulationGoal[],
  asOfIso: string | null | undefined,
): number {
  const start = startMonthFromAsOf(asOfIso ?? null);

  let maxYear: number | null = null;
  for (const g of goals) {
    const iso = pitTargetDateIso(g);
    if (!iso) continue;
    const y = Number(iso.slice(0, 4));
    if (!Number.isFinite(y)) continue;
    maxYear = maxYear === null ? y : Math.max(maxYear, y);
  }

  if (maxYear === null) {
    return FALLBACK_SIMULATION_MONTHS;
  }

  const horizonEndYear = maxYear + 2;
  // First day of January after the last simulated month (Dec of horizonEndYear).
  const endExclusive = new Date(horizonEndYear + 1, 0, 1);
  const months = monthsBetween(start, endExclusive);
  const capped = Math.min(SIMULATION_MONTHS_MAX, Math.max(1, months));
  return capped;
}

/** Human-readable label for the read-only horizon control (calendar end year). */
export function simulationHorizonEndYearLabel(
  goals: SimulationGoal[],
  asOfIso: string | null | undefined,
): string | null {
  const start = startMonthFromAsOf(asOfIso ?? null);

  let maxYear: number | null = null;
  for (const g of goals) {
    const iso = pitTargetDateIso(g);
    if (!iso) continue;
    const y = Number(iso.slice(0, 4));
    if (!Number.isFinite(y)) continue;
    maxYear = maxYear === null ? y : Math.max(maxYear, y);
  }

  if (maxYear === null) {
    return null;
  }

  const horizonEndYear = maxYear + 2;
  const endExclusive = new Date(horizonEndYear + 1, 0, 1);
  const months = monthsBetween(start, endExclusive);
  if (months >= SIMULATION_MONTHS_MAX) {
    return `${horizonEndYear} (capped at ${SIMULATION_MONTHS_MAX} mo)`;
  }
  return String(horizonEndYear);
}
