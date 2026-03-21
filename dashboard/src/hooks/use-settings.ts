"use client"

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query"

import {
  createReminder,
  deleteReminder,
  fetchReminders,
  updateReminder,
} from "@/lib/api"
import type { Reminder, ReminderCreate, ReminderUpdate } from "@/lib/types"

export const reminderKeys = {
  all: ["reminders"] as const,
  list: () => [...reminderKeys.all, "list"] as const,
}

export function useReminders(options?: Partial<UseQueryOptions<Reminder[]>>) {
  return useQuery<Reminder[]>({
    queryKey: reminderKeys.list(),
    queryFn: fetchReminders,
    staleTime: 60 * 1_000,
    ...options,
  })
}

export function useCreateReminder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ReminderCreate) => createReminder(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: reminderKeys.all }),
  })
}

export function useUpdateReminder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: ReminderUpdate }) =>
      updateReminder(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: reminderKeys.all }),
  })
}

export function useDeleteReminder() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteReminder(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: reminderKeys.all }),
  })
}
