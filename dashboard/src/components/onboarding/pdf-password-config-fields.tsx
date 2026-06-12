"use client";

/**
 * PDF password ingredients (PAN / DOB / HDFC customer ID) — shared by the Config step and
 * import pause UI. Visibility is driven by discovery sources + password-requirements API.
 *
 * Saved values are loaded from ``GET /api/onboarding/password-ingredients`` on mount (same
 * ``UserSecrets`` store as POST), so a refresh or revisit fills fields — same idea as the
 * identity step loading from the server, without putting PAN/DOB in browser localStorage.
 */

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  fetchOnboardingPasswordRequirements,
  fetchOnboardingPasswordIngredientsSaved,
  fetchOnboardingPdfPasswordNamePreview,
  postOnboardingPasswordIngredients,
} from "@/lib/api";
import {
  hasHdfcBankSavingsSource,
  needsPanPdfSource,
  needsProfileNameDobPdfStyle,
  needsSbiStatementSource,
} from "@/lib/pdf-password-source-flags";
import type { OnboardingBackfillSourceRow } from "@/lib/types";
import { sanitizeHtmlDateInputValue } from "@/lib/onboarding-input-validation";
import { getUserFacingErrorMessage } from "@/lib/user-facing-api-error";

export type PasswordRequirementRow = {
  parser_key: string;
  display_name: string;
  required_fields: string[];
  notes?: string | null;
};

export type PdfPasswordPayload = {
  pan: string | null;
  dob_iso: string | null;
  hdfc_customer_id: string | null;
  sbi_mobile_last5: string | null;
};

export type PdfPasswordConfigFieldsHandle = {
  getPayload: () => PdfPasswordPayload;
};

const FIELD_LABELS: Record<string, string> = {
  pan: "PAN (10 characters)",
  dob_iso: "Date of birth",
  hdfc_customer_id: "HDFC customer ID (net banking login, digits only)",
  sbi_mobile_last5: "SBI registered mobile — last 5 digits",
};

export type PdfPasswordConfigFieldsProps = {
  backfillSources?: OnboardingBackfillSourceRow[];
  mode: "wizard" | "resume-import";
  blockingParserKey?: string | null;
  /** Message when name strings are empty (Config page: point to the name fields above). */
  embeddedInConfigStep?: boolean;
  /** When wrapped in another ``Card`` (import pause), skip the duplicate title + intro paragraph. */
  suppressIntro?: boolean;
  hideSubmitButton?: boolean;
  onSubmitSuccess?: () => void | Promise<void>;
};

