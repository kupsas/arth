"use client"

/**
 * Settings — reminders CRUD + statement upload (pipeline).
 */

import * as React from "react"
import { Trash2 } from "lucide-react"

import { UploadButton } from "@/components/dashboard/upload-button"
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
import {
  useCreateReminder,
  useDeleteReminder,
  useReminders,
} from "@/hooks/use-settings"
import { COUNTERPARTY_CATEGORY_OPTIONS } from "@/lib/counterparty-categories"
import { formatCurrency } from "@/lib/utils"

export default function SettingsPage() {
  const { data: reminders, isLoading } = useReminders()
  const createMut = useCreateReminder()
  const deleteMut = useDeleteReminder()

  const [name, setName] = React.useState("")
  const [dueDay, setDueDay] = React.useState("5")
  const [amount, setAmount] = React.useState("")
  const [category, setCategory] = React.useState<string>("_none")

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    const day = parseInt(dueDay, 10)
    if (!name.trim() || day < 1 || day > 31) return
    await createMut.mutateAsync({
      name: name.trim(),
      due_day_of_month: day,
      amount: amount ? parseFloat(amount) : undefined,
      counterparty_category: category === "_none" ? undefined : category,
      is_active: true,
    })
    setName("")
    setAmount("")
    setCategory("_none")
  }

  return (
    <div className="max-w-2xl flex flex-col gap-8">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Statement upload</CardTitle>
          <p className="text-sm text-muted-foreground font-normal">
            Run the pipeline on a new bank export (same as before — now lives here).
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
            Rent, credit card due dates, etc. Shown on the dashboard Reminders card.
            Optional category helps guess if you&apos;ve paid this month.
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
                    <SelectValue placeholder="None — category not tracked" />
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
                  <div>
                    <p className="font-medium">{r.name}</p>
                    <p className="text-xs text-muted-foreground">
                      Day {r.due_day_of_month}
                      {r.amount != null && <> · ~{formatCurrency(r.amount)}</>}
                      {r.counterparty_category && <> · {r.counterparty_category}</>}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="text-destructive"
                    onClick={() => deleteMut.mutate(r.id)}
                    disabled={deleteMut.isPending}
                    aria-label="Delete reminder"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
