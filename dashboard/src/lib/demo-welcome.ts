/**
 * One-time demo welcome screen — remembers dismissal in localStorage so repeat
 * visits skip straight to the app (even when opening the shared link again).
 */

export const DEMO_WELCOME_SEEN_KEY = "arth:welcome:seen";

export function hasSeenDemoWelcome(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(DEMO_WELCOME_SEEN_KEY) === "1";
}

export function markDemoWelcomeSeen(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(DEMO_WELCOME_SEEN_KEY, "1");
}
