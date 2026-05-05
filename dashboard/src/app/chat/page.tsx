"use client";

/**
 * Arth agent chat — WebSocket streaming + SQLite-backed sessions (Sub-Plan 5).
 */

import { Suspense, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { ChatLayout } from "@/components/chat/chat-layout";
import { Skeleton } from "@/components/ui/skeleton";
import { useChat } from "@/hooks/use-chat";
import {
  chatSessionsQueryKey,
  useArchiveChatSessionMutation,
  useChatSessionsQuery,
} from "@/hooks/use-chat-sessions";

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session") ?? undefined;
  const queryClient = useQueryClient();

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

  const { data: sessions = [], isLoading: sessionsLoading } =
    useChatSessionsQuery();
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

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Ask Arth</h2>
        <p className="text-sm text-muted-foreground">
          Poocho! Ask me anything about your money — spending, goals, investments,
          whatever&apos;s on your mind.
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
