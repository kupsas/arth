"""
LLM prompt templates for transaction classification.

Loads prompt content from YAML files in the ``prompts/`` directory and
exposes the same three public functions the rest of the pipeline expects:

  - ``batch_classify_prompt``    — single-pass: all fields at once
  - ``two_pass_fields_prompt``   — pass 1: txn_type + upi_type + counterparty
  - ``two_pass_category_prompt`` — pass 2: counterparty_category

Each function returns a (system_message, user_message) tuple of strings.
The YAML files hold the static content (system templates, enum values,
few-shot examples); this module handles variable interpolation and
item formatting.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# ── Locate the prompts directory relative to repo root ──────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_DIR = _REPO_ROOT / "prompts"


# ── YAML loading (runs once at import time) ─────────────────────────────────

def _load_yaml(filename: str) -> dict:
    """Load and parse a YAML file from the prompts directory."""
    path = _PROMPTS_DIR / filename
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_enums = _load_yaml("enums.yaml")
_few_shot_data = _load_yaml("few_shot_examples.yaml")
_single_pass_cfg = _load_yaml("classify_single_pass.yaml")
_two_pass_fields_cfg = _load_yaml("classify_two_pass_fields.yaml")
_two_pass_category_cfg = _load_yaml("classify_two_pass_category.yaml")


# ── Render few-shot examples into the text format the LLM expects ───────────

def _render_few_shot(examples: list[dict]) -> str:
    """Convert structured example dicts into the multi-line text block.

    Each example renders as:
        Example N — {title}:
          desc: {desc}
          direction: {direction} | amount: {amount} | channel: {channel}
          → txn_type={txn_type} | upi_type={upi_type} | counterparty=... | counterparty_category=...

    Examples without upi_type omit that field from the result line.
    """
    blocks: list[str] = []
    for ex in examples:
        result_parts = [f"txn_type={ex['txn_type']}"]
        if "upi_type" in ex:
            result_parts.append(f"upi_type={ex['upi_type']}")
        result_parts.append(f"counterparty={ex['counterparty']}")
        result_parts.append(f"counterparty_category={ex['counterparty_category']}")
        result_line = " | ".join(result_parts)

        block = (
            f"Example {ex['number']} \u2014 {ex['title']}:\n"
            f"  desc: {ex['desc']}\n"
            f"  direction: {ex['direction']} | amount: {ex['amount']} | channel: {ex['channel']}\n"
            f"  \u2192 {result_line}"
        )
        blocks.append(block)

    return "\n\n".join(blocks)


# Pre-render the shared few-shot text (used by single-pass and two-pass-fields)
_FEW_SHOT_TEXT = _render_few_shot(_few_shot_data["examples"])


# ── System template interpolation ───────────────────────────────────────────

def _render_system(template: str, *, include_few_shot: bool = True) -> str:
    """Replace placeholders in a system template with actual values.

    Uses str.replace() (not str.format()) to avoid conflicts with literal
    braces in JSON response-format instructions.
    """
    result = template
    result = result.replace("{txn_types}", _enums["txn_types"])
    result = result.replace("{upi_types}", _enums["upi_types"])
    result = result.replace("{categories}", _enums["categories"])
    result = result.replace("{spend_categories}", _enums["spend_categories"])
    if include_few_shot:
        result = result.replace("{few_shot}", _FEW_SHOT_TEXT)
    return result


# ── Strategy A: Single-pass (all fields at once) ───────────────────────────

def batch_classify_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Build a single-pass prompt that classifies a batch of transactions.

    Each item has keys: id, txn_date, desc, direction, amount, channel,
    txn_type, upi_type, ref_number, needs.

    Returns (system_message, user_message).
    """
    system = _render_system(_single_pass_cfg["system_template"])

    lines = []
    for item in items:
        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"desc":"{item["desc"]}"')
        parts.append(f'"date":"{item.get("txn_date", "")}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        if item.get("txn_type"):
            parts.append(f'"txn_type_known":"{item["txn_type"]}"')
        if item.get("upi_type"):
            parts.append(f'"upi_type_known":"{item["upi_type"]}"')
        parts.append(f'"need":[{item["needs"]}]')
        lines.append("{" + ", ".join(parts) + "}")

    user = _single_pass_cfg["user_prefix"] + "\n\n" + "\n".join(lines)

    return system, user


# ── Strategy B: Two-pass prompts ────────────────────────────────────────────

def two_pass_fields_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Pass 1 of two-pass strategy: get txn_type, upi_type, counterparty.

    Does NOT ask for counterparty_category — that comes in pass 2.
    """
    system = _render_system(_two_pass_fields_cfg["system_template"])

    lines = []
    for item in items:
        needed_fields = []
        for f in ("txn_type", "upi_type", "counterparty"):
            if f'"{f}"' in item.get("needs", ""):
                needed_fields.append(f)

        if not needed_fields:
            needed_fields = ["counterparty"]

        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"desc":"{item["desc"]}"')
        parts.append(f'"date":"{item.get("txn_date", "")}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        if item.get("txn_type"):
            parts.append(f'"txn_type_known":"{item["txn_type"]}"')
        if item.get("upi_type"):
            parts.append(f'"upi_type_known":"{item["upi_type"]}"')
        need_str = ", ".join(f'"{f}"' for f in needed_fields)
        parts.append(f'"need":[{need_str}]')
        lines.append("{" + ", ".join(parts) + "}")

    user = _two_pass_fields_cfg["user_prefix"] + "\n\n" + "\n".join(lines)

    return system, user


def two_pass_category_prompt(
    items: list[dict[str, str]],
) -> tuple[str, str]:
    """Pass 2 of two-pass strategy: get counterparty_category from txn_type + counterparty.

    Each item should have "id", "txn_type_counterparty" (e.g. "UPI_EXPENSE Spotify"),
    plus full transaction context.
    """
    system = _render_system(
        _two_pass_category_cfg["system_template"],
        include_few_shot=False,
    )

    lines = []
    for item in items:
        parts = [f'"id":"{item["id"]}"']
        parts.append(f'"txn_type_counterparty":"{item["txn_type_counterparty"]}"')
        parts.append(f'"direction":"{item["direction"]}"')
        parts.append(f'"amount":"{item["amount"]}"')
        if item.get("channel"):
            parts.append(f'"channel":"{item["channel"]}"')
        lines.append("{" + ", ".join(parts) + "}")

    user = _two_pass_category_cfg["user_prefix"] + "\n\n" + "\n".join(lines)

    return system, user
