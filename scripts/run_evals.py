#!/usr/bin/env python3
"""
Run the Arth agent eval suite (33 questions) against the local API + SQLite.

Examples (from repo root)::

    python scripts/run_evals.py --dry-run
    python scripts/run_evals.py --tier 1
    python scripts/run_evals.py --question t1_q01
    python scripts/run_evals.py --model anthropic/claude-sonnet-4-6
    python scripts/run_evals.py --no-screening
    python scripts/run_evals.py --report agent/evals/results/<file>.json
    python scripts/run_evals.py --review agent/evals/results/<file>.json
    python scripts/run_evals.py --compare

Requires ``.env`` with agent provider keys (see ``agent/config.py``) and a usable ``data/arth_main.db``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _quiet_loggers() -> None:
    import logging

    for name in (
        "pikepdf",
        "sdmx",
        "api.services.inflation_service",
        "httpx",
        "httpcore",
        "LiteLLM",
        "litellm",
        "openai",
        "anthropic",
        "google",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> int:
    os.chdir(_REPO_ROOT)
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", type=int, choices=(1, 2, 3, 4), help="Only questions in this tier")
    parser.add_argument(
        "--question",
        action="append",
        dest="questions",
        metavar="ID",
        help="Question id (repeatable), e.g. t1_q01",
    )
    parser.add_argument("--model", type=str, default=None, help="Override AGENT_MODEL for this run")
    parser.add_argument(
        "--no-screening",
        action="store_true",
        help="Skip input screening (tests ReAct + tools only)",
    )
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between questions (default 2)")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Extra per-question detail (e.g. profile load); progress counts always on unless --no-progress",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress [n/total] progress lines on stderr (for CI / logs)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Load dataset and exit (no API/LLM)")
    parser.add_argument("--report", type=Path, metavar="JSON", help="Generate markdown report from result JSON")
    parser.add_argument("--review", type=Path, metavar="JSON", help="Generate review worksheet markdown")
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Write comparison markdown for all agent/evals/results/*.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write report/review/compare output to this path (optional)",
    )
    args = parser.parse_args()

    from agent.evals.dataset import all_question_ids, load_eval_questions
    from agent.evals.report import compare_runs, generate_report, generate_review_template
    from agent.evals.runner import run_eval_suite, write_eval_json

    if args.compare:
        p = compare_runs(out_path=args.out)
        print(f"Wrote {p.resolve()}")
        return 0

    if args.report:
        p = generate_report(args.report, out_path=args.out)
        print(f"Wrote {p.resolve()}")
        return 0

    if args.review:
        p = generate_review_template(args.review, out_path=args.out)
        print(f"Wrote {p.resolve()}")
        return 0

    if args.dry_run:
        qs = load_eval_questions(tier=args.tier, question_ids=frozenset(args.questions) if args.questions else None)
        print(f"Dry run OK — {len(qs)} question(s) would run.")
        print(f"All ids in suite: {len(all_question_ids())}")
        return 0

    qset = frozenset(args.questions) if args.questions else None
    questions = load_eval_questions(tier=args.tier, question_ids=qset)
    if not questions:
        print("No questions match filters.", file=sys.stderr)
        return 2

    _quiet_loggers()

    async def _go() -> object:
        return await run_eval_suite(
            questions,
            model=args.model,
            screening=not args.no_screening,
            delay_s=args.delay,
            verbose=args.verbose,
            show_progress=not args.no_progress,
        )

    result = asyncio.run(_go())
    path = write_eval_json(result)
    print(f"Wrote {path.resolve()}")
    rp = generate_report(path)
    print(f"Wrote {rp.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
