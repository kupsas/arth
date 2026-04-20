"use client";

/**
 * Same visual language as ``StreamingIndicator``: thin left rule, small type, checkmarks.
 * Technical JSON lives inside a per-row ``<details>`` so the transcript stays quiet.
 */

import { useState } from "react";
import { ChevronDown } from "lucide-react";

import type { ToolCallUi } from "@/lib/chat-types";
import { formatToolLabel } from "@/lib/format-tool-label";
import { parsePersistedToolMessageContent } from "@/lib/parse-tool-message";
import { cn } from "@/lib/utils";

/** If hydration still has the legacy ``{ raw: "<tool_result…" }`` shape, unwrap once. */
function resultForDisplay(result: Record<string, unknown> | undefined): unknown {
  if (!result) return undefined;
  const keys = Object.keys(result);
  if (
    keys.length === 1 &&
    typeof result.raw === "string" &&
    result.raw.trim().toLowerCase().startsWith("<tool_result")
  ) {
    return parsePersistedToolMessageContent(result.raw);
  }
  return result;
}

function JsonPreview({ data }: { data: unknown }) {
  const [open, setOpen] = useState(false);
  const s = JSON.stringify(data, null, 2);
  const long = s.length > 500;
  const shown = open || !long ? s : `${s.slice(0, 500)}…`;
  return (
    <div>
      <pre
        className={cn(
          "mt-1 max-w-full rounded border border-border/40 bg-background/80 px-2 py-1.5 font-mono text-[0.65rem] leading-snug text-foreground/90",
          "overflow-x-auto whitespace-pre-wrap wrap-break-word",
          open && long ? "max-h-64 overflow-y-auto [scrollbar-width:thin]" : "",
        )}
      >
        {shown}
      </pre>
      {long && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="mt-1 text-[0.65rem] text-primary underline"
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

export function ToolCallGroup({ tools }: { tools: ToolCallUi[] }) {
  if (!tools.length) return null;

  return (
    <div
      className={cn(
        "w-full max-w-[95%] text-xs text-muted-foreground",
        "flex flex-col gap-1",
      )}
    >
      <ul className="m-0 flex list-none flex-col gap-0.5 border-l border-border pl-3">
        {tools.map((t, i) => (
          <li key={`${t.name}-${i}`}>
            <details className="group/row">
              <summary
                className={cn(
                  "flex cursor-pointer list-none items-center gap-2 py-0.5 [&::-webkit-details-marker]:hidden",
                )}
              >
                <span
                  className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground"
                  aria-hidden
                >
                  ✓
                </span>
                <span className="min-w-0 flex-1 text-muted-foreground/80">
                  {formatToolLabel(t.name, i)}
                </span>
                {t.duration_ms != null && (
                  <span className="shrink-0 font-mono text-[0.65rem] text-muted-foreground/60">
                    {t.duration_ms}ms
                  </span>
                )}
                <ChevronDown
                  className="h-3 w-3 shrink-0 opacity-40 transition-transform group-open/row:rotate-180"
                  aria-hidden
                />
              </summary>
              <div className="mt-1 space-y-2 border-l border-border/50 pl-3 ml-2 pb-1 text-[0.7rem]">
                <div>
                  <div className="font-medium text-muted-foreground">Arguments</div>
                  <JsonPreview data={t.arguments} />
                </div>
                {t.result !== undefined && (
                  <div>
                    <div className="font-medium text-muted-foreground">Response</div>
                    <JsonPreview data={resultForDisplay(t.result)} />
                  </div>
                )}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
