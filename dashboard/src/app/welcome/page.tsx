"use client";

/**
 * One-time demo welcome screen — explains why Arth exists and what feedback
 * would help, then sends the visitor into the sample app via Continue.
 *
 * Shown only when ``NEXT_PUBLIC_DEMO_MODE`` is on and ``arth:welcome:seen``
 * is not set in localStorage. Repeat visits skip straight to ``/chat``.
 */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { isDemoMode } from "@/lib/demo";
import { hasSeenDemoWelcome, markDemoWelcomeSeen } from "@/lib/demo-welcome";

const FEEDBACK_QUESTIONS = [
  "What excites you about the product? (if anything at all)",
  "What concerns you about the product? (if anything at all)",
] as const;

const WEALTH_FACTORS = [
  {
    tag: "Your expenses",
    aside: "(Did I overspend on Swiggy again? I should get a bike to get rid of these Uber costs ughh!)",
  },
  {
    tag: "Your assets and their performance",
    aside:
      "(Oil prices are rising again? What does that mean? BUY Reliance? NOT buy it!?)",
  },
  {
    tag: "Your goals",
    aside:
      "(Am I on track for my house down payment? Wait, I have no money for that trip to Bali this year WTF?)",
  },
  { tag: "Taxes",
    aside:
      "(What do you mean by 'declaring' RSUs? I have already paid taxes on them!)",
  },
  {
    tag: "Individual v Family",
    aside:
      "(The house is on my name, the expense account is my partner's and don't get me started on parents).",
  },
] as const;

export default function WelcomePage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [showContent, setShowContent] = useState(false);

  useEffect(() => {
    if (!isDemoMode || hasSeenDemoWelcome()) {
      router.replace("/chat");
      return;
    }
    setShowContent(true);
    setReady(true);
  }, [router]);

  function handleContinue() {
    markDemoWelcomeSeen();
    router.push("/chat");
  }

  if (!ready || !showContent) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-background"
      aria-labelledby="welcome-heading"
    >
      <div className="mx-auto flex min-h-full w-[min(1400px,92vw)] flex-col px-6 py-8 sm:px-10 sm:py-10">
        <div className="mb-6 flex items-start justify-between gap-6">
          <header className="min-w-0 space-y-2">
            <p className="text-sm font-medium tracking-wide text-muted-foreground uppercase">
              Before you explore the demo
            </p>
            <h1
              id="welcome-heading"
              className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl"
            >
              Arth
            </h1>
          </header>
          <Button
            type="button"
            size="lg"
            className="shrink-0 gap-2"
            onClick={handleContinue}
          >
            Continue
            <ArrowRight className="h-4 w-4" aria-hidden />
          </Button>
        </div>

        <div className="space-y-5 text-base leading-relaxed text-foreground/90">
          <section className="space-y-2">
            <p>
              Personal wealth management is hard. There are a lot of
              different factors you have to think about:
            </p>
            <ul className="list-disc space-y-1 pl-6">
              {WEALTH_FACTORS.map((item) => (
                <li key={item.tag}>
                  <span className="font-semibold text-foreground">{item.tag}</span>
                  {item.aside ? <> {item.aside}</> : null}
                </li>
              ))}
            </ul>
          </section>

          <section className="space-y-2">
            <p>
              Honestly, the human brain is not good enough to do all of
              these computations in parallel in a decently acceptable way. Heck, most of us are not even
              good at understanding &ldquo;compounding&rdquo; as a concept -
              let alone managing wealth end to end.
            </p>
            <p>
              I saw this problem for myself. I tried to find an
              app that actually solves it. Turns out every app in India wants
              my data so they can sell me loans, insurance, and credit cards.
              That felt wrong.
            </p>
          </section>

          <section className="space-y-2">
            <p>
              So I decided to build a personal financial intelligence platform.
              I call it{" "}
              <a
                href="https://en.wikipedia.org/wiki/Artha"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-foreground underline underline-offset-2 hover:text-foreground/80"
              >
                Arth
              </a>{" "}
              (अर्थ) - from the Sanskrit word for economics.
            </p>
            <p>
              The actual product will run locally, store data on your device
              alone, and be open-source - free to use, and something you can
              contribute to if you want.
            </p>
            <p className="text-muted-foreground">
              What you are about to see is a demo with sample data so you can
              poke around without signing up or connecting your accounts.
            </p>
          </section>

          <section className="space-y-2 rounded-xl border border-border bg-muted/40 p-4">
            <h2 className="text-lg font-semibold text-foreground">
              As you explore, I&apos;d love to know your thoughts on:
            </h2>
            <ol className="list-decimal space-y-1.5 pl-6">
              {FEEDBACK_QUESTIONS.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ol>
            <p className="text-sm text-muted-foreground">
              There is a banner at the top of the demo for you to message
              me on WhatsApp!
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
