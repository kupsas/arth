/**
 * Persisted chat rows store tool outputs as strings from ``wrap_tool_output``:
 * ``<tool_result name="…">\n{…json…}\n</tool_result>``.
 * The dashboard must unwrap that for display (same shape as live WebSocket results).
 */

function tryParseJsonObject(s: string): Record<string, unknown> | undefined {
  const t = s.trim();
  if (!t) return undefined;
  try {
    const v = JSON.parse(t) as unknown;
    if (typeof v === "object" && v !== null && !Array.isArray(v)) {
      return v as Record<string, unknown>;
    }
  } catch {
    /* ignore */
  }
  return undefined;
}

/**
 * Parse ``role: tool`` message ``content`` from SQLite / OpenAI history into a plain object.
 */
export function parsePersistedToolMessageContent(content: unknown): Record<string, unknown> {
  if (content !== null && typeof content === "object" && !Array.isArray(content)) {
    return content as Record<string, unknown>;
  }
  if (typeof content !== "string") {
    return { _unparsed: String(content) };
  }

  const s = content.trim();
  const open = s.match(/^<tool_result\b[^>]*>/i);
  const closeIdx = s.toLowerCase().lastIndexOf("</tool_result>");
  if (open && open.index === 0 && closeIdx > open[0].length) {
    const inner = s.slice(open[0].length, closeIdx).trim();
    const parsed = tryParseJsonObject(inner);
    if (parsed) return parsed;
    return { _parse_error: true, _inner_preview: inner.slice(0, 400) };
  }

  const asJson = tryParseJsonObject(s);
  if (asJson) return asJson;

  return { raw: s };
}
