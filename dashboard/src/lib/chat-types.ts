/**
 * Types for dashboard agent chat — REST resources and WebSocket wire protocol.
 * Server shapes come from FastAPI (`api/routes/chat_ws.py`) and `agent/events.py`.
 */

import { parsePersistedToolMessageContent } from "@/lib/parse-tool-message";

/** GET /api/chat/sessions */
export interface ChatSessionSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/chat/sessions/{id} */
export interface ChatSessionDetail extends ChatSessionSummary {
  messages: Record<string, unknown>[];
}

/** One tool invocation shown in the “thinking” strip */
export interface ToolCallUi {
  name: string;
  arguments: Record<string, unknown>;
  /** Populated after `tool_call_completed` */
  result?: Record<string, unknown>;
  duration_ms?: number;
}

/** Lightweight row for the live “thinking” strip — tool names only, no arguments. */
export interface LiveTool {
  name: string;
  status: "running" | "done";
}

/**
 * Chronological ReAct trace: alternating thinking text and tool batches (matches server ``_arth_timeline``).
 * When set, the UI renders interleaved blocks instead of one aggregated thinking + one tool list.
 */
export type ActivitySegment =
  | { kind: "thinking"; content: string }
  | { kind: "tools"; tools: ToolCallUi[] };

/** Rendered chat row */
export interface ChatMessageUi {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallUi[];
  /** Model reasoning for this turn (from WebSocket ``thinking``); shown after reply completes */
  thinking?: string;
  /** Ordered thinking + tool segments for a Cursor-style chronological trace */
  activity?: ActivitySegment[];
  /** True while WebSocket ``token`` frames are still arriving for this row */
  isStreaming?: boolean;
}

/** Inbound JSON from the WebSocket (subset; ignore unknown `type`s) */
export type ServerChatWireMessage =
  | {
      type: "session_ready";
      session_id: string;
      title: string | null;
    }
  | {
      type: "llm_step";
      step: number;
      model: string | null;
      finish_reason: string | null;
      content: string | null;
      reasoning: string | null;
      tool_intents: { name: string; arguments: Record<string, unknown> }[];
    }
  | {
      type: "tool_call_started";
      tool_name: string;
      arguments: Record<string, unknown>;
      tool_call_id: string | null;
    }
  | {
      type: "tool_call_completed";
      tool_name: string;
      result: Record<string, unknown>;
      duration_ms: number;
      tool_call_id: string | null;
    }
  | { type: "thinking"; content: string }
  | { type: "thinking_done" }
  | { type: "token"; token: string }
  | { type: "response"; content: string }
  | { type: "error"; message: string; recoverable: boolean }
  | {
      type: "screening_blocked";
      category: string;
      message: string;
      layer: string;
      latency_ms: number;
    }
  | { type: "done" };

/** Client → server */
export type ClientChatWireMessage =
  | { type: "send_message"; content: string }
  | { type: "stop" };

