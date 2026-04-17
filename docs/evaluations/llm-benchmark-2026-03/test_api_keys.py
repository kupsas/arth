"""
Quick smoke test: verify that all three provider API keys work
and that every model name resolves correctly.

Usage:
    python3 -m pipeline.test_api_keys
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# Classification keys (same resolution order as ``pipeline/config.py``).
OPENAI_API_KEY = (
    os.getenv("OPENAI_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
)
ANTHROPIC_API_KEY = (
    os.getenv("ANTHROPIC_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("ANTHROPIC_API_KEY", "").strip()
)
GOOGLE_API_KEY = (
    os.getenv("GOOGLE_API_KEY_FOR_CLASSIFIER", "").strip()
    or os.getenv("GOOGLE_API_KEY", "").strip()
)

PROMPT = "Reply with exactly one word: OK"

MODELS = {
    "anthropic": [
        "claude-haiku-4-5",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
    ],
    "openai": [
        "gpt-5-mini-2025-08-07",
        "gpt-5-nano-2025-08-07",
    ],
    "google": [
        "gemini-3.1-flash-lite-preview",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
}


def test_openai(model: str) -> tuple[bool, str]:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a test assistant."},
            {"role": "user", "content": PROMPT},
        ],
        max_completion_tokens=10,
    )
    text = resp.choices[0].message.content or ""
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0
    return True, f"'{text.strip()}' (in={tokens_in}, out={tokens_out})"


def test_anthropic(model: str) -> tuple[bool, str]:
    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=model,
        max_tokens=10,
        system="You are a test assistant.",
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.0,
    )
    text = resp.content[0].text
    tokens_in = resp.usage.input_tokens
    tokens_out = resp.usage.output_tokens
    return True, f"'{text.strip()}' (in={tokens_in}, out={tokens_out})"


def test_google(model: str) -> tuple[bool, str]:
    from google import genai
    client = genai.Client(api_key=GOOGLE_API_KEY)
    resp = client.models.generate_content(
        model=model,
        contents=PROMPT,
        config=genai.types.GenerateContentConfig(
            system_instruction="You are a test assistant.",
            temperature=0.0,
            max_output_tokens=10,
        ),
    )
    text = resp.text or ""
    tokens_in = resp.usage_metadata.prompt_token_count if resp.usage_metadata else 0
    tokens_out = resp.usage_metadata.candidates_token_count if resp.usage_metadata else 0
    return True, f"'{text.strip()}' (in={tokens_in}, out={tokens_out})"


DISPATCHERS = {
    "openai": test_openai,
    "anthropic": test_anthropic,
    "google": test_google,
}


def main() -> None:
    print("=" * 70)
    print("API KEY & MODEL SMOKE TEST")
    print("=" * 70)

    for name, key in [
        ("OPENAI_API_KEY_FOR_CLASSIFIER or OPENAI_API_KEY", OPENAI_API_KEY),
        ("ANTHROPIC_API_KEY_FOR_CLASSIFIER or ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("GOOGLE_API_KEY_FOR_CLASSIFIER or GOOGLE_API_KEY", GOOGLE_API_KEY),
    ]:
        status = "SET" if key and not key.startswith("sk-...") else "MISSING"
        print(f"  {name}: {status}")
    print()

    passed = 0
    failed = 0

    for provider, models in MODELS.items():
        print(f"--- {provider.upper()} ---")
        for model in models:
            t0 = time.time()
            try:
                ok, detail = DISPATCHERS[provider](model)
                elapsed = time.time() - t0
                print(f"  [PASS] {model:40s} {elapsed:.1f}s  {detail}")
                passed += 1
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [FAIL] {model:40s} {elapsed:.1f}s  {e}")
                failed += 1
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    print("=" * 70)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
