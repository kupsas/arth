"use client";

/**
 * Pre-classification step (Track 2 Phase 3a).
 *
 * Banks often print your name as ``LASTNAME FIRSTNAMES`` in the narration. We
 * collect first + last separately so the backend can build **safe** substring
 * aliases (we deliberately skip a bare surname-only alias — it would match
 * relatives who share that surname). Matching is case-insensitive: aliases are
 * stored uppercase and compared to uppercased bank text.
 *
 * Flow for the learner:
 * 1. Type your first name(s) and surname — watch the live preview.
 * 2. Optionally add nicknames / alternate spellings your bank uses.
 * 3. Optionally list family and friend names — use the expandable help under &quot;How family and
 *    friend names are matched&quot; for two-word vs longer names; extra spellings in Settings.
 * 4. Optionally add account/card fragments and UPI IDs — stored as ``account_hints_json``
 *    for rules-based self-transfer detection (substring match on bank narrations).
 * 5. Scroll to **PDF statement passwords** — PAN / DOB / HDFC customer ID fields appear based on
 *    which banks showed up in discovery (HDFC savings vs CC-only, ICICI Direct for PAN, etc.).
 * 6. Click **Save config** — saves identity via ``POST /api/onboarding/preclassification`` then
 *    merges PDF hints via ``POST /api/onboarding/password-ingredients``.
 * 7. Fine-tune contacts under **Settings → Classification** (optional).
 *
 * **Draft persistence:** In-progress fields are debounced to localStorage; after a
 * successful save we clear that backup. If there is no local draft, we load the
 * last POSTed values from ``GET /api/onboarding/preclassification``.
 */

import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useFormDraft } from "@/hooks/use-form-draft";
import { buildApiUrl } from "@/lib/api-base";
import {
  fetchOnboardingPreclassificationSaved,
  postOnboardingPasswordIngredients,
} from "@/lib/api";
import {
  PdfPasswordConfigFields,
  type PdfPasswordConfigFieldsHandle,
} from "@/components/onboarding/pdf-password-config-fields";
import { useOnboardingBackfillSources } from "@/hooks/use-onboarding";
import {
  guardMultilineText,
  guardSingleLineText,
  normalizePreclassificationDraft,
  ONBOARDING_INPUT_LIMITS,
} from "@/lib/onboarding-input-validation";
import { getUserFacingErrorMessage, userMessageFromApiResponseBody } from "@/lib/user-facing-api-error";
import { ChevronDown } from "lucide-react";

type PreviewResponse = { self_name: string; self_aliases: string[] };

/** One localStorage + GET payload shape for this step. */
type PreclassDraft = {
  firstName: string;
  lastName: string;
  extrasRaw: string;
  /** One person per line or comma-separated — family bucket for ``UserContact`` FAMILY rows. */
  familyNamesRaw: string;
  /** One person per line or comma-separated — friend bucket for ``UserContact`` FRIEND rows. */
  friendNamesRaw: string;
  accountFragmentsRaw: string;
  upiIdsRaw: string;
};

const PRECLASS_STORAGE_KEY = "arth_onboarding_preclass";

const PRECLASS_DEFAULT: PreclassDraft = {
  firstName: "",
  lastName: "",
  extrasRaw: "",
  familyNamesRaw: "",
  friendNamesRaw: "",
  accountFragmentsRaw: "",
  upiIdsRaw: "",
};

/** Split merged ``account_hints`` from the server back into the two text areas (heuristic: ``@`` → UPI). */
function splitHintsForForm(hints: string[]): { fragments: string; upi: string } {
  const upi: string[] = [];
  const fr: string[] = [];
  for (const h of hints) {
    const t = h.trim();
    if (!t) continue;
    if (t.includes("@")) upi.push(t);
    else fr.push(t);
  }
  return { fragments: fr.join("\n"), upi: upi.join("\n") };
}

async function fetchPreview(
  first: string,
  last: string,
  /** Same parsing as save — each string becomes an uppercase alias on the server. */
  extraAliases: string[],
): Promise<PreviewResponse | null> {
  const q = new URLSearchParams({ first_name: first, last_name: last });
  for (const a of extraAliases) {
    q.append("extra_aliases", a);
  }
  const res = await fetch(buildApiUrl(`/api/onboarding/preclassification/preview?${q}`), {
    credentials: "include",
  });
  if (!res.ok) return null;
  return res.json() as Promise<PreviewResponse>;
}

