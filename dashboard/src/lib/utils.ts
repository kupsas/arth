import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import type { CounterpartyCategory, TxnType } from "@/lib/types"

// ─────────────────────────────────────────────────────────────────────────────
// shadcn utility — do not remove
// ─────────────────────────────────────────────────────────────────────────────

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ─────────────────────────────────────────────────────────────────────────────
// Currency formatting
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Formats a number as Indian Rupees with the ₹ symbol.
 * e.g. 12345.6  → "₹12,346"
 *      1234567  → "₹12,34,567"  (Indian comma grouping)
 * Use formatPercent() for savings_rate values — those are already 0–100.
 *
 * We use Intl.NumberFormat with the "en-IN" locale to get the correct
 * Indian comma grouping (2,00,000 not 200,000).
 */
export function formatCurrency(amount: number, decimals = 0): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(amount)
}

/**
 * Parse a rupee amount typed in a plain text field where the user may include
 * Indian-style grouping commas (e.g. "15,00,000"). Empty or invalid → null.
 */
export function parseInrMoneyInput(s: string): number | null {
  const normalized = s.trim().replace(/[\s,]/g, "")
  if (normalized === "" || normalized === "." || normalized === "-") return null
  const n = parseFloat(normalized)
  return Number.isFinite(n) ? n : null
}

/**
 * Format a number for controlled rupee inputs: Indian grouping, no ₹ symbol.
 * Whole rupees show without a decimal part; fractional amounts keep up to 2 decimals.
 */
export function formatInrMoneyInput(n: number): string {
  const isInt = Math.abs(n % 1) < 1e-9
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: isInt ? 0 : 2,
    minimumFractionDigits: 0,
  }).format(n)
}

/**
 * Use on `onChange` for goal-style rupee fields: normalize typing/paste to grouped text.
 */
export function reformatInrMoneyTyping(raw: string): string {
  const n = parseInrMoneyInput(raw)
  if (n === null) return ""
  return formatInrMoneyInput(n)
}

/**
 * Compact INR for tight spaces (chart axes, badges, simulation tooltips).
 * - **Crores (Cr)** from ₹1 crore upward (1,00,00,000) — e.g. 8.38Cr not 838.3L.
 * - **Lakhs (L)** from ₹1 lakh up to just below ₹1 crore.
 * - Below ₹1 lakh: **₹12k**-style thousands (lowercase k), not Western “100k” for a lakh.
 */
export function formatCurrencyCompact(amount: number): string {
  const n = Number(amount)
  const av = Math.abs(n)
  if (av >= 1_00_00_000) {
    const cr = n / 1_00_00_000
    // Two decimals then trim trailing zeros (8.00 → 8, 8.30 → 8.3, 8.38 → 8.38).
    return `₹${Number(cr.toFixed(2))}Cr`
  }
  if (av >= 1_00_000) {
    const lakhs = n / 1_00_000
    return `₹${lakhs.toFixed(1).replace(/\.0$/, "")}L`
  }
  if (av >= 1_000) {
    return `₹${Math.round(n / 1_000)}k`
  }
  return `₹${Math.round(n)}`
}

/**
 * Same rules as formatCurrencyCompact — use in Recharts `tickFormatter` / tooltips so INR stays consistent.
 */
export function formatInrChartAxis(value: number): string {
  return formatCurrencyCompact(value)
}

/**
 * Formats a 0–100 percentage value for display.
 * e.g. 42.5 → "42.5%"   0 → "0%"   100 → "100%"
 * Used for savings_rate from the metrics API (which returns 0–100, not 0–1).
 */
export function formatPercent(value: number, decimals = 1): string {
  return `${value.toFixed(decimals).replace(/\.0+$/, "")}%`
}

// ─────────────────────────────────────────────────────────────────────────────
// Date formatting
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Formats an ISO date string ("YYYY-MM-DD") into a readable format.
 * e.g. "2025-03-15" → "15 Mar 2025"
 */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—"
  const d = new Date(isoDate + "T00:00:00") // force local midnight parse
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d)
}

/** e.g. "27 March 2026" — for portfolio "as on" and other human-facing anchors. */
export function formatCalendarDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—"
  const d = new Date(isoDate + "T00:00:00")
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(d)
}

/**
 * Human-readable **time left** until a statutory maturity date (PPF column).
 * Compares **local calendar dates** to match what you see on a bank statement.
 *
 * - Before maturity: e.g. "4 yrs 2 mo left" or "12 days left"
 * - On/after maturity: "Matured"
 */
export function formatPpfMaturityRemaining(isoDate: string | null | undefined): string {
  if (!isoDate) return "—"
  const end = new Date(isoDate + "T00:00:00")
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  end.setHours(0, 0, 0, 0)
  if (end.getTime() < start.getTime()) return "Matured"
  if (end.getTime() === start.getTime()) return "Matures today"

  let years = end.getFullYear() - start.getFullYear()
  let months = end.getMonth() - start.getMonth()
  let days = end.getDate() - start.getDate()
  if (days < 0) {
    months -= 1
    const prevMonth = new Date(end.getFullYear(), end.getMonth(), 0)
    days += prevMonth.getDate()
  }
  if (months < 0) {
    years -= 1
    months += 12
  }

  const parts: string[] = []
  if (years > 0) parts.push(`${years} yr${years === 1 ? "" : "s"}`)
  if (months > 0) parts.push(`${months} mo`)
  if (parts.length === 0) {
    const msPerDay = 86_400_000
    const rawDays = Math.round((end.getTime() - start.getTime()) / msPerDay)
    return `${rawDays} day${rawDays === 1 ? "" : "s"} left`
  }
  return `${parts.join(" ")} left`
}

