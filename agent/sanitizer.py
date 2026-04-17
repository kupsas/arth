"""
Central PII / injection-pattern sanitizer for anything entering the LLM context.

Layer 1: tools should already return minimal fields via ``format_for_agent``.
Layer 2: this module scrubs common PII patterns and sensitive key names recursively.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Keys whose values are always stripped (case-insensitive match on lowercased key).
_SENSITIVE_KEY_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "token",
        "pan",
        "aadhaar",
        "aadhar",
        "ssn",
        "email",
        "phone",
        "mobile",
        "dob",
        "date_of_birth",
        "address",
        "account_number",
        "bank_account",
        "iban",
        "ifsc",
        "folio",
        "demat",
        "dp_id",
        "client_id",
    }
)

# Remove obvious user_id from agent-facing payloads (not PII per se, but unnecessary).
_DROP_KEYS: frozenset[str] = frozenset({"user_id"})

_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_AADHAAR_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
)
_PHONE_RE = re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")
_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
)


def _scrub_string(s: str) -> str:
    out = s
    out = _PAN_RE.sub("[REDACTED]", out)
    out = _AADHAAR_RE.sub("[REDACTED]", out)
    out = _EMAIL_RE.sub("[REDACTED]", out)
    out = _PHONE_RE.sub("[REDACTED]", out)
    low = out.lower()
    for phrase in _INJECTION_PHRASES:
        if phrase in low:
            out = re.sub(re.escape(phrase), "[removed]", out, flags=re.IGNORECASE)
            low = out.lower()
    return out


def _key_is_sensitive(key: str) -> bool:
    lk = key.lower()
    if lk in _DROP_KEYS:
        return True
    return any(part in lk for part in _SENSITIVE_KEY_SUBSTRINGS)


def sanitize(data: Any) -> Any:
    """Deep-copy and recursively redact PII-like content."""
    if data is None:
        return None
    if isinstance(data, str):
        return _scrub_string(data)
    if isinstance(data, (int, float, bool)):
        return data
    if isinstance(data, list):
        return [sanitize(x) for x in data]
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                k = str(k)
            if _key_is_sensitive(k):
                out[k] = "[REDACTED]"
            else:
                out[k] = sanitize(v)
        return out
    # Fallback: stringify unknown types safely
    return _scrub_string(str(data))


def sanitize_jsonable(data: Any) -> Any:
    """Public alias — same as ``sanitize`` (explicit name for call sites)."""
    return sanitize(copy.deepcopy(data))
