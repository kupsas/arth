"""
Derive description/ref substrings that appear in every example transaction.

Used for reminders where counterparty alone is ambiguous (e.g. two HDFC cards).
Returns one or more anchors; matching uses ANY anchor (OR) against raw_description
and ref_number (case-insensitive).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models import Transaction

# Masked card / bill-pay style: digits, X run, trailing 4 digits.
_RE_MASK_LONG = re.compile(r"\d{5,}[Xx]{3,}\d{4}")
# Shorter mask suffix (e.g. XXXX5778).
_RE_MASK_SHORT = re.compile(r"[Xx]{4,}\d{4}")

MAX_DERIVED_ANCHORS = 5
MAX_ANCHOR_LEN = 128


def _haystack(t: "Transaction") -> str:
    parts = [t.raw_description or "", t.ref_number or ""]
    return "\n".join(parts)


def _tokens_from_text(text: str) -> set[str]:
    """Distinct candidate substrings from one transaction's text."""
    found: set[str] = set()
    for rx in (_RE_MASK_LONG, _RE_MASK_SHORT):
        for m in rx.finditer(text):
            tok = m.group(0).upper()
            if len(tok) <= MAX_ANCHOR_LEN:
                found.add(tok)
    return found


def _intersection_of_sets(sets: list[set[str]]) -> list[str]:
    if not sets:
        return []
    inter = sets[0].intersection(*sets[1:]) if len(sets) > 1 else sets[0]
    out = sorted(inter, key=len, reverse=True)
    return out[:MAX_DERIVED_ANCHORS]


def _too_generic_substring(sub: str) -> bool:
    s = sub.strip().lower()
    if len(s) < 10:
        return True
    # Shared HDFC bill-pay boilerplate without card-specific tail.
    if s.startswith("ib billpay dr-hdfc") and not re.search(r"\d{4}\s*$", s):
        return True
    if s in ("hdfc credit card", "net banking", "billpay"):
        return True
    return False


def _longest_common_substring_all(strings: list[str]) -> str | None:
    """Longest substring present in every string (case-insensitive), min length 10."""
    if len(strings) < 2:
        return None
    lowered = [s.lower() for s in strings if s]
    if len(lowered) != len(strings):
        return None
    shortest = min(lowered, key=len)
    n = len(shortest)
    for length in range(n, 9, -1):
        for i in range(n - length + 1):
            sub = shortest[i : i + length]
            if all(sub in s for s in lowered):
                if not _too_generic_substring(sub):
                    # Return slice from original casing using first string
                    idx = strings[0].lower().find(sub)
                    if idx >= 0:
                        return strings[0][idx : idx + length]
                    return sub
    return None


def derive_description_anchors(example_transactions: list["Transaction"]) -> list[str]:
    """
    Produce anchor strings common to all examples (typically masked card tokens).

    Strategy:
      1) Intersection of regex tokens (masked-number patterns) across all rows.
      2) Else longest common substring across haystacks (raw_description + ref),
         rejecting overly generic prefixes.
    """
    if not example_transactions:
        return []

    haystacks = [_haystack(t) for t in example_transactions]
    if any(not h.strip() for h in haystacks):
        return []

    per_row_tokens = [_tokens_from_text(h) for h in haystacks]
    if all(per_row_tokens):
        merged = _intersection_of_sets(per_row_tokens)
        if merged:
            return merged

    lcs = _longest_common_substring_all(haystacks)
    if lcs and len(lcs.strip()) >= 10 and len(lcs) <= MAX_ANCHOR_LEN:
        if not _too_generic_substring(lcs):
            return [lcs.strip()]

    return []


def decode_description_match_anchors(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for x in data:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out[:MAX_DERIVED_ANCHORS]


def encode_description_match_anchors(anchors: list[str] | None) -> str | None:
    if not anchors:
        return None
    return json.dumps(anchors[:MAX_DERIVED_ANCHORS])
