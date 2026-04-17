# SQLCipher evaluation (Work Stream A.5)

**Decision (March 2026): not adopted.** Arth stays on stock SQLite with a plaintext `.db` file; protect the machine and backups instead. This document records the trade study only.

**Date:** March 2026  
**Context:** Arth stores personal finance data in SQLite (`data/arth.db`). Phase A introduced Fernet encryption for a few PII columns on `holdings`; the database file itself remains unencrypted at rest.

## What SQLCipher would add

[SQLCipher](https://www.zetetic.net/sqlcipher/) is SQLite with **full-file AES-256 encryption**. Every page is encrypted; the app supplies a passphrase (or key) when opening the DB.

## Python / SQLModel / SQLAlchemy fit

- Typical stack: `sqlcipher3` (or `pysqlcipher3`) as the SQLite driver, with a connection URI like `sqlite+pysqlcipher://:passphrase@/path/to/db`.
- SQLModel and SQLAlchemy work as usual once the **engine** is created with a SQLCipher-capable driver and `connect_args` / pragmas for keying.
- **WAL mode** (used in `api/database.py`) is supported on modern SQLCipher; verify pragma order (`PRAGMA key` before `journal_mode=WAL`) on your target version.

## Performance (qualitative)

- Expect **small CPU overhead** on every read/write (symmetric crypto per page). For Arth’s scale (single user, thousands of rows, mostly indexed reads), this is unlikely to be noticeable on a laptop.
- **No separate benchmark was run** in-repo; treat “acceptable for personal use” as the bar unless you measure a hot path.

## Operations / backups

- **Backups** must copy the **encrypted** file (or use SQLCipher’s export) — plaintext `.dump` without the key is useless for restore.
- **Key management** moves from “protect `.env` + disk” to “protect passphrase + disk” — same class of problem as `FERNET_KEY`, but rotation/rekey is heavier (export/import or `sqlcipher` CLI procedures).
- **Cross-tool access** (`sqlite3` CLI, GUI browsers) **breaks** unless they support SQLCipher and the key — a real workflow cost.

## Decision: **defer**

**Rationale:**

1. **Threat model:** Arth is a **local, two-user** tool. Host-disk encryption (FileVault/APFS) plus `chmod 600` on `arth.db` and `gmail_token.json` already reduces casual exposure.
2. **Partial encryption:** Fernet already covers the most sensitive *columns* (folio / account identifiers). Full-DB encryption is incremental hardening, not a blocker for Layer 1 features.
3. **Complexity:** Driver choice, CI images, and developer ergonomics (opening the DB in generic tools) all get worse. Alembic + SQLCipher is also a later concern (Workstream B).
4. **Revisit when:** You sync `arth.db` to cloud without client-side encryption, run the API on a shared server, or compliance asks for encryption-at-rest **inside** the file.

## If you adopt later

1. Add a well-maintained driver (e.g. `sqlcipher3-binary` or vendor-specific wheel) to `requirements.txt`.
2. Replace `create_engine` in `api/database.py` with SQLCipher URI + `PRAGMA key` (from env, never committed).
3. Document backup/restore using encrypted copy or official export.
4. Extend CI to install SQLCipher libs if wheels are not universal.

No code change was made for SQLCipher in Phase A — this note records the explicit **defer** decision.
