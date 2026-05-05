"use client";

/**
 * Optional LLM API keys during onboarding (Track 2 Phase 3c).
 *
 * Copy: one indicative accuracy pair (rules vs cloud), brief cost per 1k framing.
 * Maintainers: `dashboard/src/data/classification-llm-education.ts`.
 *
 * Keys → encrypted ``UserSecrets``. Skipping keys = rules-only for gaps (supported).
 *
 * ``GET /api/onboarding/classifier-status`` reports only keys **saved via this UI**
 * (encrypted ``UserSecrets``). Server ``.env`` keys are ignored here so they don’t look like “you
 * pasted keys”, and remove/update reflects immediately.
 */

import * as React from "react";

import { useQueryClient } from "@tanstack/react-query";

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
  ONBOARDING_INDICATIVE_CLOUD_ROWS_PER_1000,
  ONBOARDING_INDICATIVE_OVERALL_PCT,
  ONBOARDING_PRIMARY_COST_USD_PER_100,
  costUsdForCloudRowCount,
  formatUsd,
} from "@/data/classification-llm-education";
import { onboardingClassifierStatusKey, useOnboardingClassifierStatus } from "@/hooks/use-onboarding";
import { buildApiUrl } from "@/lib/api-base";
import {
  getUserFacingErrorMessage,
  userMessageFromApiResponseBody,
} from "@/lib/user-facing-api-error";
import {
  describeApiKeySanitiseFailure,
  guardApiKeyInput,
  ONBOARDING_INPUT_LIMITS,
} from "@/lib/onboarding-input-validation";
import { cn } from "@/lib/utils";