/** Split user textarea input on newlines or commas (same as extra aliases). */
function splitHintLines(raw: string): string[] {
  return raw
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

async function savePreclassification(payload: {
  first_name: string;
  last_name: string;
  extra_aliases: string[];
  /** Account/card fragments and full UPI IDs merged server-side into ``account_hints_json``. */
  account_hints: string[];
  /** Raw lines merged into ``UserContact`` rows (``contact_source`` = ONBOARDING). */
  family_names: string[];
  friend_names: string[];
}): Promise<void> {
  const res = await fetch(buildApiUrl("/api/onboarding/preclassification"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const t = await res.text();
  if (!res.ok) {
    throw new Error(userMessageFromApiResponseBody(t) || "Couldn't save. Try again.");
  }
}

export function PreClassificationForm() {
  const { value: d, setValue: setD, clearDraft, restoredFromLocalStorage } = useFormDraft(
    PRECLASS_STORAGE_KEY,
    PRECLASS_DEFAULT,
  );

  /** Used to read PAN / DOB / HDFC customer ID on the same button click as identity save. */
  const pdfSecretsRef = React.useRef<PdfPasswordConfigFieldsHandle>(null);
  const sourcesQ = useOnboardingBackfillSources();

  const [preview, setPreview] = React.useState<PreviewResponse | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [message, setMessage] = React.useState<string | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  /** Normalise localStorage / bad merges once so types and length are always safe for React + API. */
  React.useEffect(() => {
    setD((prev) => {
      const n = normalizePreclassificationDraft(prev);
      if (
        n.firstName === prev.firstName &&
        n.lastName === prev.lastName &&
        n.extrasRaw === prev.extrasRaw &&
        n.familyNamesRaw === prev.familyNamesRaw &&
        n.friendNamesRaw === prev.friendNamesRaw &&
        n.accountFragmentsRaw === prev.accountFragmentsRaw &&
        n.upiIdsRaw === prev.upiIdsRaw
      ) {
        return prev;
      }
      return n;
    });
  }, [setD]);

  const firstNameInvalid = !d.firstName.trim();
  const firstNameLen = d.firstName.length;
  const lastNameLen = d.lastName.length;

  // Same splitting rules as save — keeps preview in sync with POST /preclassification.
  const extrasList = React.useMemo(() => splitHintLines(d.extrasRaw), [d.extrasRaw]);

  const accountHintsForSave = React.useMemo(() => {
    return [...splitHintLines(d.accountFragmentsRaw), ...splitHintLines(d.upiIdsRaw)];
  }, [d.accountFragmentsRaw, d.upiIdsRaw]);

  const familyLines = React.useMemo(() => splitHintLines(d.familyNamesRaw), [d.familyNamesRaw]);
  const friendLines = React.useMemo(() => splitHintLines(d.friendNamesRaw), [d.friendNamesRaw]);

  /**
   * Hide the alias preview when the first-name field is empty — we derive this in render so the
   * debounce effect does not need ``setPreview(null)`` (which ESLint flags as setState-in-effect).
   * Stale ``preview`` state after clearing the name is harmless because we never show it here.
   */
  const displayPreview = d.firstName.trim() ? preview : null;

  // If the user has no local draft, hydrate from the last successful POST (server truth).
  React.useEffect(() => {
    if (restoredFromLocalStorage) return;
    let cancelled = false;
    (async () => {
      try {
        const saved = await fetchOnboardingPreclassificationSaved();
        if (cancelled) return;
        const hasServer =
          saved.first_name.trim() !== "" ||
          saved.last_name.trim() !== "" ||
          (saved.extra_aliases?.length ?? 0) > 0 ||
          (saved.account_hints?.length ?? 0) > 0 ||
          (saved.family_names?.length ?? 0) > 0 ||
          (saved.friend_names?.length ?? 0) > 0;
        if (!hasServer) return;
        const { fragments, upi } = splitHintsForForm(saved.account_hints ?? []);
        setD((prev) => {
          // Do not clobber in-flight typing if the user started before the GET returned.
          if (
            prev.firstName.trim() ||
            prev.lastName.trim() ||
            prev.extrasRaw.trim() ||
            prev.familyNamesRaw.trim() ||
            prev.friendNamesRaw.trim() ||
            prev.accountFragmentsRaw.trim() ||
            prev.upiIdsRaw.trim()
          ) {
            return prev;
          }
          return {
            ...prev,
            firstName: saved.first_name,
            lastName: saved.last_name,
            extrasRaw: (saved.extra_aliases ?? []).join("\n"),
            familyNamesRaw: (saved.family_names ?? []).join("\n"),
            friendNamesRaw: (saved.friend_names ?? []).join("\n"),
            accountFragmentsRaw: fragments,
            upiIdsRaw: upi,
          };
        });
      } catch {
        /* offline / non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [restoredFromLocalStorage, setD]);

  // Debounced preview so we do not spam the API on every keystroke.
  // Pass extrasList so the server can merge nicknames into self_aliases (same as on save).
  React.useEffect(() => {
    if (!d.firstName.trim()) {
      return;
    }
    const t = window.setTimeout(() => {
      void fetchPreview(d.firstName.trim(), d.lastName.trim(), extrasList).then(setPreview);
    }, 300);
    return () => window.clearTimeout(t);
  }, [d.firstName, d.lastName, extrasList]);

  async function onSave() {
    setMessage(null);
    setSaveError(null);
    if (!d.firstName.trim()) {
      setSaveError("First name is required — add how your bank usually prints your given name(s).");
      return;
    }
    setSaving(true);
    try {
      // Identity must succeed first so the PDF preview APIs see your saved name strings.
      await savePreclassification({
        first_name: guardSingleLineText(d.firstName.trim(), ONBOARDING_INPUT_LIMITS.preclassFirstLastChars),
        last_name: guardSingleLineText(d.lastName.trim(), ONBOARDING_INPUT_LIMITS.preclassFirstLastChars),
        extra_aliases: extrasList,
        account_hints: accountHintsForSave,
        family_names: familyLines,
        friend_names: friendLines,
      });
      try {
        const payload = pdfSecretsRef.current?.getPayload() ?? {
          pan: null,
          dob_iso: null,
          hdfc_customer_id: null,
        };
        await postOnboardingPasswordIngredients(payload);
      } catch (e) {
        setSaveError(
          getUserFacingErrorMessage(e) ??
            "Your name was saved, but PDF secrets could not be saved. Fix the error above and click Save config again.",
        );
        return;
      }
      clearDraft();
      setMessage(
        "Saved — names, transfer hints, and PDF password ingredients are stored on this device.",
      );
    } catch (e) {
      setSaveError(getUserFacingErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>Config</CardTitle>
        <CardDescription>
          Your <strong>identity</strong> (how banks print your name) drives matching and PDF password
          guesses. Optional hints below help spot self-transfers. Further down, we ask only for PDF
          secret fields that match the accounts you linked in discovery.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-2">
          <Label htmlFor="pc-first">First name</Label>
          <Input
            id="pc-first"
            placeholder='e.g. "Sai Sashank"'
            maxLength={ONBOARDING_INPUT_LIMITS.preclassFirstLastChars}
            value={d.firstName}
            aria-invalid={firstNameInvalid}
            aria-describedby={firstNameInvalid ? "pc-first-hint pc-first-err" : "pc-first-hint"}
            onChange={(e) =>
              setD((p) => ({
                ...p,
                firstName: guardSingleLineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassFirstLastChars,
                ),
              }))
            }
            autoComplete="given-name"
          />
          <p id="pc-first-hint" className="text-xs text-muted-foreground">
            Max {ONBOARDING_INPUT_LIMITS.preclassFirstLastChars} characters ({firstNameLen}/
            {ONBOARDING_INPUT_LIMITS.preclassFirstLastChars}). Control characters and line breaks are
            stripped automatically.
          </p>
          {firstNameInvalid && (
            <p id="pc-first-err" className="text-xs text-destructive" role="alert">
              First name is required to save — we use it to build safe bank-text aliases.
            </p>
          )}
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pc-last">Last name / surname</Label>
          <Input
            id="pc-last"
            placeholder='e.g. "Kuppa"'
            maxLength={ONBOARDING_INPUT_LIMITS.preclassFirstLastChars}
            value={d.lastName}
            aria-describedby="pc-last-hint"
            onChange={(e) =>
              setD((p) => ({
                ...p,
                lastName: guardSingleLineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassFirstLastChars,
                ),
              }))
            }
            autoComplete="family-name"
          />
          <p id="pc-last-hint" className="text-xs text-muted-foreground">
            Optional. Max {ONBOARDING_INPUT_LIMITS.preclassFirstLastChars} characters (
            {lastNameLen}/{ONBOARDING_INPUT_LIMITS.preclassFirstLastChars}). Do not worry about your
            last name matching your family — we handle it.
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pc-extras">Extra aliases (optional)</Label>
          <Textarea
            id="pc-extras"
            placeholder={"One nickname per line, or comma-separated.\ne.g. SK KUPPA"}
            maxLength={ONBOARDING_INPUT_LIMITS.preclassTextareaChars}
            value={d.extrasRaw}
            aria-describedby="pc-extras-hint"
            onChange={(e) =>
              setD((p) => ({
                ...p,
                extrasRaw: guardMultilineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
                ),
              }))
            }
            rows={3}
          />
          <p id="pc-extras-hint" className="text-xs text-muted-foreground">
            Max {ONBOARDING_INPUT_LIMITS.preclassTextareaChars.toLocaleString("en-IN")} characters;
            unusual control characters are stripped.
          </p>
        </div>
        <details
          id="pc-family-friend-how"
          className="rounded-md border border-border/70 bg-muted/30 text-xs text-muted-foreground [&_summary::-webkit-details-marker]:hidden [&[open]_summary_.pc-name-match-chevron]:rotate-180"
        >
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-left select-none hover:bg-muted/40 rounded-md transition-colors">
            <span className="font-medium text-foreground">
              How family and friend names are matched
            </span>
            <ChevronDown
              aria-hidden
              className="pc-name-match-chevron size-4 shrink-0 text-muted-foreground transition-transform"
            />
          </summary>
          <div className="space-y-2 border-t border-border/60 px-3 pb-3 pt-2">
            <p>
              We search your bank and UPI text for what you type here; capitals do not matter.
              <span className="mt-1.5 block">
                <strong>Two words</strong> (e.g. &quot;Rahul Shekhawat&quot;): we look for both words in the
                bank/UPI text in <strong>either order</strong> (&quot;Rahul … Shekhawat&quot; or &quot;Shekhawat …
                Rahul&quot;). They do <strong>not</strong> have to sit next to each other — other words in the
                middle are fine.
              </span>
              <span className="mt-1.5 block">
                <strong>Three or more words</strong> (e.g. &quot;Rahul Singh Shekhawat&quot;) are matched as{" "}
                <strong>one full phrase</strong> in that order — we do not try every mix of words. If a
                message shows a shorter or reordered name, add <strong>another line</strong> with that
                version.
              </span>
            </p>
          </div>
        </details>
        <div className="grid gap-2">
          <Label htmlFor="pc-family">Family names (optional)</Label>
          <Textarea
            id="pc-family"
            aria-describedby="pc-family-friend-how pc-family-hint"
            placeholder={
              "One person per line or comma-separated — use what actually appears in bank/UPI text.\ne.g. Mom\nRahul Verma\nRahul Singh Shekhawat"
            }
            maxLength={ONBOARDING_INPUT_LIMITS.preclassTextareaChars}
            value={d.familyNamesRaw}
            onChange={(e) =>
              setD((p) => ({
                ...p,
                familyNamesRaw: guardMultilineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
                ),
              }))
            }
            rows={3}
          />
          <p id="pc-family-hint" className="text-xs text-muted-foreground">
            Max {ONBOARDING_INPUT_LIMITS.preclassTextareaChars.toLocaleString("en-IN")} characters per
            box.
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pc-friends">Friend names (optional)</Label>
          <Textarea
            id="pc-friends"
            aria-describedby="pc-family-friend-how pc-friends-hint"
            placeholder={
              "Same format as family. Add an extra line if the app uses a different spelling or order than above."
            }
            maxLength={ONBOARDING_INPUT_LIMITS.preclassTextareaChars}
            value={d.friendNamesRaw}
            onChange={(e) =>
              setD((p) => ({
                ...p,
                friendNamesRaw: guardMultilineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
                ),
              }))
            }
            rows={3}
          />
          <p id="pc-friends-hint" className="text-xs text-muted-foreground">
            Max {ONBOARDING_INPUT_LIMITS.preclassTextareaChars.toLocaleString("en-IN")} characters per
            box.
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pc-account-hints">Account &amp; card number fragments (optional)</Label>
          <Textarea
            id="pc-account-hints"
            placeholder={
              "One per line or comma-separated — first four/last four numbers (ignore the zeroes)."
            }
            maxLength={ONBOARDING_INPUT_LIMITS.preclassTextareaChars}
            value={d.accountFragmentsRaw}
            aria-describedby="pc-account-hint"
            onChange={(e) =>
              setD((p) => ({
                ...p,
                accountFragmentsRaw: guardMultilineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
                ),
              }))
            }
            rows={3}
          />
          <p id="pc-account-hint" className="text-xs text-muted-foreground">
            This helps catch transfers your name/alias does not appear on. Max{" "}
            {ONBOARDING_INPUT_LIMITS.preclassTextareaChars.toLocaleString("en-IN")} characters; control
            characters stripped.
          </p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="pc-upi-ids">Your UPI IDs (optional)</Label>
          <Textarea
            id="pc-upi-ids"
            placeholder={"One per line or comma-separated.\ne.g. yourname@okicici"}
            maxLength={ONBOARDING_INPUT_LIMITS.preclassTextareaChars}
            value={d.upiIdsRaw}
            aria-describedby="pc-upi-hint"
            onChange={(e) =>
              setD((p) => ({
                ...p,
                upiIdsRaw: guardMultilineText(
                  e.target.value,
                  ONBOARDING_INPUT_LIMITS.preclassTextareaChars,
                ),
              }))
            }
            rows={2}
          />
          <p id="pc-upi-hint" className="text-xs text-muted-foreground">
            This helps identify self-transfers and prevent double counting in expenses. Max{" "}
            {ONBOARDING_INPUT_LIMITS.preclassTextareaChars.toLocaleString("en-IN")} characters.
          </p>
        </div>
        {displayPreview && (
          <div className="rounded-md border bg-muted/40 p-3 text-sm">
            <div className="font-medium">Names we will use to recognise you:</div>
            <div className="mt-2 space-y-2 font-mono text-xs leading-relaxed">
              <div>
                {displayPreview.self_aliases.length ? displayPreview.self_aliases.join(" · ") : "—"}
              </div>
            </div>
            {(familyLines.length > 0 || friendLines.length > 0) && (
              <div className="mt-3 border-t border-border pt-3 text-muted-foreground">
                Also saving{" "}
                {familyLines.length > 0 && (
                  <span>
                    {familyLines.length} family name{familyLines.length === 1 ? "" : "s"}
                  </span>
                )}
                {familyLines.length > 0 && friendLines.length > 0 && " · "}
                {friendLines.length > 0 && (
                  <span>
                    {friendLines.length} friend name{friendLines.length === 1 ? "" : "s"}
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        <div className="border-t border-border/70 pt-6 mt-2 space-y-4">
          <PdfPasswordConfigFields
            ref={pdfSecretsRef}
            mode="wizard"
            backfillSources={sourcesQ.data}
            embeddedInConfigStep
            hideSubmitButton
          />
        </div>

        {/* <p className="text-sm text-muted-foreground">
          For family or friends who often appear in your UPI messages, add them under&nbsp;
          <span>Settings &rarr; Classification</span>
          — optional, but it helps label those payments correctly.
        </p> */}

        {message && (
          <p className="text-sm text-emerald-700 dark:text-emerald-500" role="status">
            {message}
          </p>
        )}
        {saveError && (
          <p className="text-sm text-destructive" role="alert">
            {saveError}
          </p>
        )}
      </CardContent>
      <CardFooter>
        <Button type="button" onClick={() => void onSave()} disabled={saving || !d.firstName.trim()}>
          {saving ? "Saving…" : "Save config"}
        </Button>
      </CardFooter>
    </Card>
  );
}
