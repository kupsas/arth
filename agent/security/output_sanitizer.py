"""
Wrap tool payloads in explicit delimiters so the model treats them as data, not instructions.
"""

from __future__ import annotations

import html
import json
from typing import Any


def wrap_tool_output(tool_name: str, payload: dict[str, Any]) -> str:
    """
    Serialize ``payload`` (already PII-sanitized) and wrap in ``<tool_result>`` tags.

    The attribute value is escaped so odd tool names cannot break out of the tag.
    """
    safe_name = html.escape(str(tool_name), quote=True)
    body = json.dumps(payload, ensure_ascii=False)
    return f'<tool_result name="{safe_name}">\n{body}\n</tool_result>'
