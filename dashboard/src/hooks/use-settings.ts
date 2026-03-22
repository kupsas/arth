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
  fetchRemindersStatus,
  updateReminder,
} from "@/lib/api"
import type {
  Reminder,
  ReminderCreate,
  RemindersStatusResponse,
  ReminderUpdate,
} from "@/lib/types"

export const reminderKeys = {
  all: ["reminders"] as const,
  list: () => [...reminderKeys.all, "list"] as const,
  status: (month: string) => [...reminderKeys.all, "status", month] as const,
}

export function useReminders(options?: Partial<UseQueryOptions<Reminder[]>>) {
  return useQuery<Reminder[]>({
    queryKey: reminderKeys.list(),
    queryFn: fetchReminders,
    staleTime: 60 * 1_000,
    ...options,
  })
}

/** Match status for dashboard Reminders card (calendar month YYYY-MM). */
export function useRemindersStatus(
  month: string,
  options?: Partial<UseQueryOptions<RemindersStatusResponse>>,
) {
  return useQuery<RemindersStatusResponse>({
    queryKey: reminderKeys.status(month),
    queryFn: () => fetchRemindersStatus(month),
    staleTime: 60 * 1_000,
    enabled: Boolean(month && /^\d{4}-\d{2}$/.test(month)),
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
