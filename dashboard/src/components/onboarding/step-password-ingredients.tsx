"use client";

/**
 * Collect PAN / DOB / HDFC fragments for PDF statement decryption (Track 2 — WS3).
 *
 * Two modes:
 * - **wizard** — standalone step after “Your name”; saves to ``UserSecrets`` then advances.
 * - **resume-import** — shown during mail import when the API reports ``needs_password``;
 *   user fixes ingredients and retries the same Gmail batch.
 */

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  fetchOnboardingPasswordRequirements,
  postOnboardingPasswordIngredients,
} from "@/lib/api";
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error";

export type PasswordRequirementRow = {
  parser_key: string;
  display_name: string;
  required_fields: string[];
  notes?: string | null;
};

type StepPasswordIngredientsProps = {
  mode: "wizard" | "resume-import";
  /** When mail import paused on a template, highlight which recipe failed (optional). */
  blockingParserKey?: string | null;
  /** Wizard: after a successful save, move to the next step. Import: omitted. */
  onContinue?: () => void;
  /** Import mode: after save, caller triggers ``resume_after_password`` chunk POST. */
  onSaved?: () => void | Promise<void>;
};

const FIELD_LABELS: Record<string, string> = {
  pan: "PAN (10 characters)",
  dob_ddmmyyyy: "Date of birth",
  hdfc_account_number: "HDFC savings account number (digits)",
  hdfc_cc_last4: "HDFC credit card — last 4 digits",
};

export function StepPasswordIngredients({
  mode,
  blockingParserKey,
  onContinue,
  onSaved,
}: StepPasswordIngredientsProps) {
  const [requirements, setRequirements] = React.useState<PasswordRequirementRow[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [pan, setPan] = React.useState("");
  const [dobIso, setDobIso] = React.useState("");
  const [hdfcAccount, setHdfcAccount] = React.useState("");
  const [hdfcCc4, setHdfcCc4] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const rows = await fetchOnboardingPasswordRequirements();
        if (!cancelled) setRequirements(rows);
      } catch (e) {
        if (!cancelled) setLoadError(getUserFacingErrorMessage(e) ?? "Could not load password hints.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const neededFields = React.useMemo(() => {
    const u = new Set<string>();
    for (const r of requirements ?? []) {
      for (const f of r.required_fields) {
        u.add(f);
      }
    }
    return u;
  }, [requirements]);

  async function handleSave() {
    setSaveError(null);
    if (mode === "wizard" && (!requirements || requirements.length === 0)) {
      onContinue?.();
      return;
    }
    setBusy(true);
    try {
      await postOnboardingPasswordIngredients({
        pan: pan.trim() || null,
        dob_iso: dobIso.trim() || null,
        hdfc_account_number: hdfcAccount.trim() || null,
        hdfc_cc_last4: hdfcCc4.trim() || null,
      });
      if (mode === "wizard" && onContinue) {
        onContinue();
      }
      if (mode === "resume-import" && onSaved) {
        await onSaved();
      }
    } catch (e) {
      setSaveError(getUserFacingErrorMessage(e) ?? "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  const title =
    mode === "wizard"
      ? "Statement PDF passwords"
      : "We need a correct PDF password";
  const description =
    mode === "wizard"
      ? "Banks email password-protected PDFs. We derive the unlock string from your PAN, date of birth, " +
        "and account details — same idea as typing them in manually, but stored encrypted on this device only."
      : "One statement could not be opened. Update the fields below (or fix your .env keys), then retry the import.";

  return (
    <Card className="max-w-xl border-dashed">
      <CardHeader>
        <CardTitle className="text-lg">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        {blockingParserKey && (
          <p className="text-xs text-muted-foreground mt-2">
            Hint: template key <span className="font-mono">{blockingParserKey}</span>
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {loadError && (
          <p className="text-sm text-destructive" role="alert">
            {loadError}
          </p>
        )}
        {requirements && requirements.length === 0 && mode === "wizard" && (
          <p className="text-sm text-muted-foreground">
            No statement PDF senders showed up in discovery, or templates are not seeded yet — you can skip
            this step. You can still add secrets later under Settings.
          </p>
        )}
        {(neededFields.has("pan") || mode === "resume-import") && (
          <div className="space-y-2">
            <Label htmlFor="arth-pan">{FIELD_LABELS.pan}</Label>
            <Input
              id="arth-pan"
              autoComplete="off"
              value={pan}
              onChange={(e) => setPan(e.target.value)}
              placeholder="ABCDE1234F"
            />
          </div>
        )}
        {(neededFields.has("dob_ddmmyyyy") || mode === "resume-import") && (
          <div className="space-y-2">
            <Label htmlFor="arth-dob">{FIELD_LABELS.dob_ddmmyyyy}</Label>
            <Input
              id="arth-dob"
              type="date"
              value={dobIso}
              onChange={(e) => setDobIso(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              We convert this to DDMMYYYY for HDFC-style passwords automatically.
            </p>
          </div>
        )}
        {(neededFields.has("hdfc_account_number") || mode === "resume-import") && (
          <div className="space-y-2">
            <Label htmlFor="arth-hdfc-acct">{FIELD_LABELS.hdfc_account_number}</Label>
            <Input
              id="arth-hdfc-acct"
              inputMode="numeric"
              value={hdfcAccount}
              onChange={(e) => setHdfcAccount(e.target.value)}
            />
          </div>
        )}
        {(neededFields.has("hdfc_cc_last4") || mode === "resume-import") && (
          <div className="space-y-2">
            <Label htmlFor="arth-hdfc-cc">{FIELD_LABELS.hdfc_cc_last4}</Label>
            <Input
              id="arth-hdfc-cc"
              inputMode="numeric"
              maxLength={4}
              value={hdfcCc4}
              onChange={(e) => setHdfcCc4(e.target.value)}
            />
          </div>
        )}
        {requirements?.map((r) => (
          <details key={r.parser_key} className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">{r.display_name}</summary>
            {r.notes && <p className="mt-1 pl-2 border-l-2">{r.notes}</p>}
          </details>
        ))}
        {saveError && (
          <p className="text-sm text-destructive" role="alert">
            {saveError}
          </p>
        )}
        <Button type="button" onClick={() => void handleSave()} disabled={busy}>
          {busy
            ? "Saving…"
            : mode === "wizard"
              ? requirements?.length === 0
                ? "Skip for now"
                : "Save and continue"
              : "Save and retry import"}
        </Button>
      </CardContent>
    </Card>
  );
}
