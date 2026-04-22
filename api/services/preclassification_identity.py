"""
Onboarding pre-classification helpers (Track 2 Phase 3a).

Bank narrations often contain the account holder as ``LASTNAME FIRSTNAMES`` or
``FIRSTNAMES LASTNAME``. We auto-build ``self_aliases`` as uppercase substrings
that are safe for :mod:`pipeline.rules_classifier` matching.

We **never** add a bare single-token surname on its own — it would collide with
family members who share that surname (see product plan).
"""

from __future__ import annotations


def _squash_ws(s: str) -> str:
    """Collapse internal whitespace — user input is messy in real life."""
    return " ".join(s.split()).strip()


def build_self_aliases_from_names(
    first_name: str,
    last_name: str,
    *,
    extra_aliases: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Build ``(self_name, self_aliases)`` from onboarding first/last fields.

    Args:
        first_name: Given / first name(s), e.g. ``"Sai Sashank"``.
        last_name: Family / surname token(s), e.g. ``"Kuppa"``.
        extra_aliases: Optional nicknames or bank spellings the user adds manually.

    Returns:
        ``self_name`` is a display label (title-ish spacing). ``self_aliases`` are
        unique uppercase strings used as substring probes against narrations.

    Notes:
        - If only ``first_name`` is provided, we still add that uppercase form.
        - Bare ``last_name`` alone is **not** added when it is a single word.
        - Multi-word surnames (e.g. ``"Kuppa Reddy"``) are still omitted as a
          standalone alias to stay conservative; combined permutations with
          ``first_name`` are included when both sides are non-empty.
    """
    first = _squash_ws(first_name)
    last = _squash_ws(last_name)

    display = f"{first} {last}".strip()
    if not display:
        return "", []

    aliases: list[str] = []
    f_u = first.upper()
    l_u = last.upper()

    if f_u and l_u:
        # Typical bank formats: "KUPPA SAI SASHANK" and "SAI SASHANK KUPPA"
        aliases.append(f"{l_u} {f_u}")
        aliases.append(f"{f_u} {l_u}")
    if f_u:
        # UPI often drops the surname — first-name run is still useful.
        aliases.append(f_u)
    # Deliberately skip l_u alone (single or multi token) to avoid broad matches.

    if extra_aliases:
        for raw in extra_aliases:
            a = _squash_ws(raw).upper()
            if a:
                aliases.append(a)

    # Stable de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for a in aliases:
        if a not in seen:
            seen.add(a)
            uniq.append(a)

    return display, uniq
