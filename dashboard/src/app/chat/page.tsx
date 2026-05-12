"use client";

/**
 * Arth agent chat — WebSocket streaming + SQLite-backed sessions (Sub-Plan 5).
 *
 * Blocks the UI until at least one agent LLM API key is available (stored here or on the server).
 * Optional: reuse keys from auto-labelling setup (server copies encrypted secrets — values never
 * leave the machine).
 */

import * as React from "react";
import { Suspense, useCallback, useEffect, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ChatLayout } from "@/components/chat/chat-layout";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { onboardingClassifierStatusKey } from "@/hooks/use-onboarding";
import {
  fetchAgentKeysStatus,
  fetchOnboardingClassifierStatus,
  postAgentKeys,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import posthog from "posthog-js";

function AgentKeysBlockingModal({ onSaved }: { onSaved: () => void }) {
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  const classifierQ = useQuery({
    queryKey: [...onboardingClassifierStatusKey],
    queryFn: fetchOnboardingClassifierStatus,
  });

  const classifier = classifierQ.data;
  const classifierHasOpenAI = Boolean(classifier?.has_openai_api_key);
  const classifierHasAnthropic = Boolean(classifier?.has_anthropic_api_key);
  const classifierHasGoogle = Boolean(classifier?.has_google_api_key);
  const classifierHasAny = Boolean(classifier?.has_any_api_key);

  const [reuseClassifier, setReuseClassifier] = React.useState(true);

  const save = useMutation({
    mutationFn: async () => {
      setError(null);
      const body: Parameters<typeof postAgentKeys>[0] = {};

      if (reuseClassifier && classifierHasAny) {
        body.reuse_classifier_keys = true;
        await postAgentKeys(body);
        return;
      }

      if (anthropic.trim()) body.anthropic_api_key = anthropic.trim();
      if (google.trim()) body.google_api_key = google.trim();
      const hasManual = Boolean(anthropic.trim()) || Boolean(google.trim());
      if (!hasManual) {
        throw new Error("Add an Anthropic or Google AI key to continue.");
      }
      await postAgentKeys(body);
    },
    onSuccess: () => {
      setError(null);
      posthog.capture("agent_keys_saved", {
        reused_classifier_keys: reuseClassifier && classifierHasAny,
        has_anthropic: Boolean(anthropic.trim()) || (reuseClassifier && classifierHasAnthropic),
        has_google: Boolean(google.trim()) || (reuseClassifier && classifierHasGoogle),
      });
      onSaved();
    },
    onError: (e: Error) => setError(e.message),
  });

  /** When reusing saved auto-labelling keys, hide the paste fields until the user opts out. */
  const showProviderFields = !reuseClassifier || !classifierHasAny;

  const canSave =
    reuseClassifier && classifierHasAny
      ? true
      : Boolean(anthropic.trim()) || Boolean(google.trim());

  const reuseBanner = (() => {
    if (!reuseClassifier || !classifierHasAny) return null;
    const names: string[] = [];
    if (classifierHasOpenAI) names.push("OpenAI");
    if (classifierHasAnthropic) names.push("Anthropic");
    if (classifierHasGoogle) names.push("Google AI");
    if (names.length === 0) return null;
    if (names.length === 1 && names[0] === "OpenAI") {
      return (
        <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
          Your auto-labelling key covers everything — you&apos;re all set.
        </p>
      );
    }
    const label = new Intl.ListFormat("en-IN", { style: "long", type: "conjunction" }).format(
      names,
    );
    const noun = names.length === 1 ? "key" : "keys";
    return (
      <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
        We&apos;ll use your {label} {noun} from auto-labelling for the conversation.
      </p>
    );
  })();

  return (
    <Dialog open>
      <DialogContent className="sm:max-w-md" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>One more thing before we chat</DialogTitle>
          <DialogDescription>
            Ask Arth needs an AI key to answer your questions. Keys are stored encrypted on this
            machine — you can manage them anytime in{" "}
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

          {classifierQ.isLoading && (
            <Skeleton className="h-10 w-full" aria-hidden />
          )}

          {classifierHasAny && !classifierQ.isLoading && (
            <div className="flex items-start gap-3 rounded-md border border-border px-3 py-2">
              <Checkbox
                id="chat-gate-reuse-classifier"
                checked={reuseClassifier}
                onCheckedChange={(v) => setReuseClassifier(v === true)}
                className="mt-0.5"
              />
              <div className="grid gap-0.5 leading-none">
                <Label htmlFor="chat-gate-reuse-classifier" className="cursor-pointer font-normal">
                  Reuse my auto-labelling key
                </Label>
                <p className="text-xs text-muted-foreground">
                  Same encrypted keys you used for smart labels — nothing is sent to the browser.
                </p>
              </div>
            </div>
          )}

          {reuseBanner}

          {showProviderFields && (
            <div className="grid gap-3">
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
          )}
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Link
            href="/settings"
            className={cn(buttonVariants({ variant: "secondary" }))}
          >
            Open Settings
          </Link>
          <Button
            type="button"
            disabled={save.isPending || !canSave}
            onClick={() => void save.mutate()}
          >
            {save.isPending ? "Setting up…" : "Let's go"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ChatPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  /** Thread id from the address bar (stable once we sync an empty draft). */
  const sessionFromUrl = searchParams.get("session") ?? undefined;
  /** When true, skip reusing an empty draft and open a brand-new server thread (see "New chat"). */
  const forceNew = searchParams.get("new") === "1";
  const queryClient = useQueryClient();

  const keysQ = useQuery({
    queryKey: ["agent-keys-status"],
    queryFn: fetchAgentKeysStatus,
  });

  const { data: sessions = [], isLoading: sessionsLoading, isFetched: sessionsFetched } =
    useChatSessionsQuery();

  const hasKeys = Boolean(keysQ.data?.has_any_api_key);
  const keysGateReady = !keysQ.isLoading && hasKeys;

  /**
   * Which session the WebSocket should attach to. ``undefined`` means "let the server create one"
   * (only after we know there is no empty draft to reuse, or the user chose ``?new=1``).
   */
  const wsSessionId = useMemo(() => {
    if (sessionFromUrl) return sessionFromUrl;
    if (forceNew) return undefined;
    if (!sessionsFetched) return undefined;
    const draft = sessions.find((s) => (s.message_count ?? 0) === 0);
    return draft?.id;
  }, [sessionFromUrl, forceNew, sessionsFetched, sessions]);

  const chatWsEnabled =
    keysGateReady && (Boolean(sessionFromUrl) || forceNew || sessionsFetched);

  // Put the reused draft id in the URL so refresh and the sidebar stay in sync (no extra server row).
  useEffect(() => {
    if (!keysGateReady) return;
    if (sessionFromUrl || forceNew) return;
    if (!sessionsFetched) return;
    const draft = sessions.find((s) => (s.message_count ?? 0) === 0);
    if (!draft) return;
    router.replace(`/chat?session=${encodeURIComponent(draft.id)}`, { scroll: false });
  }, [keysGateReady, sessionFromUrl, forceNew, sessionsFetched, sessions, router]);

  const onSessionReady = useCallback(
    (id: string) => {
      void queryClient.invalidateQueries({ queryKey: chatSessionsQueryKey });
      router.replace(`/chat?session=${encodeURIComponent(id)}`, {
        scroll: false,
      });
    },
    [queryClient, router],
  );

  const onSessionNotFound = useCallback(() => {
    // Immediately wipe the stale sessions cache so wsSessionId can't re-select
    // a dead draft during the refetch window (invalidateQueries only marks as
    // stale — old data persists until the background refetch completes).
    queryClient.setQueryData(chatSessionsQueryKey, []);
    void queryClient.invalidateQueries({ queryKey: chatSessionsQueryKey });
    // ?new=1 bypasses the draft-reuse logic in wsSessionId, guaranteeing
    // a clean WS open with no session_id (server creates a fresh one).
    router.replace("/chat?new=1", { scroll: false });
  }, [queryClient, router]);

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
    agentPausedFailures,
    clearAgentPaused,
    retryLastUserMessage,
    sendMessage,
    stopGenerating,
  } = useChat(wsSessionId, onSessionReady, {
    enabled: chatWsEnabled,
    onSessionNotFound,
  });

  const onSwitchProviderKeys = useCallback(() => {
    clearAgentPaused();
    router.push("/settings");
  }, [clearAgentPaused, router]);

  const onRetryAgentPaused = useCallback(() => {
    clearAgentPaused();
    retryLastUserMessage();
  }, [clearAgentPaused, retryLastUserMessage]);

  const archive = useArchiveChatSessionMutation();

  const onNewChat = useCallback(() => {
    router.push("/chat?new=1", { scroll: false });
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
        if (sessionFromUrl === id) {
          router.push("/chat", { scroll: false });
        }
      });
    },
    [archive, sessionFromUrl, router],
  );

  const activeSidebarSessionId = sessionFromUrl ?? wsSessionId;

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
    <div>
      <ChatLayout
        sessions={sessions}
        sessionsLoading={sessionsLoading}
        activeSessionId={activeSidebarSessionId}
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
        onDismissAgentPaused={clearAgentPaused}
        onRetryAgentPaused={onRetryAgentPaused}
        onSwitchProviderKeys={onSwitchProviderKeys}
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
