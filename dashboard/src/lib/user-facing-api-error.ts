/**
 * Converts FastAPI / fetch error payloads into a single string suitable for
 * in-app UI (no raw JSON, no API path hints unless unavoidable).
 *
 * FastAPI often returns: { "detail": string | { message, hint } | array }
 */

/**
 * True if a string looks like a JSON object from an API (FastAPI error body).
 */
function looksLikeJsonObject(s: string): boolean {
  const t = s.trim();
  return t.startsWith("{") && t.includes('"detail"');
}

/**
 * Formats a FastAPI `detail` field: string, validation array, or message object.
 */
function formatDetailValue(detail: unknown): string {
  if (typeof detail === "string") {
    const m = detail.trim();
    if (!m) return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";
    if (m.length > 800) return `${m.slice(0, 797)}…`;
    return m;
  }

  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        const row = item as { msg: string; loc?: (string | number)[]; type?: string };
        const where =
          Array.isArray(row.loc) && row.loc.length
            ? `${row.loc
                .filter((x) => typeof x === "string" || typeof x === "number")
                .join(" → ")}: `
            : "";
        return `${where}${row.msg}`.trim();
      }
      try {
        return JSON.stringify(item);
      } catch {
        return String(item);
      }
    });
    return parts.join(" — ") || "That didn't work. Double-check your input and try again?";
  }

  if (detail && typeof detail === "object" && "message" in detail) {
    const o = detail as { message?: unknown; hint?: unknown };
    const msg = typeof o.message === "string" ? o.message.trim() : "";
    const hint = typeof o.hint === "string" ? o.hint.trim() : "";
    if (!msg && !hint) return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";
    if (!hint) return msg;
    if (!msg) return hint;
    if (msg.toLowerCase().includes(hint.toLowerCase().slice(0, 24))) return msg;
    return `${msg} ${hint}`;
  }

  try {
    return JSON.stringify(detail);
  } catch {
    return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";
  }
}

/**
 * Parse a full HTTP error body (usually JSON) into one user-facing line/paragraph.
 */
export function userMessageFromApiResponseBody(text: string): string {
  const t = text.trim();
  if (!t) return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";

  if (!looksLikeJsonObject(t)) {
    if (t.length > 800) return `${t.slice(0, 797)}…`;
    return t;
  }

  try {
    const parsed = JSON.parse(t) as { detail?: unknown; message?: unknown };
    if (parsed && typeof parsed === "object" && "detail" in parsed && parsed.detail !== undefined) {
      return formatDetailValue(parsed.detail);
    }
    if (parsed && typeof parsed === "object" && "message" in parsed && typeof parsed.message === "string") {
      return formatDetailValue({ message: parsed.message });
    }
  } catch {
    /* fall through */
  }

  if (t.length > 800) return `${t.slice(0, 797)}…`;
  return t;
}

/**
 * Use in catch blocks and React Query `error` where the value may be ApiError, Error,
 * a JSON string, or unknown.
 */
export function getUserFacingErrorMessage(err: unknown): string {
  if (err == null) return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";

  if (typeof err === "string") {
    return userMessageFromApiResponseBody(err);
  }

  if (err instanceof Error) {
    const m = err.message;
    if (m.trim() && (looksLikeJsonObject(m) || m.trim().startsWith("["))) {
      return userMessageFromApiResponseBody(m);
    }
    if (!m.trim()) return "Something broke on our end. Try refreshing — if it keeps happening, let us know.";
    return m;
  }

  return String(err);
}
