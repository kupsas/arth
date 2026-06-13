"use client";

/**
 * Text input that looks like a password field but avoids browser password-manager
 * prompts (no ``type="password"``). Use for API keys and similar secrets.
 */
import * as React from "react";
import { Eye, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type MaskedSecretInputProps = Omit<
  React.ComponentProps<typeof Input>,
  "type"
> & {
  /** When true, characters render as bullets (default: masked). */
  defaultMasked?: boolean;
};

export function MaskedSecretInput({
  className,
  defaultMasked = true,
  ...props
}: MaskedSecretInputProps) {
  const [masked, setMasked] = React.useState(defaultMasked);

  return (
    <div className="relative flex w-full items-center">
      <Input
        type="text"
        autoComplete="off"
        data-1p-ignore
        data-lpignore="true"
        data-form-type="other"
        spellCheck={false}
        className={cn(
          masked && "[-webkit-text-security:disc]",
          "pr-9",
          className,
        )}
        {...props}
      />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-0.5 size-7 text-muted-foreground hover:text-foreground"
        aria-label={masked ? "Show secret" : "Hide secret"}
        onClick={() => setMasked((m) => !m)}
        tabIndex={-1}
      >
        {masked ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
      </Button>
    </div>
  );
}
