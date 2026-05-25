"use client";

/**
 * WebSocket hook for the Arth agent chat — wires server events into UI messages.
 *
 * Connection URL follows ``NEXT_PUBLIC_API_URL`` / same-origin rules (see ``api-base.ts``).
 * When the URL has no ``session_id``, FastAPI creates a thread and emits ``session_ready``.
 * The chat page avoids that when an empty draft already exists (see ``/chat`` bootstrap).
 *
 * When ``sessionIdProp`` is set, the WebSocket waits until ``GET /api/chat/sessions/{id}``
 * succeeds so a wiped demo DB or stale ``?session=`` cannot open a doomed socket before
 * the URL is cleared (avoids WS ↔ 404 races).
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { apiViaSameOrigin, buildChatWebSocketUrl } from "@/lib/api-base";
import type {
  ActivitySegment,
  ChatMessageUi,
  ClientChatWireMessage,
  LiveTool,
  ToolCallUi,
} from "@/lib/chat-types";
import { normalizeOpenAiMessagesToUi } from "@/lib/chat-types";
import {
  ApiError,
  fetchChatSession,
  fetchWsTicket,
  type ProviderFailurePayload,
} from "@/lib/api";
import { isDemoMode } from "@/lib/demo";
import { formatCountdown, getDemoRateLimitState, recordDemoMessage } from "@/lib/demo-rate-limit";
import posthog from "posthog-js";

export type ChatConnectionStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "error";

/** Stop retrying after this many consecutive "session not found" 404s. */
const MAX_SESSION_NOT_FOUND = 3;

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

export type UseChatOptions = {
  /**
   * When false, no WebSocket is opened (used while the page decides whether to reuse
   * an empty draft session from REST instead of creating another server-side thread).
   */
  enabled?: boolean;
  /**
   * Called when ``GET /api/chat/sessions/{id}`` returns 404 — e.g. bookmarked ``?session=`` from an
   * old DB while demo reset issued a new SQLite file. Parent should drop the stale query param.
   */
  onSessionNotFound?: () => void;
  /**
   * Incremented when the user clicks "New chat" while already on ``?new=1`` so we still
   * clear the transcript and reopen the WebSocket (the URL alone would not change).
   */
  newChatRequestId?: number;
};

