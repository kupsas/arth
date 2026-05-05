/**
 * Turn internal source keys (e.g. ``hdfc_savings``) into short UI labels without
 * exposing raw pipeline identifiers to end users.
 */

/** Friendly names for known pipeline source keys (add more as you onboard banks). */
const KNOWN_SOURCE_LABELS: Record<string, string> = {
  hdfc_savings: "HDFC savings",
  hdfc_cc: "HDFC credit card",
  icici_savings: "ICICI savings",
  all: "All accounts",
};

/**
 * Short label for a statement / pipeline source key shown in upload flows and hints.
 */
export function humanizeSourceKey(sourceKey: string | null | undefined): string {
  if (!sourceKey?.trim()) return "this account";
  const key = sourceKey.trim().toLowerCase();
  if (KNOWN_SOURCE_LABELS[key]) return KNOWN_SOURCE_LABELS[key];
  return sourceKey
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}