/**
 * Formats an ISO date string as short month + year.
 * e.g. "2025-03-15" → "Mar '25"
 * Used for chart axis labels.
 */
export function formatMonthShort(isoDate: string): string {
  // isoDate may be "YYYY-MM" from monthly trend endpoint
  const fullDate = isoDate.length === 7 ? isoDate + "-01" : isoDate
  const d = new Date(fullDate + "T00:00:00")
  return new Intl.DateTimeFormat("en-IN", {
    month: "short",
    year: "2-digit",
  }).format(d)
}

// ─────────────────────────────────────────────────────────────────────────────
// Category colour mapping
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Returns a Tailwind CSS colour class (bg-*) for a given category.
 * Used for consistent colouring in charts and badges across the dashboard.
 *
 * We intentionally avoid chartjs / recharts colour arrays because those
 * are positional — the same category could get different colours depending
 * on what else is in the chart. A stable mapping prevents that.
 */
export function categoryColor(category: CounterpartyCategory | string): string {
  const map: Record<string, string> = {
    "Food & Dining":                           "bg-orange-500",
    "Swiggy":                                  "bg-orange-400",
    "Shopping & E-commerce":                   "bg-blue-500",
    "Transport & Fuel":                        "bg-yellow-500",
    "Travel & Stay":                           "bg-cyan-500",
    "Utilities & Internet":                    "bg-teal-500",
    "Mobile, OTT & Subscriptions":             "bg-violet-500",
    "Healthcare & Pharmacy":                   "bg-red-500",
    "Entertainment & Events":                  "bg-pink-500",
    "Rent & Housing":                          "bg-indigo-500",
    "Salary & Income":                         "bg-green-500",
    "Asset Markets":                           "bg-emerald-500",
    "Financial Services, Insurance & Banking": "bg-sky-500",
    "Friends and Family":                      "bg-rose-400",
    "Gifts & Personal Transfers":              "bg-fuchsia-500",
    "Personal Grooming":                       "bg-purple-400",
    "Self Transfer":                           "bg-slate-400",
    "Fees, Charges & Interest":                "bg-red-400",
    "Miscellaneous":                           "bg-gray-400",
  }
  return map[category] ?? "bg-gray-400"
}

/**
 * Same as categoryColor but returns a hex colour string.
 * Recharts requires actual colour values (not Tailwind class names).
 */
export function categoryHexColor(category: CounterpartyCategory | string): string {
  const map: Record<string, string> = {
    "Food & Dining":                           "#f97316",
    "Swiggy":                                  "#fb923c",
    "Shopping & E-commerce":                   "#3b82f6",
    "Transport & Fuel":                        "#eab308",
    "Travel & Stay":                           "#06b6d4",
    "Utilities & Internet":                    "#14b8a6",
    "Mobile, OTT & Subscriptions":             "#8b5cf6",
    "Healthcare & Pharmacy":                   "#ef4444",
    "Entertainment & Events":                  "#ec4899",
    "Rent & Housing":                          "#6366f1",
    "Salary & Income":                         "#22c55e",
    "Asset Markets":                           "#10b981",
    "Financial Services, Insurance & Banking": "#0ea5e9",
    "Friends and Family":                      "#fb7185",
    "Gifts & Personal Transfers":              "#d946ef",
    "Personal Grooming":                       "#a855f7",
    "Self Transfer":                           "#94a3b8",
    "Fees, Charges & Interest":                "#f87171",
    "Miscellaneous":                           "#9ca3af",
  }
  return map[category] ?? "#9ca3af"
}

// ─────────────────────────────────────────────────────────────────────────────
// Transaction type labels
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Converts a TxnType enum value to a human-readable label.
 * e.g. "UPI_EXPENSE" → "UPI Expense"
 *      "CARD_PAYMENT" → "CC Bill Payment"
 */
export function txnTypeLabel(txnType: TxnType | string | null | undefined): string {
  if (!txnType) return "—"
  const labels: Record<string, string> = {
    BANK_TRANSFER:          "Bank Transfer",
    CARD_EXPENSE:           "Card Expense",
    CARD_PAYMENT:           "CC Bill Payment",
    EQUITY_PURCHASE:        "Equity Purchase",
    EQUITY_SALE:            "Equity Sale",
    EXPENSE_OTHER:          "Other Expense",
    INCOME_DIVIDEND:        "Dividend",
    INCOME_OTHER:           "Other Income",
    INCOME_SALARY:          "Salary",
    LOAN_INSURANCE_PAYMENT: "Loan / Insurance",
    MF_PURCHASE:            "MF Purchase",
    MF_SALE:                "MF Redemption",
    SELF_TRANSFER:          "Self Transfer",
    UPI_EXPENSE:            "UPI Expense",
    UPI_TRANSFER:           "UPI Transfer",
  }
  return labels[txnType] ?? txnType
}

// ─────────────────────────────────────────────────────────────────────────────
// Email-ingest review confidence (HIGH / MEDIUM / LOW)
// ─────────────────────────────────────────────────────────────────────────────

/** Tailwind classes for a small confidence badge on review cards and the edit sheet. */
export function reviewConfidenceBadgeClass(
  tier: string | null | undefined,
): string {
  switch (tier) {
    case "HIGH":
      return "border border-emerald-500/30 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
    case "MEDIUM":
      return "border border-amber-500/30 bg-amber-500/15 text-amber-800 dark:text-amber-300"
    case "LOW":
      return "border border-rose-500/30 bg-rose-500/15 text-rose-700 dark:text-rose-400"
    default:
      return "border border-border bg-muted text-muted-foreground"
  }
}

/** Short label for accessibility and tooltips. */
export function reviewConfidenceLabel(tier: string | null | undefined): string {
  if (!tier) return "Not scored (pre-feature or statement import)"
  return `${tier} review confidence`
}

