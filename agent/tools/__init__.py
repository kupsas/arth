"""
Tool package — importing submodules registers tools on the registry.
"""

from __future__ import annotations

# Side-effect imports register @tool definitions
from agent.tools import goals as _goals  # noqa: F401
from agent.tools import portfolio as _portfolio  # noqa: F401
from agent.tools import simulation as _simulation  # noqa: F401
from agent.tools import spending as _spending  # noqa: F401
from agent.tools import utility as _utility  # noqa: F401
from agent.tools.base import Tool, get_all_tools, get_tool, tool

__all__ = ["Tool", "get_all_tools", "get_tool", "tool"]
