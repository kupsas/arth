#!/usr/bin/env python3
"""
Run security probes against the **real** screening stack (Layer 1 moderation + Layer 2 LLM).

This mirrors what happens when you paste the same strings into ``python -m agent.cli``,
except it only runs :func:`agent.security.screening.screen_message` — no tools, no ReAct loop.

**Caveats (please read):**
  - Costs real API tokens (screening model + optional OpenAI moderation).
  - LLMs are non-deterministic: an occasional mismatch is not always a product bug.
  - Layer 1 (OpenAI moderation) can block before Layer 2; a "harmful" there is not wrong.
  - Base64 payloads may be classified as ``injection`` or ``off_topic`` depending on the model;
    we only assert ``allowed=False`` for those rows.

**Throttle:** By default waits ``--delay`` seconds between each screening call so we do not
hammer Gemini (or your configured ``SCREENING_MODEL``).

Usage (from repo root)::

    python3 scripts/run_security_probes_live.py
    python3 scripts/run_security_probes_live.py --delay 5.0
    python3 scripts/run_security_probes_live.py --skip-sanitizer
    python3 scripts/run_security_probes_live.py --skip-screening

Requires ``.env`` with ``GOOGLE_API_KEY_FOR_SINGLE_AGENT`` (or whichever provider your
``SCREENING_MODEL`` uses) and optionally ``OPENAI_API_KEY_FOR_SINGLE_AGENT`` for moderation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Repo root on sys.path (script may be invoked as ``python3 scripts/...``).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

def _run_sanitizer_section() -> tuple[int, int]:
    from agent.sanitizer import _scrub_string, sanitize_jsonable
    from agent.security.output_sanitizer import wrap_tool_output
    from agent.security.probe_dataset import SANITIZER_ATTACK_CASES, SANITIZER_FP_CASES

    ok, bad = 0, 0
    print("\n=== Sanitizer (tool-output path, no LLM) ===\n")
    for test_id, payload, must_be_gone in SANITIZER_ATTACK_CASES:
        wrapped = {"narration": payload, "amount": 1000}
        out = sanitize_jsonable(wrapped)
        narration = str(out["narration"])
        if must_be_gone.lower() in narration.lower():
            print(f"FAIL {test_id} — {must_be_gone!r} still present")
            bad += 1
        else:
            ok += 1
    for test_id, payload, must_survive in SANITIZER_FP_CASES:
        out = _scrub_string(payload)
        if must_survive not in out:
            print(f"FAIL {test_id} — legitimate {must_survive!r} was scrubbed")
            bad += 1
        else:
            ok += 1
    # Quick wrapper sanity
    w = wrap_tool_output("t", {"x": 1})
    if "<tool_result" in w and "</tool_result>" in w:
        ok += 1
    else:
        print("FAIL wrap_tool_output — unexpected format")
        bad += 1
    print(f"Sanitizer summary: {ok} passed, {bad} failed\n")
    return ok, bad


async def _run_screening_section(delay_s: float, relaxed_base64: bool) -> tuple[int, int]:
    from agent import config as cfg
    from agent.security.probe_dataset import LIVE_SCREEN_CASES
    from agent.security.screening import screen_message

    ok, bad = 0, 0
    print("=== Screening (real moderation + real classifier LLM) ===\n")
    if not cfg.SCREENING_ENABLED:
        print("SCREENING_ENABLED is false — nothing to run.\n")
        return 0, 0

    for i, (test_id, message, exp_allowed, exp_cat) in enumerate(LIVE_SCREEN_CASES):
        if i > 0 and delay_s > 0:
            await asyncio.sleep(delay_s)
        try:
            result = await screen_message(message, cost_tracker=None)
        except Exception as e:
            print(f"ERROR {test_id} — {e!r}")
            bad += 1
            continue

        got_allowed = result.allowed
        got_cat = result.category

        # Base64 row: model may label injection or off_topic; only require block.
        if relaxed_base64 and test_id == "live_block__base64_injection":
            if got_allowed:
                print(
                    f"FAIL {test_id} — expected blocked, got ALLOW "
                    f"(layer={result.layer!r}, {result.latency_ms} ms)"
                )
                bad += 1
            else:
                print(
                    f"PASS {test_id} — blocked as {got_cat!r} "
                    f"(layer={result.layer!r}, {result.latency_ms} ms)"
                )
                ok += 1
            continue

        if got_allowed != exp_allowed or got_cat != exp_cat:
            print(
                f"MISMATCH {test_id}\n"
                f"  message:   {message[:120]!r}{'…' if len(message) > 120 else ''}\n"
                f"  expected:  allowed={exp_allowed} category={exp_cat!r}\n"
                f"  actual:    allowed={got_allowed} category={got_cat!r} "
                f"layer={result.layer!r} {result.latency_ms} ms"
            )
            bad += 1
        else:
            print(
                f"PASS {test_id} — allowed={got_allowed} category={got_cat!r} "
                f"layer={result.layer!r} {result.latency_ms} ms"
            )
            ok += 1

    print(f"\nScreening summary: {ok} matched expectation, {bad} mismatched/errors\n")
    return ok, bad


def main() -> int:
    os.chdir(_REPO_ROOT)
    # Ensure repo-root ``.env`` is loaded before reading config / API keys.
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delay",
        type=float,
        default=2.5,
        metavar="SEC",
        help="Seconds to sleep between screening LLM calls (default: 2.5)",
    )
    parser.add_argument(
        "--skip-sanitizer",
        action="store_true",
        help="Only run the screening section",
    )
    parser.add_argument(
        "--skip-screening",
        action="store_true",
        help="Only run the sanitizer section",
    )
    parser.add_argument(
        "--strict-base64",
        action="store_true",
        help="Require live_block__base64_injection to be category=injection (default: any block)",
    )
    args = parser.parse_args()

    print(__doc__.split("Usage")[0].strip())
    print(f"\nUsing SCREENING_MODEL={os.getenv('SCREENING_MODEL', '(config default)')}")
    print(f"Throttle delay between screening calls: {args.delay} s\n")

    total_bad = 0
    if not args.skip_sanitizer:
        _, b = _run_sanitizer_section()
        total_bad += b
    if not args.skip_screening:
        relaxed_b64 = not args.strict_base64
        _, b = asyncio.run(_run_screening_section(args.delay, relaxed_b64))
        total_bad += b

    if total_bad:
        print(f"Done with {total_bad} failure(s) — review mismatches above.\n")
        return 1
    print("Done — all checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