function uid(): string {
  return crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Parse persisted ``_arth_timeline`` JSON into UI segments. */
export function parseActivityTimeline(raw: unknown): ActivitySegment[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: ActivitySegment[] = [];
  for (const seg of raw) {
    if (!seg || typeof seg !== "object") continue;
    const o = seg as Record<string, unknown>;
    const kind = String(o.kind ?? "");
    if (kind === "thinking") {
      const content = String(o.content ?? "");
      if (content.trim()) out.push({ kind: "thinking", content });
    } else if (kind === "tools") {
      const toolsRaw = o.tools;
      if (!Array.isArray(toolsRaw)) continue;
      const tools: ToolCallUi[] = [];
      for (const t of toolsRaw) {
        if (!t || typeof t !== "object") continue;
        const tr = t as Record<string, unknown>;
        const name = String(tr.name ?? "");
        const args = tr.arguments;
        const result = tr.result;
        const duration_ms =
          typeof tr.duration_ms === "number" && !Number.isNaN(tr.duration_ms)
            ? tr.duration_ms
            : undefined;
        tools.push({
          name,
          arguments:
            typeof args === "object" && args !== null && !Array.isArray(args)
              ? (args as Record<string, unknown>)
              : {},
          ...(result !== undefined && typeof result === "object" && result !== null && !Array.isArray(result)
            ? { result: result as Record<string, unknown> }
            : {}),
          ...(duration_ms !== undefined ? { duration_ms } : {}),
        });
      }
      if (tools.length) out.push({ kind: "tools", tools });
    }
  }
  return out.length ? out : undefined;
}

function mergeAssistantToolBlock(
  m: Record<string, unknown>,
  raw: Record<string, unknown>[],
  startIdx: number,
): { endIdx: number; merged: ToolCallUi[] } {
  const tcRaw = m.tool_calls as unknown;
  const toolsOpenAi: { id?: string; function?: { name?: string; arguments?: string } }[] =
    Array.isArray(tcRaw) ? (tcRaw as typeof toolsOpenAi) : [];

  const merged: ToolCallUi[] = toolsOpenAi.map((t) => {
    let args: Record<string, unknown> = {};
    const rawArgs = t.function?.arguments;
    if (typeof rawArgs === "string") {
      try {
        args = JSON.parse(rawArgs) as Record<string, unknown>;
      } catch {
        args = {};
      }
    }
    return {
      name: String(t.function?.name ?? ""),
      arguments: args,
    };
  });

  let i = startIdx + 1;
  const resultsByToolCallId = new Map<string, Record<string, unknown>>();
  while (i < raw.length && String(raw[i]?.role ?? "") === "tool") {
    const tr = raw[i] as Record<string, unknown>;
    const tid = String(tr.tool_call_id ?? "");
    const parsed = parsePersistedToolMessageContent(tr.content);
    if (tid) resultsByToolCallId.set(tid, parsed);
    i++;
  }

  for (let k = 0; k < merged.length; k++) {
    const id = toolsOpenAi[k]?.id;
    if (id && resultsByToolCallId.has(id)) {
      merged[k].result = resultsByToolCallId.get(id);
    }
  }

  return { endIdx: i, merged };
}

/**
 * Converts persisted OpenAI-format rows into UI rows.
 * Collapses a full user turn (multiple assistant rows + tools) into one assistant bubble when the
 * final assistant message carries ``_arth_timeline``.
 */
export function normalizeOpenAiMessagesToUi(
  raw: Record<string, unknown>[],
): ChatMessageUi[] {
  const result: ChatMessageUi[] = [];
  let i = 0;

  while (i < raw.length) {
    const m = raw[i];
    const role = String(m.role ?? "");

    if (role === "user") {
      result.push({
        id: uid(),
        role: "user",
        content: String(m.content ?? ""),
      });
      i++;
      continue;
    }

    if (role === "assistant") {
      const row = m as Record<string, unknown>;
      const tcRaw = row.tool_calls as unknown;
      const hasToolCalls = Array.isArray(tcRaw) && tcRaw.length > 0;

      if (!hasToolCalls) {
        const activity = parseActivityTimeline(row._arth_timeline);
        const persistedThinking =
          typeof row._arth_thinking === "string" && row._arth_thinking.trim().length > 0
            ? row._arth_thinking
            : undefined;
        result.push({
          id: uid(),
          role: "assistant",
          content: String(row.content ?? ""),
          ...(activity?.length
            ? { activity }
            : {
                ...(persistedThinking ? { thinking: persistedThinking } : {}),
              }),
        });
        i++;
        continue;
      }

      /** Merge every assistant+tool block until the natural-language reply (no tool_calls). */
      const allMerged: ToolCallUi[] = [];
      let j = i;
      let sawFinal = false;
      while (j < raw.length) {
        const a = raw[j] as Record<string, unknown>;
        if (String(a.role ?? "") !== "assistant") break;
        const hasTc = Array.isArray(a.tool_calls) && (a.tool_calls as unknown[]).length > 0;
        if (!hasTc) {
          const activity = parseActivityTimeline(a._arth_timeline);
          const persistedThinking =
            typeof a._arth_thinking === "string" && a._arth_thinking.trim().length > 0
              ? a._arth_thinking
              : undefined;
          result.push({
            id: uid(),
            role: "assistant",
            content: String(a.content ?? ""),
            ...(activity?.length
              ? { activity }
              : {
                  ...(persistedThinking ? { thinking: persistedThinking } : {}),
                  ...(allMerged.length ? { toolCalls: allMerged } : {}),
                }),
          });
          sawFinal = true;
          j++;
          break;
        }
        const { endIdx, merged } = mergeAssistantToolBlock(a, raw, j);
        allMerged.push(...merged);
        j = endIdx;
      }
      if (!sawFinal && allMerged.length) {
        result.push({
          id: uid(),
          role: "assistant",
          content: "",
          toolCalls: allMerged,
        });
      }
      i = j;
      continue;
    }

    i++;
  }

  return result;
}
