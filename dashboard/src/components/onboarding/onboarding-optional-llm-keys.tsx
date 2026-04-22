"use client";

/**
 * Optional LLM API keys during onboarding (Track 2 Phase 3c).
 *
 * Keys are stored encrypted in the same ``UserSecrets`` row as PDF passwords.
 * If you skip this, the pipeline stays **rules-only** (plus whatever you teach it
 * in the classification batch). That is a supported path — not a second-class mode.
 */

import * as React from "react";

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

async function postKeys(body: {
  openai_api_key?: string | null;
  anthropic_api_key?: string | null;
  google_api_key?: string | null;
}): Promise<void> {
  const res = await fetch(buildApiUrl("/api/onboarding/api-key"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
}

export function OnboardingOptionalLlmKeys() {
  const [openai, setOpenai] = React.useState("");
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  const [msg, setMsg] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function onSave() {
    setMsg(null);
    setBusy(true);
    try {
      await postKeys({
        openai_api_key: openai || null,
        anthropic_api_key: anthropic || null,
        google_api_key: google || null,
      });
      setMsg("Saved — keys are encrypted at rest. Clear a field and save again to remove a key.");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="max-w-lg">
      <CardHeader>
        <CardTitle>Optional: LLM classification</CardTitle>
        <CardDescription>
          Paste **one** provider key if you want automatic narration tagging. Leave everything blank
          to stay fully offline/rules-driven — the app lowers the “pause for human review” threshold
          when no key is present so you are not stuck with huge unknown piles.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid gap-1">
          <Label htmlFor="llm-openai">OpenAI (optional)</Label>
          <Input
            id="llm-openai"
            type="password"
            autoComplete="off"
            value={openai}
            onChange={(e) => setOpenai(e.target.value)}
            placeholder="sk-…"
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="llm-anthropic">Anthropic (optional)</Label>
          <Input
            id="llm-anthropic"
            type="password"
            autoComplete="off"
            value={anthropic}
            onChange={(e) => setAnthropic(e.target.value)}
            placeholder="sk-ant-…"
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="llm-google">Google AI (optional)</Label>
          <Input
            id="llm-google"
            type="password"
            autoComplete="off"
            value={google}
            onChange={(e) => setGoogle(e.target.value)}
          />
        </div>
        {msg && <p className="text-sm">{msg}</p>}
        <Button type="button" onClick={() => void onSave()} disabled={busy}>
          {busy ? "Saving…" : "Save keys"}
        </Button>
      </CardContent>
    </Card>
  );
}
