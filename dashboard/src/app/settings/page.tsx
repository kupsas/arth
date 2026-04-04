"use client"

/**
 * Settings — reminders CRUD + statement upload (pipeline).
 *
 * Reminders can optionally reference past expense transactions (“examples”).
 * The API builds a fingerprint from those rows (counterparty + optional
 * description anchors, often auto-derived) so the dashboard can match payments.
 */

import * as React from "react"
import { Pencil, Trash2 } from "lucide-react"

import { UploadButton } from "@/components/dashboard/upload-button"
import { ReminderExamplePicker } from "@/components/settings/reminder-example-picker"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  useCreateReminder,
  useDeleteReminder,
  useReminders,
  useUpdateReminder,
} from "@/hooks/use-settings"
import { deriveReminderAnchors } from "@/lib/api"
import { COUNTERPARTY_CATEGORY_OPTIONS } from "@/lib/counterparty-categories"
import type { Reminder } from "@/lib/types"
import { formatCurrency } from "@/lib/utils"

/** Split user comma-separated match text into anchor strings for the API. */
function parseAnchorInput(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export default function SettingsPage() {
  const { data: reminders, isLoading } = useReminders()
  const createMut = useCreateReminder()
  const updateMut = useUpdateReminder()
  const deleteMut = useDeleteReminder()

  const [name, setName] = React.useState("")
  const [dueDay, setDueDay] = React.useState("5")
  const [amount, setAmount] = React.useState("")
  const [category, setCategory] = React.useState<string>("_none")
  const [createExampleIds, setCreateExampleIds] = React.useState<number[]>([])
  const [createAnchorText, setCreateAnchorText] = React.useState("")

  const [editing, setEditing] = React.useState<Reminder | null>(null)
  /** Set when user clicks trash — confirmed in a small dialog before DELETE runs. */
  const [deleteTarget, setDeleteTarget] = React.useState<Reminder | null>(null)
  const [editName, setEditName] = React.useState("")
  const [editDueDay, setEditDueDay] = React.useState("5")
  const [editAmount, setEditAmount] = React.useState("")
  const [editCategory, setEditCategory] = React.useState<string>("_none")
  const [editExampleIds, setEditExampleIds] = React.useState<number[]>([])
  const [editAnchorText, setEditAnchorText] = React.useState("")
  /** After loading a reminder into the sheet, skip re-derive until example IDs change. */
  const editExampleIdsSyncRef = React.useRef<string | null>(null)

  React.useEffect(() => {
    if (!editing) {
      editExampleIdsSyncRef.current = null
      return
    }
    setEditName(editing.name)
    setEditDueDay(String(editing.due_day_of_month))
    setEditAmount(editing.amount != null ? String(editing.amount) : "")
    setEditCategory(editing.counterparty_category ?? "_none")
    setEditExampleIds([...editing.example_transaction_ids])
    setEditAnchorText(editing.description_match_anchors.join(", "))
    editExampleIdsSyncRef.current = [...editing.example_transaction_ids]
      .sort((a, b) => a - b)
      .join(",")
  }, [editing])

  React.useEffect(() => {
    if (createExampleIds.length === 0) {
      setCreateAnchorText("")
      return
    }
    const handle = window.setTimeout(() => {
      void deriveReminderAnchors(createExampleIds)
        .then((res) => {
          setCreateAnchorText(res.anchors.join(", "))
        })
        .catch(() => {
          /* keep previous text; user can type manually */
        })
    }, 450)
    return () => window.clearTimeout(handle)
  }, [createExampleIds])

  React.useEffect(() => {
    if (!editing) return
    const key = [...editExampleIds].sort((a, b) => a - b).join(",")
    if (editExampleIds.length === 0) {
      setEditAnchorText("")
      editExampleIdsSyncRef.current = key
      return
    }
    if (editExampleIdsSyncRef.current === key) return
    const handle = window.setTimeout(() => {
      void deriveReminderAnchors(editExampleIds)
        .then((res) => {
          setEditAnchorText(res.anchors.join(", "))
          editExampleIdsSyncRef.current = key
        })
        .catch(() => {
          editExampleIdsSyncRef.current = key
        })
    }, 450)
    return () => window.clearTimeout(handle)
  }, [editing, editExampleIds])

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    const day = parseInt(dueDay, 10)
    if (!name.trim() || day < 1 || day > 31) return
    try {
      await createMut.mutateAsync({
        name: name.trim(),
        due_day_of_month: day,
        amount: amount ? parseFloat(amount) : undefined,
        counterparty_category: category === "_none" ? undefined : category,
        example_transaction_ids:
          createExampleIds.length > 0 ? createExampleIds : undefined,
        ...(createExampleIds.length > 0
          ? { description_match_anchors: parseAnchorInput(createAnchorText) }
          : {}),
        is_active: true,
      })
      setName("")
      setAmount("")
      setCategory("_none")
      setCreateExampleIds([])
      setCreateAnchorText("")
    } catch {
      /* ApiError surfaced by mutation if we add onError toast later */
    }
  }

  async function handleEditSave(e: React.FormEvent) {
    e.preventDefault()
    if (!editing) return
    const day = parseInt(editDueDay, 10)
    if (!editName.trim() || day < 1 || day > 31) return
    try {
      await updateMut.mutateAsync({
        id: editing.id,
        body: {
          name: editName.trim(),
          due_day_of_month: day,
          amount: editAmount ? parseFloat(editAmount) : null,
          counterparty_category:
            editCategory === "_none" ? null : editCategory,
          example_transaction_ids: editExampleIds,
          description_match_anchors: parseAnchorInput(editAnchorText),
        },
      })
      setEditing(null)
    } catch {
      /* keep sheet open */
    }
  }

  return (
    <div className="max-w-2xl flex flex-col gap-8">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Statement upload</CardTitle>
          <p className="text-sm text-muted-foreground font-normal">
            Run the pipeline on a new bank export.
          </p>
        </CardHeader>
        <CardContent>
          <UploadButton />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Payment reminders</CardTitle>
          <p className="text-sm text-muted-foreground font-normal">
            Rent, credit card due dates, etc. Pick example payments when you can. We
            auto-suggest comma-separated match text from bank descriptions — confirm or edit
            before saving.
          </p>
        </CardHeader>
        <CardContent className="space-y-6">
          <form onSubmit={handleAdd} className="space-y-4 rounded-lg border border-border p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5 sm:col-span-2">
                <Label htmlFor="r-name">Name</Label>
                <Input
                  id="r-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Rent, HDFC CC"
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="r-day">Due day of month (1–31)</Label>
                <Input
                  id="r-day"
                  type="number"
                  min={1}
                  max={31}
                  value={dueDay}
                  onChange={(e) => setDueDay(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="r-amt">Expected amount (optional)</Label>
                <Input
                  id="r-amt"
                  type="number"
                  step="0.01"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  placeholder="₹"
                />
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label>Category (optional)</Label>
                <Select
                  value={category}
                  onValueChange={(v) => setCategory(v ?? "_none")}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="None" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_none">None</SelectItem>
                    {COUNTERPARTY_CATEGORY_OPTIONS.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5 sm:col-span-2">
                <Label>Example transactions (optional)</Label>
                <ReminderExamplePicker
                  ids={createExampleIds}
                  onIdsChange={setCreateExampleIds}
                  disabled={createMut.isPending}
                />
              </div>
              {createExampleIds.length > 0 && (
                <div className="space-y-1.5 sm:col-span-2">
                  <Label htmlFor="r-anchors">
                    Description match text (comma-separated) — confirm or edit
                  </Label>
                  <Textarea
                    id="r-anchors"
                    value={createAnchorText}
                    onChange={(e) => setCreateAnchorText(e.target.value)}
                    placeholder="Auto-filled from examples; add multiple tokens separated by commas"
                    rows={3}
                    className="min-h-18 text-sm"
                  />
                  <p className="text-xs text-muted-foreground">
                    A future payment matches if its description or reference contains{" "}
                    <strong>any</strong> of these substrings (case-insensitive). Leave empty
                    after clearing to fall back to amount-only matching.
                  </p>
                </div>
              )}
            </div>
            <Button type="submit" disabled={createMut.isPending}>
              {createMut.isPending ? "Adding…" : "Add reminder"}
            </Button>
          </form>

          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {reminders && reminders.length === 0 && (
            <p className="text-sm text-muted-foreground">No reminders yet.</p>
          )}
          {reminders && reminders.length > 0 && (
            <ul className="space-y-2">
              {reminders.map((r) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <p className="font-medium">{r.name}</p>
                    <p className="text-xs text-muted-foreground">
                      Day {r.due_day_of_month}
                      {r.amount != null && <> · ~{formatCurrency(r.amount)}</>}
                      {r.counterparty_category && <> · {r.counterparty_category}</>}
                      {r.example_transaction_ids.length > 0 && (
                        <>
                          {" "}
                          · {r.example_transaction_ids.length} example
                          {r.example_transaction_ids.length !== 1 ? "s" : ""}
                        </>
                      )}
                      {r.examples_stale && (
                        <span className="ml-1 text-amber-600 dark:text-amber-500">
                          (examples outdated — edit to fix)
                        </span>
                      )}
                      {r.suggest_manual_anchors && (
                        <span className="ml-1 block text-amber-700 dark:text-amber-400">
                          No description anchors — edit to add comma-separated match text
                          (or re-save after picking examples).
                        </span>
                      )}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Edit reminder"
                      onClick={() => setEditing(r)}
                    >
                      <Pencil className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="text-destructive"
                      onClick={() => setDeleteTarget(r)}
                      disabled={deleteMut.isPending}
                      aria-label="Delete reminder"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Sheet open={editing != null} onOpenChange={(o) => !o && setEditing(null)}>
        <SheetContent className="flex w-full flex-col sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Edit reminder</SheetTitle>
            <SheetDescription>
              Update due date, amount, category, or example transactions used for matching.
            </SheetDescription>
          </SheetHeader>
          {editing && (
            <form
              onSubmit={handleEditSave}
              className="flex flex-1 flex-col gap-4 overflow-y-auto px-4"
            >
              <div className="space-y-1.5">
                <Label htmlFor="e-name">Name</Label>
                <Input
                  id="e-name"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="e-day">Due day (1–31)</Label>
                <Input
                  id="e-day"
                  type="number"
                  min={1}
                  max={31}
                  value={editDueDay}
                  onChange={(e) => setEditDueDay(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="e-amt">Expected amount (optional)</Label>
                <Input
                  id="e-amt"
                  type="number"
                  step="0.01"
                  value={editAmount}
                  onChange={(e) => setEditAmount(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Category (optional)</Label>
                <Select
                  value={editCategory}
                  onValueChange={(v) => setEditCategory(v ?? "_none")}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_none">None</SelectItem>
                    {COUNTERPARTY_CATEGORY_OPTIONS.map((c) => (
                      <SelectItem key={c} value={c}>
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Example transactions</Label>
                <ReminderExamplePicker
                  ids={editExampleIds}
                  onIdsChange={setEditExampleIds}
                  disabled={updateMut.isPending}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="e-anchors">
                  Description match text (comma-separated) — confirm or edit
                </Label>
                <Textarea
                  id="e-anchors"
                  value={editAnchorText}
                  onChange={(e) => setEditAnchorText(e.target.value)}
                  placeholder="Tokens from bank text; comma-separated"
                  rows={3}
                  className="min-h-18 text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  When you change examples, we re-suggest anchors — review before saving.
                </p>
              </div>
              <SheetFooter className="flex-row gap-2 sm:justify-end">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setEditing(null)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={updateMut.isPending}>
                  {updateMut.isPending ? "Saving…" : "Save"}
                </Button>
              </SheetFooter>
            </form>
          )}
        </SheetContent>
      </Sheet>

      <Dialog
        open={deleteTarget != null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete reminder?</DialogTitle>
            <DialogDescription>
              This removes &quot;{deleteTarget?.name}&quot; from your payment reminders. You can add
              it again later, but this action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 border-0 bg-transparent p-0 pt-2 sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={deleteMut.isPending}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => {
                if (deleteTarget == null) return
                void deleteMut.mutateAsync(deleteTarget.id).then(() => setDeleteTarget(null))
              }}
            >
              {deleteMut.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
