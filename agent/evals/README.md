# Arth agent — evals & benchmarking

This package runs the **33-question** suite from the product plan against your **local** API + SQLite (same path as the CLI).

## Quick start

```bash
# From repo root — loads .env, needs agent API keys + DB
python3 scripts/run_evals.py --dry-run

# Full run prints `[n/33] …` progress on stderr by default; silence with:
# python3 scripts/run_evals.py --no-progress

python scripts/run_evals.py --tier 1
python scripts/run_evals.py --question t1_q01

# Benchmark one provider/model (no fallback chain for that run)
python scripts/run_evals.py --model anthropic/claude-sonnet-4-6

# Skip screening — tests only the ReAct loop + tools
python scripts/run_evals.py --no-screening --tier 4

# After a run JSON exists under agent/evals/results/
python scripts/run_evals.py --review agent/evals/results/<file>.json
python scripts/run_evals.py --report agent/evals/results/<file>.json
python scripts/run_evals.py --compare
```

## Manual scoring

1. Open the generated JSON under `agent/evals/results/`.
2. Fill `manual_scores` (`parameter_accuracy`, `synthesis_quality`, `boundary_awareness`, `notes`) with integers **1–5** where applicable.
3. Regenerate the markdown report with `--report`.

## Notes

- **No golden numeric answers** — data depends on your DB. Auto-scores focus on screening, tool names (when configured), and obvious PII patterns in the reply text.
- **`tool_match_mode`**: `exact` (same set), `contains_all` (every listed tool must appear; extras OK), `skip` (no auto tool check).
