import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

// next.config.ts lives in dashboard/ — force Turbopack’s project root here so a stray
// package-lock.json in the parent repo (Arth/) does not steal resolution and break
// imports like tailwindcss (they live in dashboard/node_modules only).
const dashboardDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * When you open the dev server through Cloudflare Tunnel, Next blocks HMR
 * (webpack-hmr / Turbopack) from “foreign” Host headers unless you allow them.
 * Not a security issue — only affects live reload in that browser tab.
 *
 * Set in .env.local (no https://, hostname only):
 *   NEXT_ALLOWED_DEV_ORIGINS=clinton-polyester-weapon-amber.trycloudflare.com
 * Quick tunnel hostnames change when you restart cloudflared — update this then.
 */
const allowedDevOrigins = (process.env.NEXT_ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  turbopack: {
    root: dashboardDir,
  },
  ...(allowedDevOrigins.length > 0 ? { allowedDevOrigins } : {}),
};

export default nextConfig;
