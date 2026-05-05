"use client";

import { useState, KeyboardEvent } from "react";
import { Loader2, Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

export function ChatInput({
  disabled,
  isGenerating,
  onSend,
  onStop,
}: {
  disabled?: boolean;
  isGenerating: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");

  function submit() {
    const t = text.trim();
    if (!t || disabled || isGenerating) return;
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
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <div className="flex items-end gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Kuch poocho — spending, goals, investments…"
          disabled={disabled || isGenerating}
          rows={3}
          className="min-h-[5rem] flex-1 resize-none rounded-xl border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50"
        />
        {isGenerating ? (
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="size-10 shrink-0"
            onClick={onStop}
            aria-label="Stop generating"
          >
            <Square className="size-4 fill-current" />
          </Button>
        ) : (
          <Button
            type="button"
            size="icon"
            className="size-10 shrink-0"
            onClick={submit}
            disabled={disabled || !text.trim()}
            aria-label="Send message"
          >
            <Send className="size-4" />
          </Button>
        )}
      </div>
      {disabled && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          Connecting to chat…
        </p>
      )}
    </div>
  );
}