export const PdfPasswordConfigFields = React.forwardRef<PdfPasswordConfigFieldsHandle, PdfPasswordConfigFieldsProps>(
  function PdfPasswordConfigFields(
    {
      backfillSources,
      mode,
      blockingParserKey,
      embeddedInConfigStep,
      suppressIntro,
      hideSubmitButton,
      onSubmitSuccess,
    },
    ref,
  ) {
    const [requirements, setRequirements] = React.useState<PasswordRequirementRow[] | null>(null);
    const [loadError, setLoadError] = React.useState<string | null>(null);
    const [pan, setPan] = React.useState("");
    const [dobIso, setDobIso] = React.useState("");
    const [hdfcCustomerId, setHdfcCustomerId] = React.useState("");
    const [sbiMobileLast5, setSbiMobileLast5] = React.useState("");
    const [identityNameStrings, setIdentityNameStrings] = React.useState<string[]>([]);
    const [busy, setBusy] = React.useState(false);
    const [saveError, setSaveError] = React.useState<string | null>(null);

    const sources = backfillSources;

    React.useEffect(() => {
      let cancelled = false;
      (async () => {
        const results = await Promise.allSettled([
          fetchOnboardingPasswordRequirements(),
          fetchOnboardingPdfPasswordNamePreview(),
          fetchOnboardingPasswordIngredientsSaved(),
        ]);
        if (cancelled) return;
        const [reqR, prevR, savedR] = results;
        if (reqR.status === "fulfilled") {
          setRequirements(reqR.value);
        }
        if (prevR.status === "fulfilled") {
          setIdentityNameStrings(prevR.value.name_strings ?? []);
        }
        if (savedR.status === "fulfilled") {
          const saved = savedR.value;
          setPan((p) => (p.trim() ? p : saved.pan ?? ""));
          setDobIso((d) => (d.trim() ? d : saved.dob_iso ?? ""));
          setHdfcCustomerId((h) => (h.trim() ? h : saved.hdfc_customer_id ?? ""));
          setSbiMobileLast5((m) => (m.trim() ? m : saved.sbi_mobile_last5 ?? ""));
        }
        if (reqR.status === "rejected" || prevR.status === "rejected") {
          const e = reqR.status === "rejected" ? reqR.reason : (prevR as PromiseRejectedResult).reason;
          setLoadError(getUserFacingErrorMessage(e) ?? "Couldn't load password hints.");
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

    /** Wizard: only if discovery includes HDFC savings (not CC-only). Resume-import: trust API required_fields. */
    const showHdfcCustomerIdField =
      (mode === "wizard" && hasHdfcBankSavingsSource(sources)) ||
      (mode === "resume-import" && neededFields.has("hdfc_customer_id"));

    const showPanField = neededFields.has("pan") || mode === "resume-import" || needsPanPdfSource(sources);

    const showDobField =
      neededFields.has("dob_iso") ||
      mode === "resume-import" ||
      needsProfileNameDobPdfStyle(sources) ||
      needsSbiStatementSource(sources);

    const showSbiMobileLast5Field =
      (mode === "wizard" && needsSbiStatementSource(sources)) ||
      (mode === "resume-import" && neededFields.has("sbi_mobile_last5"));

    React.useImperativeHandle(ref, () => ({
      getPayload: (): PdfPasswordPayload => ({
        pan: pan.trim() || null,
        dob_iso: dobIso.trim() || null,
        hdfc_customer_id: hdfcCustomerId.trim() || null,
        sbi_mobile_last5: sbiMobileLast5.trim() || null,
      }),
    }));

    async function handleSave() {
      setSaveError(null);
      if (mode === "wizard" && (!requirements || requirements.length === 0)) {
        await onSubmitSuccess?.();
        return;
      }
      setBusy(true);
      try {
        await postOnboardingPasswordIngredients({
          pan: pan.trim() || null,
          dob_iso: dobIso.trim() || null,
          hdfc_customer_id: hdfcCustomerId.trim() || null,
          sbi_mobile_last5: sbiMobileLast5.trim() || null,
        });
        await onSubmitSuccess?.();
      } catch (e) {
        setSaveError(getUserFacingErrorMessage(e) ?? "Couldn't save.");
      } finally {
        setBusy(false);
      }
    }

    return (
      <div className="space-y-4">
        {!suppressIntro && (
          <div>
            <h3 className="text-base font-semibold text-foreground">PDF statement passwords</h3>
            <p className="text-sm text-muted-foreground mt-1">
              {mode === "wizard"
                ? "Derived from your name and aliases above, plus the fields below — stored encrypted on this device."
                : "One statement could not be opened. Update the fields below, then retry the import."}
            </p>
          </div>
        )}

        {loadError && (
          <p className="text-sm text-destructive" role="alert">
            {loadError}
          </p>
        )}
        {requirements && requirements.length === 0 && mode === "wizard" && (
          <p className="text-sm text-muted-foreground">
            No statement PDF senders showed up in discovery — you can skip these fields. You can add secrets
            later under Settings.
          </p>
        )}
        {showPanField && (
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

        {showDobField && (
          <div className="space-y-2">
            <Label htmlFor="arth-dob">{FIELD_LABELS.dob_iso}</Label>
            <Input
              id="arth-dob"
              type="date"
              min="1900-01-01"
              max="9999-12-31"
              value={dobIso}
              onChange={(e) => {
                const raw = e.target.value
                if (raw === "") {
                  setDobIso("")
                  return
                }
                const v = sanitizeHtmlDateInputValue(raw)
                if (v != null) setDobIso(v)
              }}
            />
          </div>
        )}

        {showHdfcCustomerIdField && (
          <div className="space-y-2">
            <Label htmlFor="arth-hdfc-cust">{FIELD_LABELS.hdfc_customer_id}</Label>
            <Input
              id="arth-hdfc-cust"
              inputMode="numeric"
              value={hdfcCustomerId}
              onChange={(e) => setHdfcCustomerId(e.target.value)}
              placeholder="Digits only"
            />
          </div>
        )}

        {showSbiMobileLast5Field && (
          <div className="space-y-2">
            <Label htmlFor="arth-sbi-mobile">{FIELD_LABELS.sbi_mobile_last5}</Label>
            <Input
              id="arth-sbi-mobile"
              inputMode="numeric"
              maxLength={5}
              value={sbiMobileLast5}
              onChange={(e) => setSbiMobileLast5(e.target.value.replace(/\D/g, "").slice(-5))}
              placeholder="Last 5 digits"
            />
            <p className="text-xs text-muted-foreground">
              Same mobile SBI has on file — combined with your date of birth to open the statement PDF.
            </p>
          </div>
        )}

        {saveError && (
          <p className="text-sm text-destructive" role="alert">
            {saveError}
          </p>
        )}

        {!hideSubmitButton && (
          <Button type="button" onClick={() => void handleSave()} disabled={busy}>
            {busy
              ? "Saving…"
              : mode === "wizard"
                ? requirements?.length === 0
                  ? "Skip for now"
                  : "Save and continue"
                : "Save and retry import"}
          </Button>
        )}
      </div>
    );
  },
);

PdfPasswordConfigFields.displayName = "PdfPasswordConfigFields";
