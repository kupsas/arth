"use client";

/**
 * Sidebar — the left-hand navigation panel.
 *
 * Key concepts used here:
 *
 * 1. usePathname() — a Next.js hook that tells us the current URL path
 *    (e.g. "/transactions"). We use this to highlight the active nav link.
 *
 * 2. Link — Next.js's router-aware <a> tag. It does client-side navigation
 *    without a full page reload, which is much faster and feels smoother.
 *
 * 3. cn() — our utility from lib/utils.ts that merges Tailwind class strings
 *    conditionally. e.g. cn("base-class", isActive && "active-class")
 *
 * The sidebar is fixed on desktop. On mobile it would collapse, but we'll
 * handle responsive behaviour in Phase 3h (polish).
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  ClipboardList,
  LayoutDashboard,
  ArrowLeftRight,
  Wallet,
  Target,
  PieChart,
  Settings,
  LineChart,
  Tags,
  MessageCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";

/** Each entry defines one navigation item in the sidebar. */
const NAV_ITEMS = [
  {
    label: "Home",
    href: "/",
    icon: LayoutDashboard,
  },
  {
    label: "Ask Arth",
    href: "/chat",
    icon: MessageCircle,
  },
  {
    label: "Transactions",
    href: "/transactions",
    icon: ArrowLeftRight,
  },
  {
    label: "Goals",
    href: "/goals",
    icon: Target,
  },
  {
    label: "Simulate",
    href: "/simulate",
    icon: LineChart,
  },
  {
    label: "Holdings",
    href: "/portfolio",
    icon: PieChart,
  },
  {
    label: "Review",
    href: "/review",
    icon: ClipboardList,
  },
  {
    label: "Sorting rules",
    href: "/classification-rules",
    icon: Tags,
  },
  {
    label: "Settings",
    href: "/settings",
    icon: Settings,
  },
] as const;

export function Sidebar() {
  // usePathname gives us the current URL path (e.g. "/" or "/transactions")
  const pathname = usePathname();

  return (
    <aside className="flex h-full w-56 flex-col border-r border-border bg-sidebar">
      {/* ── Brand header ─────────────────────────────────────────── */}
      <div className="flex h-14 items-center gap-2.5 border-b border-sidebar-border px-4">
        {/* Wallet icon as the Arth logo stand-in */}
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-sidebar-primary">
          <Wallet className="h-4 w-4 text-sidebar-primary-foreground" />
        </div>
        <span className="text-sm font-semibold tracking-tight text-sidebar-foreground">
          Arth
        </span>
      </div>

      {/* ── Navigation links ──────────────────────────────────────── */}
      <nav className="flex flex-1 flex-col gap-1 p-2">
        {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
          // A link is "active" if the current path matches its href.
          // Special case for "/" (dashboard): only exact match, so
          // "/transactions" doesn't accidentally highlight Dashboard too.
          const isActive =
            href === "/" ? pathname === "/" : pathname.startsWith(href);

          return (
            <Link
              key={href}
              href={href}
              className={cn(
                // Base styles: full-width row, rounded, smooth transition
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? // Active: use sidebar-primary colours (bold highlight)
                    "bg-sidebar-primary text-sidebar-primary-foreground"
                  : // Inactive: subtle text, hover shows accent background
                    "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* ── Footer (version) ──────────────────────────────────────── */}
      <div className="border-t border-sidebar-border px-4 py-3">
        <p className="text-xs text-muted-foreground">Arth v0.5</p>
      </div>
    </aside>
  );
}
