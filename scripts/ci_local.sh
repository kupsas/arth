#!/usr/bin/env bash
# Run the same checks as .github/workflows/ci.yml (Python: pip-audit, ruff, mypy,
# prompt YAML, pytest+cov; optional dashboard block if npm is available).
# Use from repo root: ./scripts/ci_local.sh
#
# If AUTH_* are unset, we set values that match TestAuth in tests/test_db_and_api.py
# so login tests pass without a .env. Your real .env is still used if you already
# exported AUTH_PASSWORD etc. in the shell (we only default empty vars).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade pip

export AUTH_USERNAME="${AUTH_USERNAME:-sashank}"
export AUTH_PASSWORD="${AUTH_PASSWORD:-arth2026}"
export AUTH_SECRET_KEY="${AUTH_SECRET_KEY:-local-ci-not-for-production-secret-key}"
export OPENAI_API_KEY_FOR_CLASSIFIER="${OPENAI_API_KEY_FOR_CLASSIFIER:-dummy}"
export ANTHROPIC_API_KEY_FOR_CLASSIFIER="${ANTHROPIC_API_KEY_FOR_CLASSIFIER:-dummy}"
export GOOGLE_API_KEY_FOR_CLASSIFIER="${GOOGLE_API_KEY_FOR_CLASSIFIER:-dummy}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-dummy}"
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-dummy}"
export APP_ENV="${APP_ENV:-test}"

echo "==> pip-audit (requires: pip install pip-audit)"
python3 -m pip install -q pip-audit
python3 -m pip_audit

echo "==> ruff"
python3 -m ruff check pipeline/ api/ scraper/ agent/ tests/

echo "==> mypy"
python3 -m mypy pipeline/ api/ scraper/ agent/

echo "==> validate prompts/*.yaml"
python3 -c "
import pathlib
import yaml
for p in sorted(pathlib.Path('prompts').rglob('*.yaml')):
    yaml.safe_load(p.read_text(encoding='utf-8'))
print('OK:', len(list(pathlib.Path('prompts').rglob('*.yaml'))), 'yaml files')
"

echo "==> pytest (coverage)"
python3 -m pytest tests/ \
  -m "not slow" \
  --cov=pipeline \
  --cov=api \
  --cov=agent \
  --cov-report=term-missing \
  --cov-fail-under=35 \
  -q

if command -v npm >/dev/null 2>&1; then
  echo "==> dashboard (npm ci, audit, lint, build)"
  (
    cd "$ROOT/dashboard"
    npm ci
    npm audit --audit-level=high
    npm run lint
    NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 NEXT_PUBLIC_WS_URL=ws://127.0.0.1:8000 npm run build
  )
else
  echo "==> dashboard (skipped: npm not in PATH)"
fi

echo "==> OK"
