"use client";

/**
 * Inline classification batch (Track 2 Phase 3b).
 *
 * Shown when onboarding backfill pauses with ``needs_classification``. The parent
 * passes the active ``source`` (pipeline key, e.g. ``hdfc_savings``). We:
 *
 * 1. ``GET /api/onboarding/unknowns?source=…`` — rows still missing automation fields.
 * 2. Let the user fix counterparty + category (+ optional spend / txn metadata).
 * 3. ``POST /api/onboarding/classify`` — persists fixes + optional merchant rules.
 * 4. Parent should then call backfill with ``resume_after_classification: true``.
 *
 * Beginner tip: **Apply to future** stores a substring rule (like Settings → merchant
 * rules). Use **Skip** to leave a row untouched for later.
 */

import * as React from "react";
import { Loader2, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { buildApiUrl } from "@/lib/api-base";
import { COUNTERPARTY_CATEGORY_OPTIONS } from "@/lib/counterparty-categories";
import type { CounterpartyCategory, SpendCategory } from "@/lib/types";

type UnknownTxnBrief = {
  id: number;
  txn_date: string | null;
  amount: number;
  direction: string;
  channel: string | null;
  raw_description: string;
  txn_type: string | null;
  upi_type: string | null;
  counterparty: string | null;
  counterparty_category: string | null;
  spend_category: string | null;
};

type UnknownGroup = {
  fingerprint: string;
  count: number;
  sample_raw_description: string;
  transactions: UnknownTxnBrief[];
};

type UnknownsResponse = {
  source: string;
  total_transactions: number;
  groups: UnknownGroup[];
  unknown_threshold: number;
};

type Draft = {
  counterparty: string;
  category: CounterpartyCategory | "";
  spend: SpendCategory | "";
  applyFuture: boolean;
  ruleKeyword: string;
};

const SPEND_OPTIONS: { value: SpendCategory; label: string }[] = [
  { value: "NEED", label: "Need" },
  { value: "WANT", label: "Want" },
  { value: "INVESTMENT", label: "Investment" },
];

async function fetchUnknowns(source: string): Promise<UnknownsResponse> {
  const res = await fetch(buildApiUrl(`/api/onboarding/unknowns?source=${encodeURIComponent(source)}`), {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<UnknownsResponse>;
}

async function postClassify(source: string, items: unknown[]): Promise<void> {
  const res = await fetch(buildApiUrl("/api/onboarding/classify"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, items }),
  });
  if (!res.ok) throw new Error(await res.text());
}

function defaultDraft(t: UnknownTxnBrief): Draft {
  return {
    counterparty: t.counterparty ?? "",
    category: (t.counterparty_category as CounterpartyCategory) ?? "",
    spend: (t.spend_category as SpendCategory) ?? "",
    applyFuture: true,
    ruleKeyword: "",
  };
}

export type ClassificationBatchReviewProps = {
  /** Pipeline ``source_key`` for the paused backfill (e.g. ``hdfc_savings``). */
  source: string;
  /** Fires after a successful classify POST — wire this to resume backfill polling. */
  onSubmitted?: () => void;
};

export function ClassificationBatchReview({ source, onSubmitted }: ClassificationBatchReviewProps) {
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [rows, setRows] = React.useState<UnknownTxnBrief[]>([]);
  const [threshold, setThreshold] = React.useState<number | null>(null);
  const [drafts, setDrafts] = React.useState<Record<number, Draft>>({});
  const [skipped, setSkipped] = React.useState<Set<number>>(() => new Set());
  const [submitting, setSubmitting] = React.useState(false);

  const reload = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchUnknowns(source);
      setThreshold(data.unknown_threshold);
      const flat = data.groups.flatMap((g) => g.transactions);
      setRows(flat);
      const init: Record<number, Draft> = {};
      for (const t of flat) init[t.id] = defaultDraft(t);
      setDrafts(init);
      setSkipped(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load unknowns");
    } finally {
      setLoading(false);
    }
  }, [source]);

  React.useEffect(() => {
    void reload();
  }, [reload]);

  function setDraft(id: number, patch: Partial<Draft>) {
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  }

  async function onSubmit() {
    setSubmitting(true);
    setError(null);
    const items: {
      txn_id: number;
      counterparty: string;
      counterparty_category: string;
      spend_category?: string | null;
      apply_to_future: boolean;
      merchant_rule_keyword?: string | null;
    }[] = [];

    for (const t of rows) {
      if (skipped.has(t.id)) continue;
      const dr = drafts[t.id];
      if (!dr) continue;
      if (!dr.counterparty.trim() || !dr.category) {
        setError(`Row #${t.id}: counterparty and category are required (or use Skip).`);
        setSubmitting(false);
        return;
      }
      items.push({
        txn_id: t.id,
        counterparty: dr.counterparty.trim(),
        counterparty_category: dr.category,
        spend_category: dr.spend || null,
        apply_to_future: dr.applyFuture,
        merchant_rule_keyword: dr.ruleKeyword.trim() || null,
      });
    }

    if (!items.length) {
      setError("Nothing to submit — classify at least one row or reload.");
      setSubmitting(false);
      return;
    }

    try {
      await postClassify(source, items);
      onSubmitted?.();
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submit failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading unknown transactions…
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Classify pending transactions</CardTitle>
        <CardDescription>
          Source <span className="font-mono">{source}</span>
          {threshold != null && (
            <>
              {" "}
              — backfill pauses when unknowns reach ~{threshold} without LLM help (lower when no API
              keys).
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!rows.length && <p className="text-sm text-muted-foreground">No unknown rows — you are clear.</p>}
        <div className="flex max-h-[min(70vh,720px)] flex-col gap-3 overflow-y-auto pr-1">
          {rows.map((t) => {
            const dr = drafts[t.id] ?? defaultDraft(t);
            const isSkipped = skipped.has(t.id);
            return (
              <div
                key={t.id}
                className={`rounded-lg border p-3 text-sm ${isSkipped ? "opacity-50" : ""}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="text-xs text-muted-foreground">
                      #{t.id} · {t.txn_date} · {t.direction} · ₹{t.amount.toFixed(2)} ·{" "}
                      {t.channel ?? "—"}
                    </div>
                    <div className="mt-1 font-mono text-xs leading-snug">{t.raw_description}</div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSkipped((prev) => {
                        const n = new Set(prev);
                        if (n.has(t.id)) n.delete(t.id);
                        else n.add(t.id);
                        return n;
                      })
                    }
                  >
                    {isSkipped ? "Un-skip" : "Skip"}
                  </Button>
                </div>
                {!isSkipped && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="grid gap-1">
                      <Label>Counterparty</Label>
                      <Input
                        value={dr.counterparty}
                        onChange={(e) => setDraft(t.id, { counterparty: e.target.value })}
                      />
                    </div>
                    <div className="grid gap-1">
                      <Label>Category</Label>
                      <Select
                        value={dr.category || undefined}
                        onValueChange={(v) => setDraft(t.id, { category: v as CounterpartyCategory })}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Pick a category" />
                        </SelectTrigger>
                        <SelectContent>
                          {COUNTERPARTY_CATEGORY_OPTIONS.map((c) => (
                            <SelectItem key={c} value={c}>
                              {c}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {t.direction === "OUTFLOW" && (
                      <div className="grid gap-1">
                        <Label>Spend tag (optional)</Label>
                        <Select
                          value={dr.spend || "__none__"}
                          onValueChange={(v) =>
                            setDraft(t.id, { spend: v === "__none__" ? "" : (v as SpendCategory) })
                          }
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="—" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__none__">—</SelectItem>
                            {SPEND_OPTIONS.map((o) => (
                              <SelectItem key={o.value} value={o.value}>
                                {o.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                    )}
                    <div className="grid gap-1 sm:col-span-2">
                      <Label>Custom rule keyword (optional)</Label>
                      <Input
                        placeholder="Defaults to counterparty — use a narration substring to match more rows"
                        value={dr.ruleKeyword}
                        onChange={(e) => setDraft(t.id, { ruleKeyword: e.target.value })}
                      />
                    </div>
                    <label className="flex items-center gap-2 sm:col-span-2">
                      <Checkbox
                        checked={dr.applyFuture}
                        onCheckedChange={(c) => setDraft(t.id, { applyFuture: Boolean(c) })}
                      />
                      <span>Apply to future similar transactions (creates a merchant rule)</span>
                    </label>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => void onSubmit()} disabled={submitting || !rows.length}>
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Submitting…
              </>
            ) : (
              <>
                <Send className="mr-2 h-4 w-4" />
                Submit classifications
              </>
            )}
          </Button>
          <Button type="button" variant="outline" onClick={() => void reload()} disabled={submitting}>
            Reload
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          After submit, call{" "}
          <span className="font-mono">POST /api/onboarding/backfill/{"{source}"}</span> with{" "}
          <span className="font-mono">resume_after_classification: true</span> to keep ingesting mail.
        </p>
      </CardContent>
    </Card>
  );
}
