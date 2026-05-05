"use client";

/**
 * Arth agent chat — WebSocket streaming + SQLite-backed sessions (Sub-Plan 5).
 *
 * Blocks the UI until at least one agent LLM API key is available (stored here or on the server).
 */

import * as React from "react";
import { Suspense, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ChatLayout } from "@/components/chat/chat-layout";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useChat } from "@/hooks/use-chat";
import {
  chatSessionsQueryKey,
  useArchiveChatSessionMutation,
  useChatSessionsQuery,
} from "@/hooks/use-chat-sessions";
import { fetchAgentKeysStatus, postAgentKeys } from "@/lib/api";
import { cn } from "@/lib/utils";

function AgentKeysBlockingModal({
  onSaved,
}: {
  onSaved: () => void;
}) {
  const [openai, setOpenai] = React.useState("");
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      if (openai.trim()) body.openai_api_key = openai.trim();
      if (anthropic.trim()) body.anthropic_api_key = anthropic.trim();
      if (google.trim()) body.google_api_key = google.trim();
      if (Object.keys(body).length === 0) {
        throw new Error("Paste at least one API key to continue.");
      }
      await postAgentKeys(body);
    },
    onSuccess: () => {
      setError(null);
      onSaved();
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog open>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Add an LLM API key</DialogTitle>
          <DialogDescription>
            Chat uses cloud models through LiteLLM. Keys stay encrypted on this machine. You can also add them later in{" "}
            <Link href="/settings" className="underline underline-offset-2">
              Settings
            </Link>
            .
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3 py-2">
          {error && (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          )}
          <div className="space-y-1">
            <Label htmlFor="chat-gate-openai">OpenAI</Label>
            <Input
              id="chat-gate-openai"
              type="password"
              autoComplete="off"
              value={openai}
              onChange={(e) => setOpenai(e.target.value)}
              placeholder="sk-…"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="chat-gate-anthropic">Anthropic</Label>
            <Input
              id="chat-gate-anthropic"
              type="password"
              autoComplete="off"
              value={anthropic}
              onChange={(e) => setAnthropic(e.target.value)}
              placeholder="sk-ant-…"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="chat-gate-google">Google AI</Label>
            <Input
              id="chat-gate-google"
              type="password"
              autoComplete="off"
              value={google}
              onChange={(e) => setGoogle(e.target.value)}
              placeholder="AI…"
            />
          </div>
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Link
            href="/settings"
            className={cn(buttonVariants({ variant: "secondary" }))}
          >
            Open Settings
          </Link>
          <Button type="button" disabled={save.isPending} onClick={() => void save.mutate()}>
            {save.isPending ? "Saving…" : "Save and continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session") ?? undefined;
  const queryClient = useQueryClient();

  const keysQ = useQuery({
    queryKey: ["agent-keys-status"],
    queryFn: fetchAgentKeysStatus,
  });

  const onSessionReady = useCallback(
    (id: string) => {
      void queryClient.invalidateQueries({ queryKey: chatSessionsQueryKey });
      router.replace(`/chat?session=${encodeURIComponent(id)}`, {
        scroll: false,
      });
    },
    [queryClient, router],
  );

  const {
    messages,
    connection,
    isGenerating,
    isResponseStreaming,
    liveTools,
    liveThinking,
    isThinking,
    liveActivitySegments,
    liveWipTools,
    lastError,
    sendMessage,
    stopGenerating,
  } = useChat(sessionId, onSessionReady);

  const { data: sessions = [], isLoading: sessionsLoading } = useChatSessionsQuery();
  const archive = useArchiveChatSessionMutation();

  const onNewChat = useCallback(() => {
    router.push("/chat", { scroll: false });
  }, [router]);

  const onSelectSession = useCallback(
    (id: string) => {
      router.push(`/chat?session=${encodeURIComponent(id)}`, {
        scroll: false,
      });
    },
    [router],
  );

  const onArchiveSession = useCallback(
    (id: string) => {
      void archive.mutateAsync(id).then(() => {
        if (sessionId === id) {
          router.push("/chat", { scroll: false });
        }
      });
    },
    [archive, sessionId, router],
  );

  const connectionOk = connection === "open";

  if (keysQ.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-[28rem] w-full" />
      </div>
    );
  }

  if (!keysQ.data?.has_any_api_key) {
    return (
      <div className="relative space-y-4">
        <AgentKeysBlockingModal
          onSaved={() => void queryClient.invalidateQueries({ queryKey: ["agent-keys-status"] })}
        />
        <div className="pointer-events-none opacity-40">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="mt-4 h-[28rem] w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Ask Arth</h2>
        <p className="text-sm text-muted-foreground">
          Poocho! Ask me anything about your money — spending, goals, investments, whatever&apos;s on your mind.
        </p>
      </div>
      <ChatLayout
        sessions={sessions}
        sessionsLoading={sessionsLoading}
        activeSessionId={sessionId}
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
        onNewChat={onNewChat}
        onSelectSession={onSelectSession}
        onArchiveSession={onArchiveSession}
        onSend={sendMessage}
        onStop={stopGenerating}
      />
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col gap-4">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-[28rem] w-full" />
        </div>
      }
    >
      <ChatPageInner />
    </Suspense>
  );
}
