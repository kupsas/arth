"use client";

/**
 * Wrapper around ``PdfPasswordConfigFields`` for the import pause card (wizard PDF secrets
 * live on the Config step next to identity).
 *
 * TODO: When derived passwords still fail after retry, offer **manual PDF password** input.
 */

import * as React from "react";

import { PdfPasswordConfigFields } from "@/components/onboarding/pdf-password-config-fields";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useOnboardingBackfillSources } from "@/hooks/use-onboarding";

type StepPasswordIngredientsProps = {
  blockingParserKey?: string | null;
  onSaved?: () => void | Promise<void>;
};

export function StepPasswordIngredients({
  blockingParserKey,
  onSaved,
}: StepPasswordIngredientsProps) {
  const sourcesQ = useOnboardingBackfillSources();

  const title = "We need a correct PDF password";
  const description =
    "One statement could not be opened. Update the fields below, then retry the import.";

  return (
    <Card className="max-w-xl border-dashed">
      <CardHeader>
        <CardTitle className="text-lg">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <PdfPasswordConfigFields
          mode="resume-import"
          backfillSources={sourcesQ.data}
          blockingParserKey={blockingParserKey ?? null}
          suppressIntro
          onSubmitSuccess={async () => {
            await onSaved?.();
          }}
        />
      </CardContent>
    </Card>
  );
}
