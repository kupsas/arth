# Tests

pytest test suite for the Arth pipeline, API, email scraper, portfolio, goals, and supporting services. **600+** collected tests (`pytest tests/ --collect-only` for the exact count on your branch).

---

## Running Tests

```bash
# Run all tests
pytest tests/

# Run a specific file
pytest tests/test_email_parsers.py

# Run with verbose output (see individual test names)
pytest tests/ -v

# Skip slow/expensive tests (LLM calls, etc.)
pytest tests/ -m "not slow"

# Run with coverage report
pytest tests/ --cov=. --cov-report=term-missing
```

Tests use an **in-memory SQLite database** via `conftest.py`. No `.env` required, no external services needed.

---

## Test files (high level)

| Area | Examples |
|------|-----------|
| **Email & scraper** | `test_email_parsers.py`, `test_orchestrator.py`, `test_reconciliation.py` |
| **API & DB** | `test_db_and_api.py`, route-focused tests under `tests/test_*` for holdings, goals, surplus, prices, … |
| **Pipeline** | `test_pipeline_e2e.py`, parser tests, `test_prompt_*.py` |
| **Portfolio / goals** | `test_holding_*.py`, `test_goal_*.py`, `test_returns_calculator.py`, `test_surplus_calculator.py`, … |

Run `pytest tests/ --collect-only -q` for an up-to-date file list and count. Individual files move quickly as features land — rely on **module names** (`test_<feature>.py`) rather than a frozen table.

---

## Fixtures

| Path | What it is |
|---|---|
| `tests/fixtures/email_samples/` | HTML bodies for HDFC / ICICI alert parser tests and orchestrator integration tests. The repo ships **minimal synthetic** HTML so CI always runs these tests. To refresh from your real Gmail (maintainer machine only), see below. |
| `tests/fixtures/golden_single_pass.json` | Golden snapshot of the rendered single-pass prompt |
| `tests/fixtures/golden_two_pass_fields.json` | Golden snapshot of the two-pass fields prompt |
| `tests/fixtures/golden_two_pass_category.json` | Golden snapshot of the two-pass category prompt |

### Refreshing `email_samples/` from Gmail

1. Ensure `data/gmail_credentials.json` and `data/gmail_token.json` exist (same OAuth flow as the scraper).
2. Edit [`scripts/email_parser_fixtures_manifest.yaml`](../scripts/email_parser_fixtures_manifest.yaml): set `message_id` per row (from the Gmail URL) or tighten each `query` until the newest match is the mail you want.
3. Optional: copy `data/email_fixture_redactions.example.json` to **`data/email_fixture_redactions.json`** (gitignored) and add `{ "find": "...", "replace": "..." }` pairs — longest matches are applied first.
4. Preview: `python3 scripts/sync_email_parser_fixtures.py --dry-run`
5. Write: `python3 scripts/sync_email_parser_fixtures.py` (omit `--no-redact` so redactions apply; raw downloads need human review before commit).
6. After HTML changes, update assertions if needed: `python3 scripts/sync_email_parser_fixtures.py --emit-expectations`

For ad-hoc exploration (generic samples per sender, not the pinned parser filenames), [`scripts/discover_emails.py`](../scripts/discover_emails.py) is still available.

**Regenerate golden snapshots** (needed after intentional prompt changes):
```bash
python3 tests/capture_golden_snapshots.py
```

---

## Key Patterns

### In-memory SQLite with StaticPool

All tests that touch the database use an in-memory SQLite with `StaticPool`. This is non-negotiable: without `StaticPool`, every `Session()` gets its own independent in-memory database — tables created in fixture setup don't exist when the test runs.

`conftest.py` sets this up automatically. Use the `session` fixture from there; don't create sessions manually in tests.

### Patch at the usage site

When mocking imports, patch at the site where the name is **used**, not where it's **defined**:

```python
# Correct — the route module imported the name at load time; patch its reference
patch("api.routes.scraper.trigger_now")

# Wrong — the route already holds a reference to the original; this patch is invisible
patch("scraper.scheduler.trigger_now")
```

### Disable LLM calls in tests

Tests set `LLM_MODEL = "none"` to skip real LLM API calls and run rules-only:

```python
import pipeline.config as cfg
cfg.LLM_MODEL = "none"
```

Patch the attribute on the imported module object. Don't re-import the string — you'll get a copy that doesn't affect the running code.

### Mock GmailClient

`test_orchestrator.py` uses a mock `GmailClient` that returns pre-loaded HTML fixtures from `tests/fixtures/email_samples/`. This lets the orchestrator tests run the full parse → classify → write path without real Gmail credentials or network calls.

---

## `conftest.py`

Shared fixtures available to all test files:

- `session` — in-memory SQLite session (StaticPool), auto-rollback between tests
- `client` — FastAPI `TestClient` wired to the test session (dependency override)
- Any shared transaction or pipeline run setup used across multiple test files
