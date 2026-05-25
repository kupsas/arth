"use client";

/**
 * Persistent strip in demo mode — invites feedback via WhatsApp with a
 * pre-filled message so visitors can reach the founder in one tap.
 */

import { MessageCircle } from "lucide-react";

import { isDemoMode } from "@/lib/demo";

const WHATSAPP_URL =
  "https://wa.me/916375352568?text=Hey%2C%20I%20just%20tried%20Arth%20out%20and%20wanted%20to%20get%20in%20touch%20with%20you%21";

export function DemoFeedbackBanner() {
  if (!isDemoMode) return null;

  return (
    <div className="flex shrink-0 items-center justify-center gap-2 border-b border-violet-500/30 bg-violet-500/10 px-4 py-2 text-center text-sm text-violet-950 dark:border-violet-400/25 dark:bg-violet-400/10 dark:text-violet-50">
      <MessageCircle
        className="h-4 w-4 shrink-0 text-violet-600 dark:text-violet-300"
        aria-hidden
      />
      <p className="min-w-0 leading-snug">
        Hey — if you love the product, resonate with the idea, or have feedback,
        I&apos;d love to talk. Please{" "}
        <a
          href={WHATSAPP_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium underline underline-offset-2 hover:text-violet-700 dark:hover:text-violet-200"
        >
          click here to send a message
        </a>
        , and I will get back to you.
      </p>
    </div>
  );
}
