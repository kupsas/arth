"use client";

import { useState } from "react";
import type { KeyboardEvent } from "react";
import { Sparkles, Send, Loader2, Clock, X, MessageSquare } from "lucide-react";

import type {
  ActivitySegment,
  ChatMessageUi,
  ChatSessionSummary,
  LiveTool,
  ToolCallUi,
} from "@/lib/chat-types";
import type { ProviderFailurePayload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { ChatPanel } from "./chat-panel";
import { SessionSidebar } from "./session-sidebar";

/** Three short, distinct prompts that showcase different Arth capabilities. */
const LANDING_STARTERS = [
  "How much did I spend on food this month?",
  "What's my net worth?",
  "Where am I overspending?",
];

function ChatLanding({
  sessions,
  sessionsLoading,
  connectionOk,
  isGenerating,
  onSend,
  onSelectSession,
}: {
  sessions: ChatSessionSummary[];
  sessionsLoading: boolean;
  connectionOk: boolean;
  isGenerating: boolean;
  onSend: (text: string) => void;
  onSelectSession: (id: string) => void;
}) {
  const [text, setText] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  /** Only sessions with actual messages — empty drafts are not useful history entries. */
  const pastSessions = sessions.filter((s) => (s.message_count ?? 0) > 0);

  function submit() {
    const t = text.trim();
    if (!t || !connectionOk || isGenerating) return;
    onSend(t);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="relative flex min-h-[calc(100vh-8rem)] flex-col items-center justify-center px-4 py-12 overflow-hidden">
      {/* ── History toggle button (top-left) ─────────────────────────── */}
      <div className="absolute left-4 top-4">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="gap-1.5 text-muted-foreground hover:text-foreground"
          onClick={() => setShowHistory(true)}
          aria-label="View past chats"
        >
          <Clock className="size-3.5" />
          Past chats
        </Button>
      </div>

      {/* ── History panel ──────────────────────────────────────────────── */}
      {showHistory && (
        <>
          {/* Backdrop — click anywhere outside the panel to close */}
          <div
            className="absolute inset-0 z-10"
            aria-hidden
            onClick={() => setShowHistory(false)}
          />

          <aside className="absolute inset-y-0 left-0 z-20 flex w-[13rem] flex-col border-r border-border bg-card shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-3">
              <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/70">
                Past chats
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7 text-muted-foreground"
                onClick={() => setShowHistory(false)}
                aria-label="Close"
              >
                <X className="size-4" />
              </Button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-2">
              {sessionsLoading && (
                <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground">
                  <Loader2 className="size-3.5 animate-spin" />
                  Loading…
                </div>
              )}

              {!sessionsLoading && pastSessions.length === 0 && (
                <p className="px-2 py-4 text-xs text-muted-foreground">
                  No past chats yet. Start a conversation above!
                </p>
              )}

              {!sessionsLoading &&
                pastSessions.map((s) => {
                  const label = (s.title && s.title.trim()) || "New conversation";
                  return (
                    <button
                      key={s.id}
                      type="button"
                      title={label}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                      onClick={() => {
                        onSelectSession(s.id);
                        setShowHistory(false);
                      }}
                    >
                      <MessageSquare className="size-3.5 shrink-0 opacity-60" />
                      <span className="truncate">{label}</span>
                    </button>
                  );
                })}
            </div>
          </aside>
        </>
      )}

      {/* ── Branding ───────────────────────────────────────────────────── */}
      <div className="mb-10 flex flex-col items-center gap-3 text-center">
        <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Sparkles className="size-6" />
        </div>
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Ask Arth</h1>
          <p className="text-base text-muted-foreground">
            Poocho kuch bhi — spending, goals, investments.
          </p>
        </div>
      </div>

      {/* ── Input box ──────────────────────────────────────────────────── */}
      <div className="w-full max-w-2xl space-y-4">
        <div className="relative rounded-2xl border border-border bg-card shadow-sm transition-all duration-150 focus-within:border-ring/60 focus-within:shadow-md">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Kuch poocho — spending, savings, investments…"
            disabled={!connectionOk || isGenerating}
            rows={3}
            className="block w-full resize-none rounded-2xl bg-transparent px-4 pt-4 pb-14 text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
          />
          <div className="absolute bottom-3 right-3 flex items-center gap-2">
            {!connectionOk && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                Connecting…
              </span>
            )}
            <Button
              type="button"
              size="icon"
              onClick={submit}
              disabled={!connectionOk || isGenerating || !text.trim()}
              aria-label="Send"
            >
              <Send className="size-4" />
            </Button>
          </div>
        </div>

        {/* Quick-fire starter chips */}
        <div className="flex flex-wrap justify-center gap-2">
          {LANDING_STARTERS.map((q) => (
            <button
              key={q}
              type="button"
              disabled={!connectionOk || isGenerating}
              onClick={() => onSend(q)}
              className="rounded-full border border-border bg-background px-4 py-1.5 text-sm text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ChatLayout({
  sessions,
  sessionsLoading,
  activeSessionId,
  messages,
  connectionOk,
  isGenerating,
  isResponseStreaming,
  liveTools,
  liveThinking,
  isThinking,
  liveActivitySegments,
  liveWipTools,
  lastError,
  agentPausedFailures,
  onDismissAgentPaused,
  onRetryAgentPaused,
  onSwitchProviderKeys,
  onNewChat,
  onSelectSession,
  onArchiveSession,
  onSend,
  onStop,
}: {
  sessions: ChatSessionSummary[];
  sessionsLoading: boolean;
  activeSessionId: string | undefined;
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
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onArchiveSession: (id: string) => void;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  /**
   * Switch to the chat layout as soon as the user sends their first message,
   * even before the first response arrives.
   */
  const inConversation = messages.length > 0 || isGenerating;

  if (!inConversation) {
    return (
      <ChatLanding
        sessions={sessions}
        sessionsLoading={sessionsLoading}
        connectionOk={connectionOk}
        isGenerating={isGenerating}
        onSend={onSend}
        onSelectSession={onSelectSession}
      />
    );
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] min-h-[28rem] gap-0 overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        loading={sessionsLoading}
        onNewChat={onNewChat}
        onSelectSession={onSelectSession}
        onArchiveSession={onArchiveSession}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col p-4">
        <ChatPanel
          messages={messages}
          connectionOk={connectionOk}
          isGenerating={isGenerating}
          isResponseStreaming={isResponseStreaming}
          liveTools={liveTools}
          liveThinking={liveThinking}
          isThinking={isThinking}
          liveActivitySegments={liveActivitySegments}
          liveWipTools={liveWipTools}
          lastError={lastError}
          agentPausedFailures={agentPausedFailures}
          onDismissAgentPaused={onDismissAgentPaused}
          onRetryAgentPaused={onRetryAgentPaused}
          onSwitchProviderKeys={onSwitchProviderKeys}
          onSend={onSend}
          onStop={onStop}
        />
      </div>
    </div>
  );
}
