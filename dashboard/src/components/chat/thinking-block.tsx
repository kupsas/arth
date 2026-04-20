"use client";

/**
 * Collapsible “Thinking” row — matches the tool-call strip (left rule, small type).
 * Live strip: open while ``isLive``; auto-collapses shortly after ``thinking_done``.
 * Persisted (message bubble): starts collapsed; user expands to read reasoning after the reply.
 */

import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

export function ThinkingBlock({
  content,
  isLive,
  /** When true, used in the transcript after the turn — collapsed by default (Cursor-style). */
  persisted = false,
  className,
}: {
  content: string;
  isLive: boolean;
  persisted?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(() => !persisted);

  /** Live strip: follow streaming; persisted: leave open state to the user. */
  useEffect(() => {
    if (persisted) return;
    if (isLive) {
      setOpen(true);
      return;
    }
    const id = window.setTimeout(() => setOpen(false), 600);
    return () => window.clearTimeout(id);
  }, [isLive, persisted]);

  if (!content.trim()) return null;

  return (
    <div
      className={cn(
        "w-full max-w-[95%] text-xs text-muted-foreground",
        "flex flex-col gap-1",
        className,
      )}
    >
      <details className="group/thinking" open={open}>
        <summary
          className="flex cursor-pointer list-none items-center gap-2 py-0.5 [&::-webkit-details-marker]:hidden"
          onClick={(e) => {
            e.preventDefault();
            setOpen((v) => !v);
          }}
        >
          {isLive ? (
            <span
              className="relative flex h-4 w-4 shrink-0 items-center justify-center"
              aria-hidden
            >
              <span className="absolute inset-0 animate-pulse rounded-full bg-primary/25" />
              <span className="relative h-2 w-2 rounded-full bg-primary/80" />
            </span>
          ) : (
            <span
              className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground"
              aria-hidden
            >
              ✓
            </span>
          )}
          <span className="min-w-0 flex-1 font-medium text-muted-foreground/90">
            Thinking
          </span>
          <ChevronDown
            className={cn(
              "h-3 w-3 shrink-0 opacity-40 transition-transform",
              open ? "rotate-180" : "",
            )}
            aria-hidden
          />
        </summary>
        <div className="mt-1 space-y-0 border-l border-border/50 pl-3 ml-2 pb-1">
          <pre
            className={cn(
              "max-w-full whitespace-pre-wrap wrap-break-word font-mono text-[0.65rem] leading-snug text-muted-foreground/90",
              "max-h-64 overflow-y-auto [scrollbar-width:thin]",
            )}
          >
            {content}
            {isLive ? (
              <span
                className="ml-0.5 inline-block h-3 w-0.5 animate-pulse rounded-sm bg-primary align-middle"
                aria-hidden
              />
            ) : null}
          </pre>
        </div>
      </details>
    </div>
  );
}
