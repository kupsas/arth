"use client";

/**
 * Sorting rules — merchant keyword rules from built-in data and your corrections.
 * CRUD via GET/PATCH/DELETE /api/user/merchant-rules
 */

import * as React from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { buildApiUrl } from "@/lib/api-base";
import { COUNTERPARTY_CATEGORY_OPTIONS } from "@/lib/counterparty-categories";

type MerchantRuleRow = {
  id: number;
  keyword: string;
  display_name: string;
  counterparty_category: string;
  source: string;
  created_at: string | null;
};

/** Map internal rule source keys to short labels users understand (see copy guidelines). */
function ruleSourceLabel(source: string): string {
  const s = source.trim().toUpperCase();
  if (s === "USER_CORRECTION") return "You fixed this";
  if (s === "STARTER_PACK") return "Built-in";
  if (s === "LEARNED" || s.endsWith("_LEARNED")) return "Learned";
  if (!s) return "—";
  return source
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

async function fetchRules(): Promise<MerchantRuleRow[]> {
  const res = await fetch(buildApiUrl("/api/user/merchant-rules"), {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function deleteRule(id: number): Promise<void> {
  const res = await fetch(buildApiUrl(`/api/user/merchant-rules/${id}`), {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
}

async function createRule(body: {
  keyword: string;
  display_name: string;
  counterparty_category: string;
}): Promise<void> {
  const res = await fetch(buildApiUrl("/api/user/merchant-rules"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
}

export default function ClassificationRulesPage() {
  const [rows, setRows] = React.useState<MerchantRuleRow[] | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [kw, setKw] = React.useState("");
  const [dn, setDn] = React.useState("");
  const [cat, setCat] = React.useState("");

  const load = React.useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setRows(await fetchRules());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't load your rules. Try again?");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Sorting rules</h1>
        <p className="text-sm text-muted-foreground">
          These rules help Arth automatically sort your transactions. We match keywords
          from your bank descriptions to figure out who you paid and what category it
          belongs to. Rules you&apos;ve fixed yourself show up as &quot;You fixed this&quot;
          in the table below.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add rule</CardTitle>
          <CardDescription>
            Enter a keyword that appears in your bank transactions. Arth will use the
            first matching rule it finds.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end">
          <div className="grid w-full gap-1.5 sm:max-w-[200px]">
            <Label htmlFor="kw">Keyword</Label>
            <Input
              id="kw"
              placeholder="e.g. SWIGGY"
              value={kw}
              onChange={(e) => setKw(e.target.value)}
            />
          </div>
          <div className="grid w-full flex-1 gap-1.5 sm:min-w-[200px]">
            <Label htmlFor="dn">Display name</Label>
            <Input
              id="dn"
              placeholder="e.g. Swiggy"
              value={dn}
              onChange={(e) => setDn(e.target.value)}
            />
          </div>
          <div className="grid w-full gap-1.5 sm:max-w-[280px]">
            <Label htmlFor="cat">Category</Label>
            <select
              id="cat"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={cat}
              onChange={(e) => setCat(e.target.value)}
            >
              <option value="">— Select —</option>
              {COUNTERPARTY_CATEGORY_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </div>
          <Button
            type="button"
            className="gap-2"
            disabled={!kw.trim() || !dn.trim() || !cat}
            onClick={async () => {
              setErr(null);
              try {
                await createRule({
                  keyword: kw.trim(),
                  display_name: dn.trim(),
                  counterparty_category: cat,
                });
                setKw("");
                setDn("");
                setCat("");
                await load();
              } catch (e) {
                setErr(e instanceof Error ? e.message : "Couldn't save that rule. Try again?");
              }
            }}
          >
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your rules</CardTitle>
          <CardDescription>
            {loading ? "Loading…" : `${rows?.length ?? 0} rows`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {err && (
            <p className="mb-4 text-sm text-destructive" role="alert">
              {err}
            </p>
          )}
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading rules…
            </div>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-left text-sm">
                <thead className="border-b bg-muted/50">
                  <tr>
                    <th className="p-2 font-medium">Keyword</th>
                    <th className="p-2 font-medium">Display</th>
                    <th className="p-2 font-medium">Category</th>
                    <th className="p-2 font-medium">Where it came from</th>
                    <th className="w-12 p-2" />
                  </tr>
                </thead>
                <tbody>
                  {(rows ?? []).map((r) => (
                    <tr key={r.id} className="border-b border-border/60">
                      <td className="p-2 font-mono text-xs">{r.keyword}</td>
                      <td className="p-2">{r.display_name}</td>
                      <td className="p-2 text-muted-foreground">
                        {r.counterparty_category}
                      </td>
                      <td className="p-2 text-xs text-muted-foreground">
                        {ruleSourceLabel(r.source)}
                      </td>
                      <td className="p-2">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-muted-foreground hover:text-destructive"
                          title="Delete"
                          onClick={async () => {
                            if (
                              !confirm(
                                `Remove the rule for "${r.display_name}" (${r.keyword})? Existing transactions stay as they are, but new ones won't use this rule.`,
                              )
                            )
                              return;
                            setErr(null);
                            try {
                              await deleteRule(r.id);
                              await load();
                            } catch (e) {
                              setErr(
                                e instanceof Error
                                  ? e.message
                                  : "Couldn't remove that rule. Try again?",
                              );
                            }
                          }}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
