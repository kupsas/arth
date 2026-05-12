<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into the Arth dashboard. Here is a summary of what was set up:

**Initialization** — `instrumentation-client.ts` was created at the project root to initialize PostHog using the Next.js 15.3+ recommended pattern (no provider wrapper needed). The reverse proxy rewrites were added to `next.config.ts` so all PostHog traffic routes through `/ingest/*`, avoiding ad-blockers and improving reliability. Environment variables (`NEXT_PUBLIC_POSTHOG_PROJECT_TOKEN`, `NEXT_PUBLIC_POSTHOG_HOST`) were written to `.env.local`.

**User identification** — A `PostHogIdentity` component was added to `src/components/providers.tsx`. It calls `useAuthMe()` on every session and runs `posthog.identify(username)` once the session resolves, correlating all events to the authenticated user.

**Event tracking** — 12 events were instrumented across 8 files covering the core user journeys: AI chat, goal management, onboarding, settings configuration, and transaction filtering.

| Event | Description | File |
|-------|-------------|------|
| `chat_message_sent` | User sends a message to the Arth agent | `src/hooks/use-chat.ts` |
| `agent_keys_saved` | User saves API keys to unlock the chat | `src/app/chat/page.tsx` |
| `goal_created` | User creates a new financial goal | `src/hooks/use-goals.ts` |
| `goal_updated` | User edits an existing financial goal | `src/hooks/use-goals.ts` |
| `goal_deleted` | User deletes a financial goal | `src/hooks/use-goals.ts` |
| `goal_priority_reordered` | User drags goals to change funding priority | `src/components/simulation/goal-explorer.tsx` |
| `hypothetical_goal_added` | User adds a hypothetical goal in the simulator | `src/components/simulation/goal-explorer.tsx` |
| `agent_model_settings_saved` | User saves LLM model settings in Settings | `src/components/settings/agent-chat-llm-settings.tsx` |
| `agent_key_removed` | User removes a stored provider API key | `src/components/settings/agent-chat-llm-settings.tsx` |
| `onboarding_completed` | User completes the onboarding wizard | `src/hooks/use-onboarding.ts` |
| `email_import_resumed` | User resumes a paused email import | `src/components/onboarding/step-email-import.tsx` |
| `transaction_category_filter_applied` | User filters transactions by spending category | `src/components/transactions/transaction-filters.tsx` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- [Analytics basics dashboard](/dashboard/1573634)
- [Chat Messages Sent](/insights/g10vAG87) — daily chat activity trend
- [Onboarding Completions](/insights/Z6GNZCBh) — unique users who finished onboarding (bold number)
- [Goal Lifecycle](/insights/FEpyknd3) — goal created / updated / deleted over time
- [Agent Setup → First Message Funnel](/insights/g1k3cUKZ) — activation funnel from saving keys to first message
- [Top Transaction Categories Filtered](/insights/Kvm8ujdV) — which spending categories users explore most

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>
