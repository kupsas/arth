/**
 * Turn internal source keys (e.g. ``hdfc_savings``) into short UI labels without
 * exposing raw pipeline identifiers to end users.
 */

/** Friendly names for known pipeline source keys (add more as you onboard banks). */
const KNOWN_SOURCE_LABELS: Record<string, string> = {
  hdfc_savings: "HDFC savings",
  hdfc_savings_pdf: "HDFC savings (PDF)",
  hdfc_cc: "HDFC credit card",
  hdfc_cc_pdf: "HDFC credit card (PDF)",
  icici_savings: "ICICI savings",
  icici_direct_equity_statement_pdf: "ICICI Direct equity statement",
  icici_direct_mf_statement_pdf: "ICICI Direct mutual fund statement",
  icici_direct_contract_note: "Contract note / trade confirmation",
  icici_ppf_pdf: "ICICI PPF",
  icici_direct_equity: "ICICI Direct equity",
  icici_direct_mf: "ICICI Direct mutual fund",
  icici_ppf: "ICICI PPF",
  all: "All accounts",
};

/**
 * Short label for a statement / pipeline source key shown in upload flows and hints.
 */
export function humanizeSourceKey(sourceKey: string | null | undefined): string {
  if (!sourceKey?.trim()) return "this account";
  const key = sourceKey.trim().toLowerCase();
  if (KNOWN_SOURCE_LABELS[key]) return KNOWN_SOURCE_LABELS[key];
  let m = /^hdfc_savings_(\d{4})$/.exec(key);
  if (m) return `HDFC savings (…${m[1]})`;
  m = /^icici_savings_(\d{4})$/.exec(key);
  if (m) return `ICICI savings (…${m[1]})`;
  m = /^hdfc_cc_(\d{4})$/.exec(key);
  if (m) return `HDFC credit card (…${m[1]})`;
  return sourceKey
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}
