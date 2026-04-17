"""
Load prompt YAML from disk. Designed so a future version can swap in HTTP-fetched prompts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent import config as cfg
from agent.tools.base import Tool


def _prompts_dir() -> Path:
    return cfg.AGENT_PROMPTS_DIR


def load_yaml(name: str) -> dict[str, Any]:
    path = _prompts_dir() / name
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_system_prompt(*, user_profile: str, tools: list[Tool]) -> str:
    """Build the full system string from ``system_prompt.yaml`` + dynamic sections."""
    doc = load_yaml("system_prompt.yaml")
    lines: list[str] = []
    for key in (
        "identity",
        "domain",
        "user_profile_section",
        "response_guidelines",
        "tool_instructions",
        "security",
        "tool_summary",
    ):
        block = doc.get(key)
        if isinstance(block, str):
            lines.append(block.strip())
            lines.append("")
    text = "\n".join(lines).strip()
    tool_lines = "\n".join(f"- **{t.name}**: {t.description.strip()}" for t in tools)
    text = text.replace("{{USER_PROFILE}}", user_profile.strip() or "(no profile loaded)")
    text = text.replace("{{TOOL_SUMMARY}}", tool_lines or "(no tools)")
    return text
