"""
Per-user classification inputs for the rules classifier.

The rules engine stays free of hardcoded personal data: names, merchant
keywords, rent recipient, and salary platform substrings are supplied via
``UserClassificationConfig``, typically assembled from SQLite + the starter
merchant JSON shipped with the app.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pipeline.config import REPO_ROOT
from pipeline.models import CounterpartyCategory, TxnType


class MerchantRuleSource(str, Enum):
    """Where a merchant keyword rule originated."""

    STARTER_PACK = "STARTER_PACK"
    USER_DB = "USER_DB"


@dataclass
class KnownContact:
    """A person the user asked us to recognise in narrations (family, friends, …)."""

    display_name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class MerchantRule:
    """Maps a substring in a card/bank narration to a counterparty + category."""

    keyword: str
    display_name: str
    category: CounterpartyCategory
    source: MerchantRuleSource = MerchantRuleSource.STARTER_PACK


@dataclass
class CustomPattern:
    """User-defined substring that forces a specific ``TxnType`` (e.g. loan nicknames)."""

    substring: str
    set_txn_type: TxnType


STARTER_PACK_PATH: Path = REPO_ROOT / "data" / "merchant_starter_pack.json"


def load_merchant_starter_pack(path: Path | None = None) -> list[MerchantRule]:
    """Load generic India-wide merchant rules from the JSON starter pack."""
    p = path or STARTER_PACK_PATH
    if not p.is_file():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: list[MerchantRule] = []
    for row in raw:
        kw = str(row["keyword"]).strip().upper()
        name = str(row["display_name"]).strip()
        cat_val = str(row["counterparty_category"]).strip()
        cat = CounterpartyCategory(cat_val)
        out.append(
            MerchantRule(
                keyword=kw,
                display_name=name,
                category=cat,
                source=MerchantRuleSource.STARTER_PACK,
            )
        )
    return out


def merge_merchant_rule_lists(
    starter: list[MerchantRule],
    user_rules: list[MerchantRule],
) -> list[MerchantRule]:
    """User rows first so the classifier’s first-match pass prefers learned overrides."""
    user_kw = {r.keyword.upper() for r in user_rules}
    out = list(user_rules)
    for r in starter:
        if r.keyword.upper() not in user_kw:
            out.append(r)
    return out


@dataclass
class UserClassificationConfig:
    """Everything the rules classifier needs to specialise behaviour per user."""

    self_name: str = ""
    self_aliases: list[str] = field(default_factory=list)
    # Substrings that appear in bank narrations for the user’s own accounts (last-4, etc.).
    account_id_hints: list[str] = field(default_factory=list)
    family_contacts: list[KnownContact] = field(default_factory=list)
    friend_contacts: list[KnownContact] = field(default_factory=list)
    acquaintance_contacts: list[KnownContact] = field(default_factory=list)
    merchant_rules: list[MerchantRule] = field(default_factory=list)
    rent_recipient: str | None = None
    # Optional extra regex fragment (e.g. property name) OR full pattern — see _rent_description_matches.
    rent_pattern: str | None = None
    salary_indicators: list[str] = field(default_factory=lambda: ["PAYROLL"])
    custom_patterns: list[CustomPattern] = field(default_factory=list)


def default_user_classification_config() -> UserClassificationConfig:
    """Used when the DB is unavailable (e.g. tests) — starter merchants + generic salary only."""
    return UserClassificationConfig(
        merchant_rules=load_merchant_starter_pack(),
        salary_indicators=["PAYROLL"],
    )


def _contact_match_strings(contacts: list[KnownContact]) -> list[str]:
    names: list[str] = []
    for c in contacts:
        names.append(c.display_name)
        names.extend(c.aliases)
    return names


def all_contact_name_strings(cfg: UserClassificationConfig) -> list[str]:
    """Flatten family + friends + acquaintances for substring name search in bank text."""
    return (
        _contact_match_strings(cfg.family_contacts)
        + _contact_match_strings(cfg.friend_contacts)
        + _contact_match_strings(cfg.acquaintance_contacts)
    )


def rent_description_matches(desc_upper: str, cfg: UserClassificationConfig) -> bool:
    """True when narration looks like rent AND (if configured) user-specific pattern matches."""
    if "RENT" not in desc_upper:
        return False
    # Generic standing-instruction rent (same as legacy NET BANKING SI rule).
    if re.search(r"NET BANKING SI.*RENT", desc_upper):
        return True
    if cfg.rent_pattern:
        try:
            if re.search(cfg.rent_pattern, desc_upper, re.IGNORECASE):
                return True
        except re.error:
            pass
    return False
