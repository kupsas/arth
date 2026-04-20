"use client";

import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ChatMessageUi } from "@/lib/chat-types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { ThinkingBlock } from "./thinking-block";
import { ToolCallGroup } from "./tool-call-group";

/**
 * Copies the assistant’s answer as stored — that string is Markdown source (what we pass to ReactMarkdown).
 */
function CopyMarkdownAnswerButton({ markdown }: { markdown: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(id);
  }, [copied]);

  const onCopy = useCallback(async () => {
    const text = markdown.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        setCopied(true);
      } catch {
        /* clipboard unavailable */
      }
    }
  }, [markdown]);

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      className="absolute right-1.5 top-1.5 z-10 text-muted-foreground opacity-0 transition-opacity hover:bg-muted/80 hover:text-foreground group-hover:opacity-100 focus-visible:opacity-100"
      onClick={onCopy}
      aria-label={copied ? "Copied" : "Copy answer (Markdown)"}
      title="Copy answer (Markdown)"
    >
      {copied ? (
        <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
      ) : (
        <Copy className="size-3.5" aria-hidden />
      )}
    </Button>
  );
}

export function MessageBubble({ message }: { message: ChatMessageUi }) {
  const isUser = message.role === "user";
  const isStreaming = Boolean(message.isStreaming);
  /**
   * Intermediate assistant rows in stored history often have ``tool_calls`` but no
   * assistant ``content`` — we still rendered an empty card (“black pill” between turns).
   */
  const assistantHasText = message.content.trim().length > 0;
  const showBubble = isUser || assistantHasText;

  return (
    <div
      className={cn(
        "flex w-full flex-col gap-1",
        isUser ? "items-end" : "items-start",
      )}
    >
      {!isUser && message.activity && message.activity.length > 0 && (
        <div className="flex w-full max-w-[95%] flex-col gap-2">
          {message.activity.map((seg, idx) =>
            seg.kind === "thinking" ? (
              <ThinkingBlock
                key={`act-${idx}`}
                content={seg.content}
                isLive={false}
                persisted
              />
            ) : (
              <ToolCallGroup key={`act-${idx}`} tools={seg.tools} />
            ),
          )}
        </div>
      )}
      {!isUser &&
        !(message.activity && message.activity.length > 0) &&
        message.thinking &&
        message.thinking.trim().length > 0 && (
          <ThinkingBlock content={message.thinking} isLive={false} persisted />
        )}
      {!isUser &&
        !(message.activity && message.activity.length > 0) &&
        message.toolCalls &&
        message.toolCalls.length > 0 && <ToolCallGroup tools={message.toolCalls} />}
      {showBubble && (
        <div
          className={cn(
            "max-w-[95%] rounded-2xl px-4 py-2 text-sm leading-relaxed",
            isUser
              ? "bg-primary text-primary-foreground"
              : "border border-border bg-card text-card-foreground",
            !isUser && !isStreaming && assistantHasText && "group relative",
          )}
        >
          {!isUser && !isStreaming && assistantHasText && (
            <CopyMarkdownAnswerButton markdown={message.content} />
          )}
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : isStreaming ? (
            /**
             * Plain text while tokens arrive — avoids half-parsed Markdown
             * (e.g. a lone ``**``) flickering in ReactMarkdown.
             */
            <p className="whitespace-pre-wrap">
              {message.content}
              <span
                className="ml-0.5 inline-block h-4 w-0.5 animate-pulse rounded-sm bg-primary align-middle"
                aria-hidden
              />
            </p>
          ) : (
            <article className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-ul:my-2 prose-li:my-0.5 prose-table:text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            </article>
          )}
        </div>
      )}
    </div>
  );
}