/** Partial POST body: only sent keys are merged; empty string clears that provider. */
async function postKeys(body: {
  openai_api_key?: string;
  anthropic_api_key?: string;
  google_api_key?: string;
}): Promise<void> {
  const res = await fetch(buildApiUrl("/api/onboarding/api-key"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const t = await res.text();
  if (!res.ok) {
    throw new Error(
      userMessageFromApiResponseBody(t) || "Couldn't save keys. Try again.",
    );
  }
}

/** Which cloud provider row we’re adding a key for (input shown only for this row). */
type ProviderField = "openai" | "anthropic" | "google";

/**
 * One row per LLM provider: same layout, different copy and which field we read/write in state.
 * Kept as data so we don’t copy-paste three nearly identical blocks.
 */
const PROVIDER_ROWS: Array<{
  field: ProviderField;
  label: string;
  hint: React.ReactNode;
  inputId: string;
  placeholder: string;
  shortName: string;
}> = [
  {
    field: "google",
    label: "Google AI (optional)",
    hint: "Google AI Studio / Cloud console.",
    inputId: "llm-google",
    placeholder: "Google API key",
    shortName: "Google",
  },
  {
    field: "anthropic",
    label: "Anthropic (optional)",
    hint: (
      <>
        console.anthropic.com (often <span className="font-mono">sk-ant-</span>).
      </>
    ),
    inputId: "llm-anthropic",
    placeholder: "Anthropic API key",
    shortName: "Anthropic",
  },
  {
    field: "openai",
    label: "OpenAI (optional)",
    hint: (
      <>
        platform.openai.com (<span className="font-mono">sk-</span>…).
      </>
    ),
    inputId: "llm-openai",
    placeholder: "OpenAI API key",
    shortName: "OpenAI",
  },
];

export function OnboardingOptionalLlmKeys() {
  const qc = useQueryClient();
  /** Server-side presence flags refetched on mount and after save/remove. */
  const statusQ = useOnboardingClassifierStatus();

  const [openai, setOpenai] = React.useState("");
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  /** Key paste UI is hidden until “Add” is clicked for that provider. */
  const [addExpanded, setAddExpanded] = React.useState<ProviderField | null>(null);
  /** Inline remove confirmation for one provider at a time. */
  const [removeConfirm, setRemoveConfirm] = React.useState<ProviderField | null>(null);
  const [msg, setMsg] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const invalidateClassifierStatus = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: [...onboardingClassifierStatusKey] });
  }, [qc]);

  async function onSave() {
    setMsg(null);
    setErr(null);
    // Only send providers the user actually typed into — omit empty fields so we do not
    // accidentally clear keys (clearing is explicit via “Remove saved key”).
    const body: {
      openai_api_key?: string;
      anthropic_api_key?: string;
      google_api_key?: string;
    } = {};
    const o = guardApiKeyInput(openai, ONBOARDING_INPUT_LIMITS.llmApiKeyChars);
    const a = guardApiKeyInput(anthropic, ONBOARDING_INPUT_LIMITS.llmApiKeyChars);
    const g = guardApiKeyInput(google, ONBOARDING_INPUT_LIMITS.llmApiKeyChars);

    const sanitiseErr =
      describeApiKeySanitiseFailure(openai, o) ||
      describeApiKeySanitiseFailure(anthropic, a) ||
      describeApiKeySanitiseFailure(google, g);
    if (sanitiseErr) {
      setErr(sanitiseErr);
      return;
    }

    if (o) body.openai_api_key = o;
    if (a) body.anthropic_api_key = a;
    if (g) body.google_api_key = g;
    if (Object.keys(body).length === 0) {
      setMsg(
        "Paste your API key in the field you opened with “Add”, then click “Save key”.",
      );
      return;
    }
    setBusy(true);
    try {
      await postKeys(body);
      invalidateClassifierStatus();
      setOpenai("");
      setAnthropic("");
      setGoogle("");
      setAddExpanded(null);
      setMsg(
        "Saved — keys are encrypted at rest. Use “Remove” if you want to delete one.",
      );
    } catch (e) {
      setErr(getUserFacingErrorMessage(e) || "Couldn't save keys. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function removeKey(field: "openai" | "anthropic" | "google") {
    setMsg(null);
    setErr(null);
    setBusy(true);
    try {
      const body =
        field === "openai"
          ? { openai_api_key: "" }
          : field === "anthropic"
            ? { anthropic_api_key: "" }
            : { google_api_key: "" };
      await postKeys(body);
      invalidateClassifierStatus();
      if (field === "openai") setOpenai("");
      if (field === "anthropic") setAnthropic("");
      if (field === "google") setGoogle("");
      setRemoveConfirm(null);
      setMsg("Removed that saved key.");
    } catch (e) {
      setErr(getUserFacingErrorMessage(e) || "Couldn't remove that key. Try again?");
    } finally {
      setBusy(false);
    }
  }

  const acc = ONBOARDING_INDICATIVE_OVERALL_PCT;
  const cloudRowsPer1k = ONBOARDING_INDICATIVE_CLOUD_ROWS_PER_1000;
  /** $ for classifying exactly `cloudRowsPer1k` cloud rows, from benchmark “$/100 similar rows”. */
  const costForCloudSlice = costUsdForCloudRowCount(
    cloudRowsPer1k,
    ONBOARDING_PRIMARY_COST_USD_PER_100,
  );

  const st = statusQ.data;
  const loadingStatus = statusQ.isLoading;

  /** How many providers already have a key on the server (used to dull extra “Add” actions). */
  const savedKeyCount =
    (st?.has_google_api_key ? 1 : 0) +
    (st?.has_anthropic_api_key ? 1 : 0) +
    (st?.has_openai_api_key ? 1 : 0);

  return (
    <Card className="mx-auto w-full max-w-2xl">
      <CardHeader className="space-y-2">
        <CardTitle>Optional: smarter auto-labels</CardTitle>
        <CardDescription>
          Add an API key if you want cloud help labeling messy bank text. Skip to stay fully local
          (more manual fixes later).
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-5">
        {loadingStatus && (
          <p className="text-sm text-muted-foreground" role="status">
            Checking whether you already saved keys…
          </p>
        )}
        {statusQ.isError && (
          <p className="text-sm text-destructive" role="alert">
            Couldn&apos;t load saved-key status. You can still paste keys and save — nothing was blocked.
          </p>
        )}
        {!loadingStatus && st?.has_any_api_key && (
          <p className="text-sm text-muted-foreground rounded-md border border-border bg-muted/40 px-3 py-2">
            You already have a classifier key on file — you don&apos;t need to paste again unless you
            replace it (remove first, then add).
          </p>
        )}

        <div className="space-y-3 text-sm text-muted-foreground leading-relaxed">
          <p>
            When you continue, Arth will fetch bank alert emails and parse transactions.{" "}
            Every classification starts with a{" "}
            <strong className="text-foreground">local rules engine</strong>. If you add an API key below,
            only rows that still need help may be sent to a cloud model; if you skip keys, we never
            call an external model for this step.
          </p>
          <p>
            <strong className="text-foreground">Without a cloud key,</strong> we never call an
            external model: you still get an automatic first pass, but you&apos;ll spend more time in
            the review step fixing labels.

            <strong className="text-foreground">With a key,</strong> a small cloud model fills the
            fuzzy bits (weird merchant text, edge cases). Same pipeline — the difference is how much
            automation you get before you touch the rows yourself.
          </p>
          <p>
            <strong className="text-foreground">Overall classification quality (indicative):</strong>{" "}
            think <strong className="text-foreground">~{acc.rulesOnly}%</strong> of labels looking right
            without any cloud help vs <strong className="text-foreground">~{acc.withCloudModel}%</strong>{" "}
            when the cloud step runs.
          </p>
          <p>
            <strong className="text-foreground">Cost (indicative):</strong> expect on the order of{" "}
            <strong className="text-foreground">~{cloudRowsPer1k}</strong> cloud-classified rows per{" "}
            <strong className="text-foreground">1,000</strong> transactions; classifying that slice is
            about <strong className="text-foreground">{formatUsd(costForCloudSlice, 3)}</strong> at
            March 2026 API rates.
          </p>
        </div>

        <section className="space-y-3" aria-labelledby="llm-keys-form-heading">
          <h3 id="llm-keys-form-heading" className="text-sm font-semibold">
            Add a key (optional)
          </h3>
          <div className="grid gap-4">
            {PROVIDER_ROWS.map(({ field, label, hint, inputId, placeholder, shortName }) => {
              const hasKey =
                !loadingStatus &&
                (field === "google"
                  ? !!st?.has_google_api_key
                  : field === "anthropic"
                    ? !!st?.has_anthropic_api_key
                    : !!st?.has_openai_api_key);

              /**
               * With zero keys saved, every “Add” is high-contrast. Once any key exists, only
               * “Remove” stays loud; other “Add”s stay muted (still clickable — opens the paste
               * field so you can add another provider or replace without hunting for a dead control).
               */
              const addLooksPrimary =
                !loadingStatus && !hasKey && savedKeyCount === 0;
              /** Visual-only: grey styling when another provider already has a key. */
              const addMutedBecauseOtherProvider =
                !loadingStatus && !hasKey && savedKeyCount > 0;

              const inputValue =
                field === "google" ? google : field === "anthropic" ? anthropic : openai;
              const setInput =
                field === "google"
                  ? setGoogle
                  : field === "anthropic"
                    ? setAnthropic
                    : setOpenai;

              return (
                <div key={field} className="grid gap-2">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <Label htmlFor={inputId} className="shrink-0 pt-0.5">
                      {label}
                    </Label>
                    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
                      {hasKey ? (
                        removeConfirm === field ? (
                          <div
                            className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto"
                            role="group"
                            aria-label={`Confirm remove ${shortName} key`}
                          >
                            <span className="text-xs text-muted-foreground">
                              Remove saved {shortName} key?
                            </span>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="border-border bg-background text-foreground hover:bg-muted"
                              disabled={busy}
                              onClick={() => void removeKey(field)}
                            >
                              Remove
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => setRemoveConfirm(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="border-border bg-background text-foreground hover:bg-muted"
                            disabled={busy}
                            onClick={() => {
                              setRemoveConfirm(field);
                              setAddExpanded(null);
                            }}
                          >
                            Remove
                          </Button>
                        )
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={busy || loadingStatus}
                          className={cn(
                            addLooksPrimary &&
                              "border-border bg-background text-foreground hover:bg-muted",
                            addMutedBecauseOtherProvider &&
                              "border-border text-muted-foreground opacity-45",
                            loadingStatus && "opacity-40",
                          )}
                          onClick={() => {
                            setRemoveConfirm(null);
                            setAddExpanded(field);
                            // Only one paste target at a time — avoid sending two keys in one save.
                            if (field !== "google") setGoogle("");
                            if (field !== "anthropic") setAnthropic("");
                            if (field !== "openai") setOpenai("");
                          }}
                        >
                          Add
                        </Button>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{hint}</p>
                  {addExpanded === field && !hasKey && (
                    <div className="grid gap-1">
                      <Input
                        id={inputId}
                        type="password"
                        autoComplete="off"
                        maxLength={ONBOARDING_INPUT_LIMITS.llmApiKeyChars}
                        value={inputValue}
                        aria-describedby={`${inputId}-paste-hint`}
                        onChange={(e) =>
                          setInput(
                            guardApiKeyInput(
                              e.target.value,
                              ONBOARDING_INPUT_LIMITS.llmApiKeyChars,
                            ),
                          )
                        }
                        placeholder={placeholder}
                      />
                      <p id={`${inputId}-paste-hint`} className="text-xs text-muted-foreground">
                        Paste one line only — spaces and control characters are removed automatically (max{" "}
                        {ONBOARDING_INPUT_LIMITS.llmApiKeyChars.toLocaleString("en-IN")} characters).
                      </p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {msg && (
            <p className="text-sm text-emerald-700 dark:text-emerald-500" role="status">
              {msg}
            </p>
          )}
          {err && (
            <p className="text-sm text-destructive" role="alert">
              {err}
            </p>
          )}
          {addExpanded !== null && (
            <Button type="button" onClick={() => void onSave()} disabled={busy}>
              {busy ? "Saving…" : "Save key"}
            </Button>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
