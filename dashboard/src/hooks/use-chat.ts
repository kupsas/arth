"use client";

/**
 * WebSocket hook for the Arth agent chat — wires server events into UI messages.
 *
 * Connection URL follows ``NEXT_PUBLIC_API_URL`` / same-origin rules (see ``api-base.ts``).
 * When the URL has no ``session_id``, FastAPI creates a thread and emits ``session_ready``.
 *
 * In same-origin mode, the WS bypasses the Next.js proxy (which can't upgrade
 * WebSocket) and connects directly to FastAPI.  A one-time auth ticket fetched
 * via REST (where the httpOnly cookie *does* travel through the proxy) is passed
 * as ``?ticket=`` so FastAPI can authenticate the connection.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiViaSameOrigin, buildChatWebSocketUrl } from "@/lib/api-base";
import type {
  ActivitySegment,
  ChatMessageUi,
  ClientChatWireMessage,
  LiveTool,
  ToolCallUi,
} from "@/lib/chat-types";
import { normalizeOpenAiMessagesToUi } from "@/lib/chat-types";
import { fetchChatSession, fetchWsTicket } from "@/lib/api";

export type ChatConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "error";

function uuid(): string {
  return crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function cloneToolUi(t: ToolCallUi): ToolCallUi {
  return {
    name: t.name,
    arguments: { ...t.arguments },
    ...(t.result !== undefined
      ? {
          result: { ...t.result } as Record<string, unknown>,
          duration_ms: t.duration_ms,
        }
      : {}),
  };
}

export function useChat(
  sessionIdProp: string | undefined,
  onSessionReady?: (sessionId: string) => void,
) {
  const onReadyRef = useRef(onSessionReady);
  useEffect(() => {
    onReadyRef.current = onSessionReady;
  }, [onSessionReady]);
  const [messages, setMessages] = useState<ChatMessageUi[]>([]);
  const [connection, setConnection] = useState<ChatConnectionStatus>("connecting");
  const [isGenerating, setIsGenerating] = useState(false);
  /** True once we have received at least one ``token`` frame for the current turn (hides the thinking strip). */
  const [isResponseStreaming, setIsResponseStreaming] = useState(false);
  /** Tool names shown live under “Arth is thinking…” while the turn runs (mirrors WS events). */
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  /** Ephemeral model reasoning (WebSocket ``thinking``); cleared each turn / step boundary. */
  const [liveThinking, setLiveThinking] = useState("");
  /** True while ``thinking`` chunks are arriving before ``thinking_done``. */
  const [isThinking, setIsThinking] = useState(false);
  /** Interleaved thinking + tool segments completed so far this turn (chronological lane). */
  const [liveActivitySegments, setLiveActivitySegments] = useState<ActivitySegment[]>([]);
  /** Tools in the current ReAct step after ``toolsMarkRef`` (in-flight, for the live lane). */
  const [liveWipTools, setLiveWipTools] = useState<ToolCallUi[]>([]);
  const [lastError, setLastError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  /**
   * After ``thinking_done``, the next ``thinking`` frame starts a new ReAct step —
   * replace ``liveThinking`` instead of appending.  Ref tracks “same step” vs “new step”.
   */
  const isThinkingLiveRef = useRef(false);
  /**
   * Full reasoning text for the current user turn (all steps), appended every ``thinking`` chunk.
   * Copied onto the final assistant ``ChatMessageUi.thinking`` on ``response``, then cleared.
   */
  const turnThinkingRef = useRef("");
  /** Reasoning accumulated for the *current* ``thinking`` burst (until ``thinking_done``). */
  const turnStepThinkingRef = useRef("");
  /** Completed segments for this turn — mirrors what we persist as ``_arth_timeline``. */
  const activityTimelineRef = useRef<ActivitySegment[]>([]);
  /**
   * Index into ``liveAssistantRef.tools``: tools at or after this index belong to the current
   * ReAct step; earlier entries are already flushed into ``activityTimelineRef``.
   */
  const toolsMarkRef = useRef(0);
  /** Assistant row being filled for the current turn (tools + final text). */
  const liveAssistantRef = useRef<{ id: string; tools: ToolCallUi[] } | null>(
    null,
  );
  /** Id of the assistant bubble we append ``token`` deltas into (cleared on ``response`` / errors). */
  const streamDraftIdRef = useRef<string | null>(null);

  const flushPendingToolsToActivity = useCallback(() => {
    const live = liveAssistantRef.current;
    const tools = live?.tools ?? [];
    if (tools.length <= toolsMarkRef.current) return;
    const slice = tools.slice(toolsMarkRef.current).map(cloneToolUi);
    activityTimelineRef.current = [
      ...activityTimelineRef.current,
      { kind: "tools", tools: slice },
    ];
    setLiveActivitySegments([...activityTimelineRef.current]);
    toolsMarkRef.current = tools.length;
  }, []);

  const pushActivitySegment = useCallback((seg: ActivitySegment) => {
    activityTimelineRef.current = [...activityTimelineRef.current, seg];
    setLiveActivitySegments([...activityTimelineRef.current]);
  }, []);

  const syncWipTools = useCallback(() => {
    const live = liveAssistantRef.current;
    const tools = live?.tools ?? [];
    setLiveWipTools(tools.slice(toolsMarkRef.current).map(cloneToolUi));
  }, []);

  const resetTurnActivity = useCallback(() => {
    activityTimelineRef.current = [];
    setLiveActivitySegments([]);
    toolsMarkRef.current = 0;
    turnStepThinkingRef.current = "";
    setLiveWipTools([]);
  }, []);

  /** Hydrate transcript when switching threads (REST — same rows the agent loads server-side). */
  useEffect(() => {
    if (!sessionIdProp) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    fetchChatSession(sessionIdProp)
      .then((d) => {
        if (!cancelled)
          setMessages(normalizeOpenAiMessagesToUi(d.messages ?? []));
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionIdProp]);

  /** One WebSocket per ``sessionIdProp`` (selected thread or "new").
   *  In same-origin mode, fetch a one-time ticket via REST first (the cookie
   *  travels through the proxy), then pass it as ``?ticket=`` to FastAPI. */
  useEffect(() => {
    let cancelled = false;
    setConnection("connecting");
    setLastError(null);

    function openSocket(ticket?: string) {
      if (cancelled) return;
      const url = buildChatWebSocketUrl(sessionIdProp, ticket);
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnection("open");
      };

      ws.onclose = () => {
        setConnection("closed");
        if (wsRef.current === ws) wsRef.current = null;
      };

      ws.onerror = () => {
        setConnection("error");
        setLastError(
          "Can't connect to Arth right now. Make sure the app is running and try again.",
        );
      };

      ws.onmessage = (evt) => {
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(String(evt.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        const typ = String(data.type ?? "");

        if (typ === "session_ready") {
          const sid = String(data.session_id ?? "");
          if (sid) onReadyRef.current?.(sid);
          return;
        }

        if (typ === "screening_blocked") {
          const msg = String(data.message ?? "");
          setMessages((prev) => [
            ...prev,
            { id: uuid(), role: "assistant", content: msg },
          ]);
          setIsGenerating(false);
          setIsResponseStreaming(false);
          liveAssistantRef.current = null;
          streamDraftIdRef.current = null;
          setLiveTools([]);
          isThinkingLiveRef.current = false;
          setLiveThinking("");
          setIsThinking(false);
          turnThinkingRef.current = "";
          resetTurnActivity();
          return;
        }

        if (typ === "thinking") {
          const chunk = String(data.content ?? "");
          if (!chunk) return;
          setIsGenerating(true);
          if (!isThinkingLiveRef.current) {
            flushPendingToolsToActivity();
            turnStepThinkingRef.current = "";
          }
          turnStepThinkingRef.current += chunk;
          turnThinkingRef.current += chunk;
          isThinkingLiveRef.current = true;
          setLiveThinking(turnStepThinkingRef.current);
          setIsThinking(true);
          syncWipTools();
          return;
        }

        if (typ === "thinking_done") {
          const buf = turnStepThinkingRef.current.trim();
          turnStepThinkingRef.current = "";
          if (buf) {
            pushActivitySegment({ kind: "thinking", content: buf });
          }
          isThinkingLiveRef.current = false;
          setLiveThinking("");
          setIsThinking(false);
          syncWipTools();
          return;
        }

        if (typ === "tool_call_started") {
          setIsGenerating(true);
          if (!liveAssistantRef.current) {
            liveAssistantRef.current = { id: uuid(), tools: [] };
          }
          liveAssistantRef.current.tools.push({
            name: String(data.tool_name ?? ""),
            arguments:
              typeof data.arguments === "object" &&
              data.arguments !== null &&
              !Array.isArray(data.arguments)
                ? (data.arguments as Record<string, unknown>)
                : {},
          });
          setLiveTools((prev) => [
            ...prev,
            { name: String(data.tool_name ?? ""), status: "running" },
          ]);
          syncWipTools();
          return;
        }

        if (typ === "token") {
          const piece = String((data as { token?: string }).token ?? "");
          if (!piece) return;
          setIsGenerating(true);
          setIsResponseStreaming(true);
          if (!liveAssistantRef.current) {
            liveAssistantRef.current = { id: uuid(), tools: [] };
          }
          const live = liveAssistantRef.current;
          const existingDraft = streamDraftIdRef.current;
          if (!existingDraft) {
            streamDraftIdRef.current = live.id;
            const toolCalls =
              live.tools.length > 0
                ? live.tools.map((t) => ({
                    name: t.name,
                    arguments: { ...t.arguments },
                    ...(t.result !== undefined
                      ? {
                          result: { ...t.result } as Record<string, unknown>,
                          duration_ms: t.duration_ms,
                        }
                      : {}),
                  }))
                : undefined;
            setMessages((prev) => [
              ...prev,
              {
                id: live.id,
                role: "assistant",
                content: piece,
                isStreaming: true,
                ...(toolCalls && toolCalls.length > 0 ? { toolCalls } : {}),
              },
            ]);
            return;
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === existingDraft
                ? { ...m, content: m.content + piece, isStreaming: true }
                : m,
            ),
          );
          return;
        }

        if (typ === "tool_call_completed") {
          const tools = liveAssistantRef.current?.tools;
          if (tools?.length) {
            const name = String(data.tool_name ?? "");
            const result =
              typeof data.result === "object" &&
              data.result !== null &&
              !Array.isArray(data.result)
                ? (data.result as Record<string, unknown>)
                : {};
            const duration_ms = Number(data.duration_ms ?? 0);
            for (let i = tools.length - 1; i >= 0; i--) {
              if (tools[i].name === name && tools[i].result === undefined) {
                tools[i].result = result;
                tools[i].duration_ms = duration_ms;
                break;
              }
            }
          }
          setLiveTools((prev) => {
            const next = [...prev];
            const name = String(data.tool_name ?? "");
            for (let i = next.length - 1; i >= 0; i--) {
              if (next[i].name === name && next[i].status === "running") {
                next[i] = { ...next[i], status: "done" };
                break;
              }
            }
            return next;
          });
          syncWipTools();
          return;
        }

        if (typ === "response") {
          const text = String(data.content ?? "");
          /**
           * Read refs here synchronously — not inside ``setMessages``.
           * The next WebSocket frame is often ``done``, which clears refs; React may
           * run the state updater later, so reading refs inside the updater loses tools.
           */
          const live = liveAssistantRef.current;
          const draftId = streamDraftIdRef.current;

          const toolsSnapshot = live?.tools ?? [];
          const finalActivity: ActivitySegment[] = [...activityTimelineRef.current];
          if (toolsSnapshot.length > toolsMarkRef.current) {
            finalActivity.push({
              kind: "tools",
              tools: toolsSnapshot.slice(toolsMarkRef.current).map(cloneToolUi),
            });
          }
          const stepLeft = turnStepThinkingRef.current.trim();
          if (stepLeft) {
            finalActivity.push({ kind: "thinking", content: stepLeft });
          }

          const thinkingText = turnThinkingRef.current.trim();
          turnThinkingRef.current = "";
          turnStepThinkingRef.current = "";

          streamDraftIdRef.current = null;
          liveAssistantRef.current = null;
          setIsResponseStreaming(false);
          resetTurnActivity();

          const toolCallsFromLive =
            live && live.tools.length > 0
              ? live.tools.map((t) => ({
                  name: t.name,
                  arguments: { ...t.arguments },
                  ...(t.result !== undefined
                    ? {
                        result: { ...t.result } as Record<string, unknown>,
                        duration_ms: t.duration_ms,
                      }
                    : {}),
                }))
              : undefined;

          const streamExtras =
            finalActivity.length > 0
              ? { activity: finalActivity }
              : {
                  ...(thinkingText ? { thinking: thinkingText } : {}),
                  ...(toolCallsFromLive && toolCallsFromLive.length > 0
                    ? { toolCalls: toolCallsFromLive }
                    : {}),
                };

          // Streamed assistant text: patch the draft row (``token`` frames already built it).
          if (draftId) {
            setMessages((prev) => {
              const ix = prev.findIndex((m) => m.id === draftId);
              if (ix >= 0) {
                return prev.map((m, j) =>
                  j === ix
                    ? {
                        ...m,
                        role: "assistant",
                        content: text,
                        isStreaming: false,
                        ...streamExtras,
                      }
                    : m,
                );
              }
              return [
                ...prev,
                {
                  id: draftId,
                  role: "assistant" as const,
                  content: text,
                  ...streamExtras,
                },
              ];
            });
            return;
          }

          const assistantId = live?.id ?? uuid();
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: "assistant",
              content: text,
              ...streamExtras,
            },
          ]);
          return;
        }

        if (typ === "error") {
          const msg = String(data.message ?? "Error");
          liveAssistantRef.current = null;
          streamDraftIdRef.current = null;
          setIsResponseStreaming(false);
          setMessages((prev) => [
            ...prev,
            { id: uuid(), role: "assistant", content: msg },
          ]);
          setLiveTools([]);
          isThinkingLiveRef.current = false;
          setLiveThinking("");
          setIsThinking(false);
          turnThinkingRef.current = "";
          resetTurnActivity();
          return;
        }

        if (typ === "done") {
          setIsGenerating(false);
          setIsResponseStreaming(false);
          setLiveTools([]);
          isThinkingLiveRef.current = false;
          setLiveThinking("");
          setIsThinking(false);
          turnThinkingRef.current = "";
          resetTurnActivity();
          return;
        }

        // llm_step — UI ignores (``token`` + ``response`` carry assistant text).
      };
    }

    if (apiViaSameOrigin) {
      fetchWsTicket()
        .then((res) => openSocket(res.ticket))
        .catch(() => {
          if (!cancelled) {
            setConnection("error");
            setLastError("Couldn't start the chat. Try refreshing the page.");
          }
        });
    } else {
      openSocket();
    }

    return () => {
      cancelled = true;
      const ws = wsRef.current;
      if (ws) {
        ws.close();
        if (wsRef.current === ws) wsRef.current = null;
      }
    };
  }, [
    sessionIdProp,
    flushPendingToolsToActivity,
    pushActivitySegment,
    syncWipTools,
    resetTurnActivity,
  ]);

  const sendMessage = useCallback((raw: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const content = raw.trim();
    if (!content) return;

    setMessages((prev) => [
      ...prev,
      { id: uuid(), role: "user", content },
    ]);
    setIsGenerating(true);
    setIsResponseStreaming(false);
    setLiveTools([]);
    isThinkingLiveRef.current = false;
    setLiveThinking("");
    setIsThinking(false);
    turnThinkingRef.current = "";
    resetTurnActivity();
    // New user turn — drop any stale assistant draft (e.g. stopped mid-stream).
    liveAssistantRef.current = null;
    streamDraftIdRef.current = null;

    const payload: ClientChatWireMessage = { type: "send_message", content };
    ws.send(JSON.stringify(payload));
  }, [resetTurnActivity]);

  const stopGenerating = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const payload: ClientChatWireMessage = { type: "stop" };
    ws.send(JSON.stringify(payload));
  }, []);

  return {
    messages,
    connection,
    isGenerating,
    isResponseStreaming,
    liveTools,
    liveThinking,
    isThinking,
    liveActivitySegments,
    liveWipTools,
    lastError,
    sendMessage,
    stopGenerating,
  };
}
