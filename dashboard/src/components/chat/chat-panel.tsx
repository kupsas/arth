"use client";

import type {
  ActivitySegment,
  ChatMessageUi,
  LiveTool,
  ToolCallUi,
} from "@/lib/chat-types";

import { ProviderPausedDialog } from "@/components/shared/provider-paused-dialog";
import type { ProviderFailurePayload } from "@/lib/api";

import { ChatInput } from "./chat-input";
import { MessageList } from "./message-list";

export function ChatPanel({
  messages,
  connectionOk,
  isGenerating,
  isResponseStreaming,
  liveTools,
  liveThinking = "",
  isThinking = false,
  liveActivitySegments = [],
  liveWipTools = [],
  lastError,
  agentPausedFailures,
  onDismissAgentPaused,
  onRetryAgentPaused,
  onSwitchProviderKeys,
  onSend,
  onStop,
}: {
  messages: ChatMessageUi[];
  connectionOk: boolean;
  isGenerating: boolean;
  isResponseStreaming?: boolean;
  liveTools?: LiveTool[];
  liveThinking?: string;
  isThinking?: boolean;
  liveActivitySegments?: ActivitySegment[];
  liveWipTools?: ToolCallUi[];
  lastError: string | null;
  agentPausedFailures: ProviderFailurePayload[] | null;
  onDismissAgentPaused: () => void;
  onRetryAgentPaused: () => void;
  onSwitchProviderKeys: () => void;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3">
      <ProviderPausedDialog
        open={agentPausedFailures !== null && agentPausedFailures.length > 0}
        onOpenChange={(o) => {
          if (!o) onDismissAgentPaused();
        }}
        failures={agentPausedFailures ?? []}
        context="chat"
        onSwitchProvider={onSwitchProviderKeys}
        onTryAgain={onRetryAgentPaused}
      />

      {lastError && (
        <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {lastError}
        </p>
      )}

      <MessageList
        messages={messages}
        isGenerating={isGenerating}
        isResponseStreaming={isResponseStreaming}
        liveTools={liveTools}
        liveThinking={liveThinking}
        isThinking={isThinking}
        liveActivitySegments={liveActivitySegments}
        liveWipTools={liveWipTools}
      />

      <ChatInput
        disabled={!connectionOk}
        isGenerating={isGenerating}
        onSend={onSend}
        onStop={onStop}
      />
    </section>
  );
}
