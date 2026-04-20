"use client";

import { Loader2, MessageSquarePlus, Trash2 } from "lucide-react";

import type { ChatSessionSummary } from "@/lib/chat-types";
import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";

export function SessionSidebar({
  sessions,
  activeSessionId,
  loading,
  onNewChat,
  onSelectSession,
  onArchiveSession,
}: {
  sessions: ChatSessionSummary[];
  activeSessionId: string | undefined;
  loading: boolean;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onArchiveSession: (id: string) => void;
}) {
  return (
    <aside className="flex w-full max-w-[14rem] shrink-0 flex-col border-r border-border bg-muted/15">
      <div className="border-b border-border p-2">
        <Button
          type="button"
          variant="secondary"
          className="w-full justify-start gap-2"
          onClick={onNewChat}
        >
          <MessageSquarePlus className="size-4" />
          New chat
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto p-2">
        {loading && (
          <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Loading…
          </div>
        )}
        {!loading &&
          sessions.map((s) => {
            const active = s.id === activeSessionId;
            const label =
              (s.title && s.title.trim()) ||
              `Chat ${s.id.slice(0, 8)}…`;
            return (
              <div
                key={s.id}
                className={cn(
                  "group flex items-center gap-1 rounded-lg border border-transparent",
                  active && "border-border bg-background shadow-sm",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelectSession(s.id)}
                  className={cn(
                    "min-w-0 flex-1 truncate rounded-lg px-2 py-2 text-left text-xs transition hover:bg-muted",
                    active && "font-medium",
                  )}
                  title={label}
                >
                  {label}
                </button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7 shrink-0 opacity-0 transition group-hover:opacity-100"
                  aria-label="Archive chat"
                  onClick={(e) => {
                    e.stopPropagation();
                    onArchiveSession(s.id);
                  }}
                >
                  <Trash2 className="size-3.5 text-muted-foreground" />
                </Button>
              </div>
            );
          })}
      </div>
    </aside>
  );
}
