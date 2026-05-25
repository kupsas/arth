/**
 * Root Layout — the outermost wrapper for every page in the app.
 *
 * In Next.js App Router, layout.tsx is special: it wraps all pages under
 * its directory. The root layout (at src/app/layout.tsx) wraps EVERY page.
 *
 * What we set up here:
 *   1. Fonts: Outfit (sans UI) + Geist Mono (loaded via next/font for performance)
 *   2. Providers: React Query + next-themes + TooltipProvider
 *   3. App shell: Sidebar (left) + main content area (right)
 *      - Header sits at the top of the main content area
 *      - {children} is where the actual page content renders
 *
 * The "use client" directive is NOT here because layout.tsx is a Server
 * Component by default. Only the components that use browser APIs
 * (like usePathname, useTheme) are marked "use client".
 */

import type { Metadata } from "next";
import { Outfit, Geist_Mono } from "next/font/google";

import { Providers } from "@/components/providers";
import { DemoBanner } from "@/components/demo-banner";
import { DemoFeedbackBanner } from "@/components/demo-feedback-banner";
import { Sidebar } from "@/components/layout/sidebar";
import { Header } from "@/components/layout/header";
import { MobileBlocker } from "@/components/layout/mobile-blocker";

import "./globals.css";

/** Sans-serif UI font — loaded by Next.js from Google Fonts at build time. */
const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
  display: "swap",
  weight: ["300", "400", "500", "600", "700"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Arth — Your money, your way",
  description:
    "Track spending, goals, and holdings in one place — built for how money works in India.",
  // Tab icon, bookmarks, and “Add to Home Screen” — served from public/rupees.png
  icons: {
    icon: [{ url: "/rupees.png", type: "image/png" }],
    apple: [{ url: "/rupees.png", type: "image/png" }],
    shortcut: "/rupees.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    /*
     * suppressHydrationWarning is required on <html> when using next-themes.
     * next-themes modifies the class attribute on the client to add "dark" or
     * "light", which would cause a React hydration mismatch warning without this.
     */
    <html
      lang="en"
      suppressHydrationWarning
      className={`${outfit.variable} ${geistMono.variable} h-full`}
    >
      <body className="h-full font-sans antialiased">
        <Providers>
          <div className="flex h-full min-h-0 flex-col">
            <DemoBanner />
            <DemoFeedbackBanner />
            <MobileBlocker>
              {/*
               * Full-height flex row:
               *   - Sidebar is fixed-width on the left
               *   - The right side takes remaining space (flex-1) and is itself
               *     a column: Header on top, scrollable content below
               */}
              <div className="flex h-full min-h-0 flex-1">
                <Sidebar />

                <div className="flex flex-1 flex-col overflow-hidden">
                  <Header />

                  {/* Main scrollable content area */}
                  <main className="flex-1 overflow-y-auto bg-background p-6">
                    {children}
                  </main>
                </div>
              </div>
            </MobileBlocker>
          </div>
        </Providers>
      </body>
    </html>
  );
}
