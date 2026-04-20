"use client";

/**
 * Shown while the agent is still working (submitted, not yet ``done``).
 * Prefers a chronological lane (thinking → tools → thinking → …) when the hook
 * supplies ``liveActivitySegments`` + optional in-flight tools; otherwise falls
 * back to the legacy flat thinking strip + tool name list.
 */

import type { ActivitySegment, LiveTool, ToolCallUi } from "@/lib/chat-types";
import { formatToolLabel } from "@/lib/format-tool-label";
import { cn } from "@/lib/utils";

import { ThinkingBlock } from "./thinking-block";
import { ToolCallGroup } from "./tool-call-group";

export function StreamingIndicator({
  className,
  liveTools,
  liveThinking = "",
  isThinking = false,
  /** When the assistant bubble is already growing from ``token`` frames, hide this strip. */
  isResponseStreaming = false,
  /** Completed interleaved segments for this turn (thinking ↔ tools). */
  liveActivitySegments = [] as ActivitySegment[],
  /**
   * Tools for the *current* step after the last flushed boundary — same lane as
   * ``liveActivitySegments``, still in progress.
   */
  liveWipSegmentTools = [] as ToolCallUi[],
}: {
  className?: string;
  /** Tool names emitted over the socket during this turn (running → done). */
  liveTools?: LiveTool[];
  /** Ephemeral model reasoning text (WebSocket ``thinking`` frames). */
  liveThinking?: string;
  /** True while reasoning chunks are still arriving for the current step. */
  isThinking?: boolean;
  isResponseStreaming?: boolean;
  liveActivitySegments?: ActivitySegment[];
  liveWipSegmentTools?: ToolCallUi[];
}) {
  const tools = liveTools ?? [];
  const useChronological =
    liveActivitySegments.length > 0 ||
    liveWipSegmentTools.length > 0 ||
    liveThinking.trim().length > 0;

  if (isResponseStreaming) {
    return null;
  }

  if (useChronological) {
    return (
      <div
        className={cn("flex flex-col gap-2 text-xs text-muted-foreground", className)}
        aria-live="polite"
      >
        {liveActivitySegments.map((seg, idx) =>
          seg.kind === "thinking" ? (
            <ThinkingBlock
              key={`seg-${idx}`}
              content={seg.content}
              isLive={false}
              persisted={false}
            />
          ) : (
            <ToolCallGroup key={`seg-${idx}`} tools={seg.tools} />
          ),
        )}
        {liveThinking.trim().length > 0 && (
          <ThinkingBlock content={liveThinking} isLive={isThinking} />
        )}
        {liveWipSegmentTools.length > 0 && <ToolCallGroup tools={liveWipSegmentTools} />}
        <div className="flex items-center gap-1.5">
          <span className="inline-flex gap-0.5">
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80 [animation-delay:-0.2s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80 [animation-delay:-0.1s]" />
            <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80" />
          </span>
          <span>Arth is thinking…</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn("flex flex-col gap-2 text-xs text-muted-foreground", className)}
      aria-live="polite"
    >
      {liveThinking.trim().length > 0 && (
        <ThinkingBlock content={liveThinking} isLive={isThinking} />
      )}
      <div className="flex items-center gap-1.5">
        <span className="inline-flex gap-0.5">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80 [animation-delay:-0.2s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80 [animation-delay:-0.1s]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/80" />
        </span>
        <span>Arth is thinking…</span>
      </div>

      {tools.length > 0 && (
        <ul className="ml-0 flex list-none flex-col gap-1 border-l border-border pl-3">
          {tools.map((t, i) => (
            <li key={`${t.name}-${i}`} className="flex items-center gap-2">
              {t.status === "done" ? (
                <span
                  className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground"
                  aria-hidden
                >
                  ✓
                </span>
              ) : (
                <span
                  className="relative flex h-4 w-4 shrink-0 items-center justify-center"
                  aria-hidden
                >
                  <span className="absolute inset-0 animate-pulse rounded-full bg-primary/25" />
                  <span className="relative h-2 w-2 rounded-full bg-primary/80" />
                </span>
              )}
              <span
                className={
                  t.status === "done"
                    ? "text-muted-foreground/70"
                    : "font-medium text-foreground"
                }
              >
                {formatToolLabel(t.name, i)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
