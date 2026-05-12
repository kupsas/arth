"""Loose shape checks for classifier LLM API keys (UserSecrets paste validation).

Matches dashboard ``describeClassifierKeyShapeError`` — keep messages user-facing, no internal jargon.
"""

from __future__ import annotations

from typing import Literal

ClassifierProviderField = Literal["openai", "anthropic", "google"]

_GOOGLE_CLASSIFIER_KEY_LEN = 39
_LLM_KEY_MAX = 8192


def validate_classifier_key_shape(field: ClassifierProviderField, value: str) -> str | None:
    """Return an error message if ``value`` fails loose checks, else ``None``."""
    v = value.strip()
    if not v:
        return None
    if field == "google":
        if not v.startswith("AIza"):
            return (
                "That doesn’t look like a Google AI key — copy the one that starts with AIza."
            )
        if len(v) != _GOOGLE_CLASSIFIER_KEY_LEN:
            return (
                f"Google AI keys are usually {_GOOGLE_CLASSIFIER_KEY_LEN} characters. "
                "Double-check the full key."
            )
        rest = v[4:]
        if not rest or not all(c.isalnum() or c in "-_" for c in rest):
            return (
                "That doesn’t look like a complete Google AI key — letters, numbers, hyphens, "
                "or underscores only."
            )
        return None
    if field == "openai":
        if not v.startswith("sk-"):
            return "That doesn’t look like an OpenAI key — copy the one that starts with sk-."
        if len(v) < 20 or len(v) > _LLM_KEY_MAX:
            return (
                "That OpenAI key doesn’t look the right length — paste the full key from OpenAI."
            )
        tail = v[3:]
        if not tail or not all(c.isalnum() or c in "-_" for c in tail):
            return "That doesn’t look like a complete OpenAI key — use only the characters from your key."
        return None
    # anthropic
    if not v.startswith("sk-ant-"):
        return "That doesn’t look like an Anthropic key — copy the one that starts with sk-ant-."
    if len(v) < 28 or len(v) > _LLM_KEY_MAX:
        return (
            "That Anthropic key doesn’t look the right length — paste the full key from Anthropic."
        )
    tail = v[7:]
    if not tail or not all(c.isalnum() or c in "-_" for c in tail):
        return (
            "That doesn’t look like a complete Anthropic key — use only the characters from your key."
        )
    return None
