#!/usr/bin/env bash
# Run the same checks as .github/workflows/ci.yml (ruff, mypy, pytest+cov).
# Use from repo root: ./scripts/ci_local.sh
#
# If AUTH_* are unset, we set values that match TestAuth in tests/test_db_and_api.py
# so login tests pass without a .env. Your real .env is still used if you already
# exported AUTH_PASSWORD etc. in the shell (we only default empty vars).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export AUTH_USERNAME="${AUTH_USERNAME:-sashank}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-arth2026}"
export AUTH_SECRET_KEY="${AUTH_SECRET_KEY:-local-ci-not-for-production-secret-key}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-dummy}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-dummy}"
export APP_ENV="${APP_ENV:-test}"

echo "==> ruff"
python3 -m ruff check pipeline/ api/ scraper/ tests/

echo "==> mypy"
python3 -m mypy pipeline/ api/ scraper/

echo "==> pytest (coverage)"
python3 -m pytest tests/ \
  -m "not slow" \
  --cov=pipeline \
  --cov=api \
  --cov-report=term-missing \
  --cov-fail-under=35 \
  -q

echo "==> OK"
