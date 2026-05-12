"use client";

/**
 * Settings cards for the conversational agent — encrypted API keys + LiteLLM model ids.
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchAgentConfig,
  fetchAgentKeysStatus,
  postAgentConfig,
  postAgentKeys,
} from "@/lib/api";
import posthog from "posthog-js";

const MODEL_PRESETS = [
  "gemini/gemini-3-flash-preview",
  "gemini/gemini-2.5-flash",
  "anthropic/claude-sonnet-4-6",
  "anthropic/claude-haiku-4-5",
  "openai/gpt-5.4-mini-2026-03-17",
];

export function AgentChatLlmSettings() {
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: ["agent-keys-status"],
    queryFn: fetchAgentKeysStatus,
  });
  const configQ = useQuery({
    queryKey: ["agent-config"],
    queryFn: fetchAgentConfig,
  });

  const [openai, setOpenai] = React.useState("");
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  const [modelChoice, setModelChoice] = React.useState<string>("");
  const [fallback, setFallback] = React.useState("");
  const [banner, setBanner] = React.useState<string | null>(null);

  React.useEffect(() => {
    const row = configQ.data;
    if (!row) return;
    setModelChoice(row.agent_model);
    setFallback(row.agent_fallback_chain);
  }, [configQ.data]);

  const saveKeys = useMutation({
    mutationFn: async () => {
      const body: Record<string, string> = {};
      if (openai.trim()) body.openai_api_key = openai.trim();
      if (anthropic.trim()) body.anthropic_api_key = anthropic.trim();
      if (google.trim()) body.google_api_key = google.trim();
      if (Object.keys(body).length === 0) {
        throw new Error("Paste at least one key, then save.");
      }
      return postAgentKeys(body);
    },
    onSuccess: async () => {
      setBanner("Saved agent keys.");
      setOpenai("");
      setAnthropic("");
      setGoogle("");
      await qc.invalidateQueries({ queryKey: ["agent-keys-status"] });
    },
    onError: (e: Error) => setBanner(e.message),
  });

  /** Empty string clears that slot in ``UserSecrets`` (same as API contract). */
  const removeStoredAgentKey = useMutation({
    mutationFn: (which: "openai" | "anthropic" | "google") => {
      if (which === "openai") return postAgentKeys({ openai_api_key: "" });
      if (which === "anthropic") return postAgentKeys({ anthropic_api_key: "" });
      return postAgentKeys({ google_api_key: "" });
    },
    onSuccess: async (_, which) => {
      posthog.capture("agent_key_removed", { provider: which });
      setBanner("Removed that saved key from this device.");
      await qc.invalidateQueries({ queryKey: ["agent-keys-status"] });
    },
    onError: (e: Error) => setBanner(e.message),
  });

  const saveModel = useMutation({
    mutationFn: () =>
      postAgentConfig({
        agent_model: modelChoice.trim(),
        agent_fallback_chain: fallback.trim(),
      }),
    onSuccess: async () => {
      posthog.capture("agent_model_settings_saved", {
        model: modelChoice.trim(),
        has_fallback_chain: Boolean(fallback.trim()),
      });
      setBanner("Saved agent model settings.");
      await qc.invalidateQueries({ queryKey: ["agent-config"] });
    },
    onError: (e: Error) => setBanner(e.message),
  });

  const st = statusQ.data;
  const selectOptions = React.useMemo(() => {
    const extra = modelChoice && !MODEL_PRESETS.includes(modelChoice) ? [modelChoice] : [];
    return [...MODEL_PRESETS, ...extra];
  }, [modelChoice]);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">API keys - Chat</CardTitle>
          <CardDescription>
            Stored encrypted on this device. You need at least one provider before the chat tab can run models (unless you configure keys only on the server).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {banner && (
            <p className="text-sm text-muted-foreground" role="status">
              {banner}
            </p>
          )}
          {statusQ.isLoading && <p className="text-sm text-muted-foreground">Loading key status…</p>}
          {st && (
            <div className="space-y-1 text-sm text-muted-foreground">
              <p>
                Active providers:{" "}
                {[
                  st.has_openai_api_key ? "OpenAI" : null,
                  st.has_anthropic_api_key ? "Anthropic" : null,
                  st.has_google_api_key ? "Google" : null,
                ]
                  .filter(Boolean)
                  .join(", ") || "none detected"}
              </p>
              {(st.stored_has_openai_api_key ||
                st.stored_has_anthropic_api_key ||
                st.stored_has_google_api_key) && (
                <p className="text-xs">
                  A saved key stays on this device until you remove it — empty boxes above do not
                  remove old keys.
                </p>
              )}
            </div>
          )}
          <div className="grid gap-3">
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Label htmlFor="settings-agent-openai">OpenAI</Label>
                {st?.stored_has_openai_api_key && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-auto py-0 text-xs text-muted-foreground"
                    disabled={removeStoredAgentKey.isPending}
                    onClick={() => void removeStoredAgentKey.mutateAsync("openai")}
                  >
                    Remove saved key
                  </Button>
                )}
              </div>
              <Input
                id="settings-agent-openai"
                type="password"
                autoComplete="off"
                value={openai}
                onChange={(e) => setOpenai(e.target.value)}
                placeholder="sk-…"
              />
            </div>
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Label htmlFor="settings-agent-anthropic">Anthropic</Label>
                {st?.stored_has_anthropic_api_key && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-auto py-0 text-xs text-muted-foreground"
                    disabled={removeStoredAgentKey.isPending}
                    onClick={() => void removeStoredAgentKey.mutateAsync("anthropic")}
                  >
                    Remove saved key
                  </Button>
                )}
              </div>
              <Input
                id="settings-agent-anthropic"
                type="password"
                autoComplete="off"
                value={anthropic}
                onChange={(e) => setAnthropic(e.target.value)}
                placeholder="sk-ant-…"
              />
            </div>
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Label htmlFor="settings-agent-google">Google AI</Label>
                {st?.stored_has_google_api_key && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-auto py-0 text-xs text-muted-foreground"
                    disabled={removeStoredAgentKey.isPending}
                    onClick={() => void removeStoredAgentKey.mutateAsync("google")}
                  >
                    Remove saved key
                  </Button>
                )}
              </div>
              <Input
                id="settings-agent-google"
                type="password"
                autoComplete="off"
                value={google}
                onChange={(e) => setGoogle(e.target.value)}
                placeholder="AI…"
              />
            </div>
          </div>
          <Button type="button" disabled={saveKeys.isPending} onClick={() => void saveKeys.mutate()}>
            {saveKeys.isPending ? "Saving…" : "Save agent keys"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Agent chat — model</CardTitle>
          <CardDescription>
            LiteLLM uses strings like gemini/gemini-3-flash-preview. Defaults ship with the app; save here to override.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {configQ.isLoading && (
            <p className="text-sm text-muted-foreground">Loading current model…</p>
          )}
          {configQ.data && (
            <>
              <p className="text-xs text-muted-foreground">
                Built-in defaults: {configQ.data.defaults.agent_model} · fallbacks{" "}
                {configQ.data.defaults.agent_fallback_chain}
              </p>
              <div className="space-y-1">
                <Label>Primary model</Label>
                <Select
                  value={modelChoice}
                  onValueChange={(v) => setModelChoice(v ?? "")}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Pick a model" />
                  </SelectTrigger>
                  <SelectContent>
                    {selectOptions.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="settings-agent-fallback">Fallback chain (comma-separated)</Label>
                <Input
                  id="settings-agent-fallback"
                  value={fallback}
                  onChange={(e) => setFallback(e.target.value)}
                  placeholder="gemini/...,anthropic/..."
                />
              </div>
              <Button
                type="button"
                disabled={saveModel.isPending}
                onClick={() => void saveModel.mutate()}
              >
                {saveModel.isPending ? "Saving…" : "Save model settings"}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </>
  );
}
