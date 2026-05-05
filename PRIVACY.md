# Privacy — Arth and your data

**Short version:** YOUR financial story stays on YOUR machine. We don’t run a hosted vault that holds your numbers for you. Anything that leaves your laptop is something **you** switched on — mail sync, or Ask Arth / auto-categorisation talking to an AI provider **you** picked with **your** API keys.

---

## Who “we” are

Arth is software you **download and run yourself** (Docker or a normal install). Unless you write to us on GitHub or elsewhere, **we don’t get your diary, your balances, or your screenshots.** There isn’t an Arth company server sitting between you and your money.

---

## What stays on your device

Rough picture of what Arth keeps locally:

- **Your money diary** — spends, credits, categories, goals, reminders, and the bits that power **Simulate**.
- **Holdings** — what you own, marks, investment activity, loans you’ve told Arth about.
- **Sign-ins you’ve set up** — app login, optional mail connection, optional keys if you use Ask Arth or auto-categorisation (from **Settings** or the local config you copied from the template)

Sometimes Arth caches **public-ish helpers** (think price or inflation lookups) so the app feels snappy offline; that still lives next to your diary on disk.

---

## Where it lives

- **Normal install:** everything sensible sits under a `data` folder beside the code (exact filenames depend on how you configured things).
- **Docker:** the bundled setup keeps your diary in a **named disk pocket** (`arth_data`) that stays on **your** computer even when containers restart — still not on our machines.

**Backups:** If you copy your diary file to iCloud, Drive, or a USB stick, **their** rules apply. That’s your call.

---

## Gmail (optional)

If you connect Gmail:

- You sign in with Google’s usual **“allow this app”** flow; Arth keeps a small token so you aren’t nagged every hour.
- Reading is aimed at **bank- and broker-style mail** the app knows how to understand — not rifling through your inbox for ads. Details: [scraper/README.md](scraper/README.md).
- **Revoke anytime** from your Google Account → Security → Third-party access.

We don’t run Gmail; Google’s rules still apply to mail on their side.

---

## Ask Arth and auto-categorisation (optional)

If you **don’t** turn these on, Arth never needs to phone an external AI.

If you **do**:

- **Auto-categorisation** sends **small bundles per transaction** — things like the description your bank wrote, amount, date, in/out — so the model can suggest a category or merchant. Bank text can still accidentally include awkward fragments; that’s why this stays **optional**.
- **Ask Arth** sends **your question** and whatever **tiny excerpts** the assistant needs to answer (think: selected rows, not “here’s my entire life.csv”).

**The honest bit:** Arth runs on **your** PC or Docker on **your** desk. So “local” means **no Arth-owned cloud** — not “magically invisible to OpenAI / Anthropic / Google” if **you** plug those in. You’re choosing to share **snippets** with **them**, not with us.

---

## Telemetry

**Arth doesn’t ship tracking, crash reporters, or sneaky stats** back to the maintainers. We’re not counting your net worth clicks.

You’ll still interact with **banks, Gmail, AI providers, and GitHub** under **their** policies when you use them.

---

## If we ever share patterns for research

Imagine a future “help improve sorting” toggle — it would be **off by default**, explained in normal words, and never a silent upload of your raw diary.

---

## License sidebar

Arth uses the **GNU AGPL v3** ([LICENSE](LICENSE)). That’s about **keeping modified versions open** when people offer Arth over the internet — not about us harvesting your data.

---

## Questions

If anything reads fishy or vague, open a GitHub discussion or issue. We’d rather sound human and precise than hide behind policy theatre.