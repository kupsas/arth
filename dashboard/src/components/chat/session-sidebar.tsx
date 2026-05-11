"use client";

import { Loader2, PenSquare, Trash2 } from "lucide-react";

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
    <aside className="flex w-full max-w-[13rem] shrink-0 flex-col border-r border-border bg-muted/20">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-3">
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground/70">
          Chats
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="size-7 text-muted-foreground hover:text-foreground"
          onClick={onNewChat}
          aria-label="New chat"
          title="New chat"
        >
          <PenSquare className="size-4" />
        </Button>
      </div>

      {/* Session list */}
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-2">
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
              "New conversation";
            return (
              <div
                key={s.id}
                className={cn(
                  "group flex items-center gap-1 rounded-lg",
                  active
                    ? "bg-background shadow-sm ring-1 ring-border"
                    : "hover:bg-muted/60",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelectSession(s.id)}
                  className={cn(
                    "min-w-0 flex-1 truncate rounded-lg px-2 py-2 text-left text-xs transition-colors",
                    active ? "font-medium text-foreground" : "text-muted-foreground",
                  )}
                  title={label}
                >
                  {label}
                </button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                  aria-label="Delete chat"
                  onClick={(e) => {
                    e.stopPropagation();
                    onArchiveSession(s.id);
                  }}
                >
                  <Trash2 className="size-3 text-muted-foreground" />
                </Button>
              </div>
            );
          })}
      </div>
    </aside>
  );
}