export function useChat(
  sessionIdProp: string | undefined,
  onSessionReady?: (sessionId: string) => void,
  options?: UseChatOptions,
) {
  const enabled = options?.enabled ?? true;
  const newChatRequestId = options?.newChatRequestId ?? 0;
  const onReadyRef = useRef(onSessionReady);
  useEffect(() => {
    onReadyRef.current = onSessionReady;
  }, [onSessionReady]);
  const onSessionNotFoundRef = useRef(options?.onSessionNotFound);
  useEffect(() => {
    onSessionNotFoundRef.current = options?.onSessionNotFound;
  }, [options?.onSessionNotFound]);
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
  /** Every LiteLLM model in the chain failed — structured reasons from ``agent_paused`` WS frame. */
  const [agentPausedFailures, setAgentPausedFailures] = useState<ProviderFailurePayload[] | null>(
    null,
  );
  /** Last user message text — used to retry after fixing keys without retyping. */
  const lastUserMessageRef = useRef<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  /**
   * Circuit breaker: counts consecutive "session not found" 404s.  If this
   * exceeds ``MAX_SESSION_NOT_FOUND`` without a successful load or
   * ``session_ready``, we stop retrying and show a final error message.
   */
  const sessionNotFoundCountRef = useRef(0);
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
  /**
   * Session ID the current WebSocket received via ``session_ready``.
   *
   * When the server creates a new chat session on a WS opened without ``session_id``,
   * it sends back ``session_ready`` with the new ID.  The parent then updates the URL
   * (``router.replace``), which changes ``sessionIdProp``, which re-triggers this
   * effect.  Without this ref, the effect cleanup would tear down the perfectly-good
   * WS and open a redundant second connection — causing the "multiple session IDs +
   * 404" cascade the user sees in Docker.
   *
   * Flow:
   *   1. ``session_ready`` handler sets this ref.
   *   2. Effect cleanup sees ref is set → keeps WS alive (no ``close()``).
   *   3. Next effect invocation sees ``ref === sessionIdProp`` → skips reconnection.
   */
  const serverAssignedSessionRef = useRef<string | null>(null);

  /**
   * When the URL (or parent) passes a concrete ``session_id``, we must not open a
   * WebSocket against it until ``GET /api/chat/sessions/{id}`` succeeds. Otherwise
   * a stale bookmark (e.g. demo DB wiped after Fly machine sleep) opens WS →
   * server closes with policy error while REST 404 races to clear the URL →
   * reconnect loop.
   *
   * ``true`` when there is no session id to verify, or when REST has confirmed
   * the current id, or when this id was just assigned by ``session_ready`` on an
   * already-open socket (see ``useLayoutEffect`` + WebSocket effect handshake).
   */
  const [restSessionGateOk, setRestSessionGateOk] = useState(true);

  /**
   * Run before the WebSocket ``useEffect`` so ``restSessionGateOk`` is false for a
   * newly-selected thread id in the same commit (avoids opening WS before REST runs).
   */
  useLayoutEffect(() => {
    if (!sessionIdProp) {
      setRestSessionGateOk(true);
      return;
    }
    if (serverAssignedSessionRef.current === sessionIdProp) {
      setRestSessionGateOk(true);
    } else {
      setRestSessionGateOk(false);
    }
  }, [sessionIdProp]);

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

  /** Drop in-flight turn UI so ``ChatLayout`` can show the landing (not an empty thread shell). */
  const resetStreamingUi = useCallback(() => {
    setIsGenerating(false);
    setIsResponseStreaming(false);
    setLiveTools([]);
    isThinkingLiveRef.current = false;
    setLiveThinking("");
    setIsThinking(false);
    turnThinkingRef.current = "";
    turnStepThinkingRef.current = "";
    resetTurnActivity();
    liveAssistantRef.current = null;
    streamDraftIdRef.current = null;
    setAgentPausedFailures(null);
    setLastError(null);
  }, [resetTurnActivity]);

  // On unmount, always close the WebSocket regardless of serverAssignedSessionRef.
  // The main WS effect's cleanup may defer closing when a session_ready is in-flight,
  // but if the component is unmounting there is no "next invocation" to pick it up.
  useEffect(() => {
    return () => {
      const ws = wsRef.current;
      if (ws) {
        ws.close();
        wsRef.current = null;
      }
      serverAssignedSessionRef.current = null;
    };
  }, []);

  /** "New chat" while URL is already ``?new=1`` — URL does not change, so reset explicitly. */
  useEffect(() => {
    if (newChatRequestId === 0) return;
    setMessages([]);
    resetStreamingUi();
    sessionNotFoundCountRef.current = 0;
    serverAssignedSessionRef.current = null;
    setRestSessionGateOk(true);
  }, [newChatRequestId, resetStreamingUi]);

  /** Hydrate transcript when switching threads (REST — same rows the agent loads server-side). */
  useEffect(() => {
    if (!sessionIdProp) {
      setMessages([]);
      resetStreamingUi();
      return;
    }
    // Avoid showing the previous thread while the new id loads (or after 404 clears rows).
    setMessages([]);
    resetStreamingUi();
    let cancelled = false;
    fetchChatSession(sessionIdProp)
      .then((d) => {
        if (!cancelled) {
          sessionNotFoundCountRef.current = 0;
          setMessages(normalizeOpenAiMessagesToUi(d.messages ?? []));
          setRestSessionGateOk(true);
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setMessages([]);
        resetStreamingUi();
        setRestSessionGateOk(false);
        if (e instanceof ApiError && e.status === 404) {
          sessionNotFoundCountRef.current += 1;
          if (sessionNotFoundCountRef.current > MAX_SESSION_NOT_FOUND) {
            setConnection("error");
            setLastError(
              "This chat session is gone — probably a server restart. Please start a new chat.",
            );
            return;
          }
          onSessionNotFoundRef.current?.();
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionIdProp, resetStreamingUi]);

  /** One WebSocket per ``sessionIdProp`` (selected thread or "new").
   *  In same-origin mode, fetch a one-time ticket via REST first (the cookie
   *  travels through the proxy), then pass it as ``?ticket=`` to FastAPI. */
  useEffect(() => {
    let cancelled = false;

    const wsEnabled = enabled && restSessionGateOk;

    if (!wsEnabled) {
      serverAssignedSessionRef.current = null;
      const staleWs = wsRef.current;
      if (staleWs) {
        staleWs.close();
        if (wsRef.current === staleWs) wsRef.current = null;
      }
      setConnection("idle");
      setLastError(null);
      return () => {
        cancelled = true;
      };
    }

    // If the current WS already owns this session (assigned via session_ready),
    // keep it alive instead of tearing down and reconnecting.
    {
      const ws = wsRef.current;
      if (
        ws &&
        ws.readyState <= WebSocket.OPEN &&
        serverAssignedSessionRef.current != null &&
        serverAssignedSessionRef.current === sessionIdProp
      ) {
        serverAssignedSessionRef.current = null;
        return () => {
          cancelled = true;
          if (!serverAssignedSessionRef.current) {
            const w = wsRef.current;
            if (w) {
              w.close();
              if (wsRef.current === w) wsRef.current = null;
            }
          }
        };
      }
    }

    // Close any stale WS before opening a fresh one.
    serverAssignedSessionRef.current = null;
    {
      const staleWs = wsRef.current;
      if (staleWs) {
        staleWs.close();
        if (wsRef.current === staleWs) wsRef.current = null;
      }
    }

    setConnection("connecting");
    setLastError(null);

    function openSocket(ticket?: string, arthDemoSid?: string) {
      if (cancelled) return;
      const url = buildChatWebSocketUrl(sessionIdProp, ticket, arthDemoSid);
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnection("open");
      };

      ws.onclose = (evt) => {
        if (wsRef.current === ws) {
          setConnection("closed");
          wsRef.current = null;
        }
        // FastAPI closes with 1008 when the requested chat session row is missing.
        if (evt.code === 1008 && sessionIdProp) {
          onSessionNotFoundRef.current?.();
        }
      };

      ws.onerror = () => {
        posthog.capture("chat_stream_error", {
          session_id: sessionIdProp ?? null,
          recoverable: false,
          source: "websocket_transport",
        });
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
          sessionNotFoundCountRef.current = 0;
          const sid = String(data.session_id ?? "");
          if (sid) {
            serverAssignedSessionRef.current = sid;
            onReadyRef.current?.(sid);
          }
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

        if (typ === "agent_paused") {
          const raw = (data as { failures?: unknown }).failures;
          const failures = Array.isArray(raw)
            ? (raw as ProviderFailurePayload[])
            : [];
          setAgentPausedFailures(failures.length ? failures : null);
          setIsGenerating(false);
          setIsResponseStreaming(false);
          setLiveTools([]);
          isThinkingLiveRef.current = false;
          setLiveThinking("");
          setIsThinking(false);
          turnThinkingRef.current = "";
          resetTurnActivity();
          liveAssistantRef.current = null;
          streamDraftIdRef.current = null;
          return;
        }

        if (typ === "error") {
          const msg = String(data.message ?? "Error");
          // Non-recoverable errors include "session row missing" — parent drops stale
          // ``?session=`` so we start fresh instead of looping on a dead thread id.
          const recoverable = (data as { recoverable?: boolean }).recoverable !== false;
          if (!recoverable) {
            onSessionNotFoundRef.current?.();
            return;
          }
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
          const toolCount = liveAssistantRef.current?.tools.length ?? 0;
          const hasThinking = turnThinkingRef.current.trim().length > 0;
          posthog.capture("chat_stream_completed", {
            session_id: sessionIdProp ?? null,
            tool_count: toolCount,
            has_thinking: hasThinking,
          });
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
        .then((res) => openSocket(res.ticket, res.arth_demo_sid))
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
      // If session_ready just assigned an ID, keep the WS alive so the next
      // effect invocation can detect the match and skip reconnection.
      if (serverAssignedSessionRef.current) return;
      const ws = wsRef.current;
      if (ws) {
        ws.close();
        if (wsRef.current === ws) wsRef.current = null;
      }
    };
  }, [
    sessionIdProp,
    enabled,
    restSessionGateOk,
    newChatRequestId,
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

    // In demo mode, enforce the 30-minute sliding-window rate limit client-side.
    // This check runs BEFORE any message reaches the server so blocked attempts
    // never consume a server turn and cannot be bypassed by resetting the demo DB.
    if (isDemoMode) {
      const rl = getDemoRateLimitState();
      if (rl.isLimited) {
        posthog.capture("demo_rate_limit_hit", {
          messages_used: rl.count,
          ms_until_reset: rl.msUntilReset,
        });
        const countdown = formatCountdown(rl.msUntilReset);
        setMessages((prev) => [
          ...prev,
          { id: uuid(), role: "user", content },
          {
            id: uuid(),
            role: "assistant",
            content: `You've used all ${rl.count} demo messages for this 30-minute window. Come back in **${countdown}** to ask more.`,
          },
        ]);
        return;
      }
      // Record the message now (starts the window on first send).
      recordDemoMessage();
    }

    lastUserMessageRef.current = content;
    setAgentPausedFailures(null);

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

    posthog.capture("chat_message_sent", {
      session_id: sessionIdProp,
      message_length: content.length,
      is_demo: isDemoMode,
    });

    const payload: ClientChatWireMessage = { type: "send_message", content };
    ws.send(JSON.stringify(payload));
  }, [resetTurnActivity, sessionIdProp]);

  const clearAgentPaused = useCallback(() => {
    setAgentPausedFailures(null);
  }, []);

  const retryLastUserMessage = useCallback(() => {
    const t = lastUserMessageRef.current;
    if (t) sendMessage(t);
  }, [sendMessage]);

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
    agentPausedFailures,
    clearAgentPaused,
    retryLastUserMessage,
    sendMessage,
    stopGenerating,
  };
}
