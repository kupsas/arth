"use client";

import { useEffect, useRef } from "react";

import type {
  ActivitySegment,
  ChatMessageUi,
  LiveTool,
  ToolCallUi,
} from "@/lib/chat-types";

import { MessageBubble } from "./message-bubble";
import { StreamingIndicator } from "./streaming-indicator";

export function MessageList({
  messages,
  isGenerating,
  isResponseStreaming,
  liveTools,
  liveThinking = "",
  isThinking = false,
  liveActivitySegments = [],
  liveWipTools = [],
}: {
  messages: ChatMessageUi[];
  isGenerating: boolean;
  /** True when ``token`` frames are updating the last assistant bubble. */
  isResponseStreaming?: boolean;
  /** Live tool names while the assistant turn is in flight (from WebSocket). */
  liveTools?: LiveTool[];
  liveThinking?: string;
  isThinking?: boolean;
  liveActivitySegments?: ActivitySegment[];
  liveWipTools?: ToolCallUi[];
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: isResponseStreaming ? "auto" : "smooth",
    });
  }, [
    messages,
    isGenerating,
    isResponseStreaming,
    liveTools?.length,
    liveThinking,
    liveActivitySegments.length,
    liveWipTools.length,
  ]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto pr-1">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      {isGenerating && (
        <StreamingIndicator
          liveTools={liveTools}
          liveThinking={liveThinking}
          isThinking={isThinking}
          isResponseStreaming={isResponseStreaming}
          liveActivitySegments={liveActivitySegments}
          liveWipSegmentTools={liveWipTools}
        />
      )}
      <div ref={bottomRef} className="h-1 shrink-0" aria-hidden />
    </div>
  );
}
