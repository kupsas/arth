"use client";

/**
 * TanStack Query wrappers for persisted chat sessions (REST).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveChatSession,
  listChatSessions,
  renameChatSession,
} from "@/lib/api";

/** Shared query key — invalidate after WS creates a thread or rename/delete. */
export const chatSessionsQueryKey = ["chat-sessions"] as const;

export function useChatSessionsQuery() {
  return useQuery({
    queryKey: chatSessionsQueryKey,
    queryFn: () => listChatSessions({ limit: 50, offset: 0 }),
  });
}

export function useRenameChatSessionMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      renameChatSession(id, title),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: chatSessionsQueryKey }),
  });
}

export function useArchiveChatSessionMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => archiveChatSession(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: chatSessionsQueryKey }),
  });
}
