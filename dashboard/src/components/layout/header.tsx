"use client";

/**
 * Header — the top bar that sits above the main content area.
 *
 * It shows:
 *   - The current page title (derived from the URL path)
 *   - The ThemeToggle button
 *
 * We derive the title from usePathname() rather than passing it as a prop,
 * so we don't have to thread a title string through every single page.
 */

import { usePathname } from "next/navigation";

import { ThemeToggle } from "./theme-toggle";

/** Maps URL paths to human-readable page titles. */
const PAGE_TITLES: Record<string, string> = {
  "/": "Dashboard",
  "/transactions": "Transactions",
  "/goals": "Goals",
  "/review": "Review Queue",
  "/settings": "Settings",
};

export function Header() {
  const pathname = usePathname();

  // Find the best matching title. We check exact match first, then prefix.
  // This means "/transactions/123" would still show "Transactions".
  const title =
    PAGE_TITLES[pathname] ??
    Object.entries(PAGE_TITLES).find(([path]) =>
      pathname.startsWith(path) && path !== "/"
    )?.[1] ??
    "Arth";

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-6">
      {/* Page title */}
      <h1 className="text-sm font-semibold text-foreground">{title}</h1>

      {/* Right-side controls */}
      <div className="flex items-center gap-2">
        <ThemeToggle />
      </div>
    </header>
  );
}
