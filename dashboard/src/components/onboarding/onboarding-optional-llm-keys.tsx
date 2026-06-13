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
 * pasted keys”, and updates reflect immediately after save.
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
import { Label } from "@/components/ui/label";
import { MaskedSecretInput } from "@/components/ui/masked-secret-input";
import {
  ONBOARDING_INDICATIVE_CLOUD_ROWS_PER_1000,
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
  describeClassifierKeyShapeError,
  guardApiKeyInput,
  ONBOARDING_INPUT_LIMITS,
} from "@/lib/onboarding-input-validation";
import { cn } from "@/lib/utils";

/** Partial POST body: only sent keys are merged; empty string clears that provider (when allowed). */
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

/** Which cloud provider row we’re pasting into. */
type ProviderField = "openai" | "anthropic" | "google";

/** `add` = new provider row; `replace` = rotate key for a provider that already has one saved. */
type KeyPanel = { field: ProviderField; mode: "add" | "replace" } | null;

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
    label: "Google AI",
    hint: "Google AI Studio / Cloud console.",
    inputId: "llm-google",
    placeholder: "Google API key",
    shortName: "Google",
  },
  {
    field: "anthropic",
    label: "Anthropic",
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
    label: "OpenAI",
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

/** Clear the other two paste buffers so one Save sends a single provider key. */
function clearOtherProviderInputs(
  field: ProviderField,
  setters: {
    setOpenai: (s: string) => void;
    setAnthropic: (s: string) => void;
    setGoogle: (s: string) => void;
  },
) {
  if (field !== "google") setters.setGoogle("");
  if (field !== "anthropic") setters.setAnthropic("");
  if (field !== "openai") setters.setOpenai("");
}

export function OnboardingOptionalLlmKeys() {
  const qc = useQueryClient();
  /** Server-side presence flags refetched on mount and after save/remove. */
  const statusQ = useOnboardingClassifierStatus();

  const [openai, setOpenai] = React.useState("");
  const [anthropic, setAnthropic] = React.useState("");
  const [google, setGoogle] = React.useState("");
  /** Single paste target: adding a new provider or replacing an existing key. */
  const [panel, setPanel] = React.useState<KeyPanel>(null);
  /** When multiple keys exist, confirm removing one provider (cannot drop the last saved key). */
  const [removeConfirm, setRemoveConfirm] = React.useState<ProviderField | null>(null);
  const [msg, setMsg] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const setters = React.useMemo(
    () => ({ setOpenai, setAnthropic, setGoogle }),
    [],
  );

  const invalidateClassifierStatus = React.useCallback(() => {
    void qc.invalidateQueries({ queryKey: [...onboardingClassifierStatusKey] });
  }, [qc]);

  async function onSave() {
    setMsg(null);
    setErr(null);

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

    if (panel?.mode === "replace") {
      const field = panel.field;
      const val = field === "google" ? g : field === "anthropic" ? a : o;
      if (!val) {
        setErr("Paste the new key before saving.");
        return;
      }
      const shapeErr = describeClassifierKeyShapeError(field, val);
      if (shapeErr) {
        setErr(shapeErr);
        return;
      }
      const body =
        field === "openai"
          ? { openai_api_key: val }
          : field === "anthropic"
            ? { anthropic_api_key: val }
            : { google_api_key: val };
      setBusy(true);
      try {
        await postKeys(body);
        invalidateClassifierStatus();
        setOpenai("");
        setAnthropic("");
        setGoogle("");
        setPanel(null);
        setMsg("Saved. Your key is encrypted on this machine.");
      } catch (e) {
        setErr(getUserFacingErrorMessage(e) || "Couldn't save keys. Try again.");
      } finally {
        setBusy(false);
      }
      return;
    }

    // Add flow: user opened “Add” on a provider without a key.
    const body: {
      openai_api_key?: string;
      anthropic_api_key?: string;
      google_api_key?: string;
    } = {};
    if (o) body.openai_api_key = o;
    if (a) body.anthropic_api_key = a;
    if (g) body.google_api_key = g;

    const shapeErr =
      (o && describeClassifierKeyShapeError("openai", o)) ||
      (a && describeClassifierKeyShapeError("anthropic", a)) ||
      (g && describeClassifierKeyShapeError("google", g));
    if (shapeErr) {
      setErr(shapeErr);
      return;
    }

    if (Object.keys(body).length === 0) {
      setMsg(
        'Open a provider above with “Add”, paste the key, then click “Save key”.',
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
      setPanel(null);
      setMsg("Saved. Your key is encrypted on this machine.");
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
      setPanel(null);
      setMsg("Removed that saved key.");
    } catch (e) {
      setErr(getUserFacingErrorMessage(e) || "Couldn't remove that key. Try again?");
    } finally {
      setBusy(false);
    }
  }

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
        <CardTitle>API Keys - Classifier</CardTitle>
        <CardDescription>
          Auto-labelling without an AI key is genuinely bad. Our built-in sorting rules handle
          clear cases, but messy merchant names and edge cases trip them up — badly. One key below
          fixes this. Takes 30 seconds, costs almost nothing.
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
            Couldn&apos;t load key status. You can still paste a key and save.
          </p>
        )}
        {!loadingStatus && st?.has_any_api_key && (
          <p className="text-sm text-muted-foreground rounded-md border border-border bg-muted/40 px-3 py-2">
            Key saved — you&apos;re good to go.
          </p>
        )}

        <div className="space-y-1 text-sm text-muted-foreground leading-relaxed">
          <p>
            Cost:{" "}
            <strong className="text-foreground">~{cloudRowsPer1k}</strong> AI-assisted rows per
            1,000 transactions ≈{" "}
            <strong className="text-foreground">{formatUsd(costForCloudSlice, 3)}</strong>.
          </p>
        </div>

        <section className="space-y-3" aria-labelledby="llm-keys-form-heading">
          <h3 id="llm-keys-form-heading" className="text-sm font-semibold">
            Choose a provider
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
               * “Replace” on the active row stays loud; other “Add”s stay muted (still clickable).
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

              const showPastePanel =
                panel?.field === field &&
                ((panel.mode === "add" && !hasKey) ||
                  (panel.mode === "replace" && hasKey));

              const canRemoveThisRow = hasKey && savedKeyCount > 1;

              return (
                <div key={field} className="grid gap-2">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <Label htmlFor={inputId} className="shrink-0 pt-0.5">
                      {label}
                    </Label>
                    <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
                      {hasKey ? (
                        <>
                          {canRemoveThisRow &&
                            (removeConfirm === field ? (
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
                                  setPanel(null);
                                }}
                              >
                                Remove
                              </Button>
                            ))}
                          {!removeConfirm || removeConfirm !== field ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="border-border bg-background text-foreground hover:bg-muted"
                              disabled={busy}
                              onClick={() => {
                                setRemoveConfirm(null);
                                setPanel({ field, mode: "replace" });
                                clearOtherProviderInputs(field, setters);
                              }}
                            >
                              Replace
                            </Button>
                          ) : null}
                        </>
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
                            setPanel({ field, mode: "add" });
                            clearOtherProviderInputs(field, setters);
                          }}
                        >
                          Add
                        </Button>
                      )}
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">{hint}</p>
                  {showPastePanel && (
                    <div className="grid gap-1">
                      <MaskedSecretInput
                        id={inputId}
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
          {panel !== null && (
            <Button type="button" onClick={() => void onSave()} disabled={busy}>
              {busy ? "Saving…" : "Save key"}
            </Button>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
