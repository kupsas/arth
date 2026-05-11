#!/usr/bin/env python3
"""
Verify ``OPENAI_API_KEY_FOR_MODERATION`` is usable for OpenAI Moderation only.

Check 1 — Moderation: ``moderations.create`` must succeed (returns results).
Check 2 — Not chat: ``chat.completions.create`` must fail with a permission-style error
          (restricted key); if it succeeds, the key is too broad for shipping with Arth.

Run from repo root (loads root ``.env``)::

    python3 scripts/check_moderation_key.py

Exit code 0 only if both checks pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")


def _is_permission_like(exc: BaseException) -> bool:
    """True if the exception indicates the key cannot run chat completions."""
    name = type(exc).__name__
    if name in ("PermissionDeniedError", "AuthenticationError"):
        return True
    body = str(exc).lower()
    if "403" in body or "permission" in body or "not allowed" in body or "forbidden" in body:
        return True
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return True
    return False


def main() -> int:
    os.chdir(_REPO_ROOT)
    _load_env()

    key = (os.getenv("OPENAI_API_KEY_FOR_MODERATION") or "").strip()
    if not key:
        print("FAIL  OPENAI_API_KEY_FOR_MODERATION is unset or empty in .env")
        return 1

    from openai import OpenAI

    client = OpenAI(api_key=key)
    ok_mod = False
    ok_chat_blocked = True

    # --- Check 1: moderation ---
    try:
        res = client.moderations.create(
            input="Hello — how should I categorise this coffee shop expense?"
        )
        results = getattr(res, "results", None) or []
        if not results:
            print("FAIL  moderation returned no results")
        else:
            print("PASS  moderation call succeeded (results present)")
            ok_mod = True
    except Exception as e:
        print(f"FAIL  moderation call raised: {type(e).__name__}: {e}")

    # --- Check 2: chat completions should not work ---
    trivia = (
        "What's the capital of France?",
        "What is the capital of Telangana?",
    )
    for q in trivia:
        try:
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": q}],
                max_tokens=16,
            )
            print(f"FAIL  chat completion succeeded — key may not be moderation-only: {q!r}")
            ok_chat_blocked = False
        except Exception as e:
            if _is_permission_like(e):
                print(
                    f"PASS  chat completion blocked as expected ({type(e).__name__}): {q!r}"
                )
            else:
                print(
                    f"WARN  chat completion raised unexpected error ({type(e).__name__}): {e!r} — "
                    f"question: {q!r}"
                )
                # Treat unknown errors as failure for a strict gate
                ok_chat_blocked = False

    if ok_mod and ok_chat_blocked:
        print("\nAll checks passed — safe to rely on OPENAI_API_KEY_FOR_MODERATION for screening.")
        return 0
    print("\nOne or more checks failed — fix the key or .env before shipping.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
