"""
Tool registry and execution — OpenAI-compatible function schemas for LiteLLM.

Each tool is an async function registered with ``@tool``. The first parameter
must be named ``client`` (``httpx.AsyncClient``) and is **not** exposed to the LLM schema.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Union, get_args, get_origin

from httpx import AsyncClient

logger = logging.getLogger(__name__)

_TOOL_REGISTRY: dict[str, "Tool"] = {}


def _is_optional(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_optional) for ``T | None`` / ``Optional[T]``."""
    origin = get_origin(annotation)
    args = get_args(annotation) if origin is not None else ()
    if origin in (Union, UnionType):
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return (non_none[0], True)
    if origin is not None:
        if origin is list:
            return (annotation, False)
        if origin is dict:
            return (annotation, False)
    return (annotation, False)


def _json_type_for(py_type: Any) -> dict[str, Any]:
    if py_type in (str, "str"):
        return {"type": "string"}
    if py_type in (int, "int"):
        return {"type": "integer"}
    if py_type in (float, "float"):
        return {"type": "number"}
    if py_type in (bool, "bool"):
        return {"type": "boolean"}
    origin = get_origin(py_type)
    if origin is list:
        args = get_args(py_type)
        inner = args[0] if args else Any
        return {"type": "array", "items": _json_type_for(inner)}
    # Default: string (safest for LLM-filled params)
    return {"type": "string"}


def _build_parameters_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, p in sig.parameters.items():
        if pname == "client":
            continue
        ann = p.annotation if p.annotation is not inspect.Parameter.empty else str
        inner, opt = _is_optional(ann)
        props[pname] = _json_type_for(inner)
        if p.default is inspect.Parameter.empty and not opt:
            required.append(pname)
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


@dataclass
class Tool:
    """One callable tool exposed to the LLM."""

    name: str
    description: str
    fn: Callable[..., Awaitable[Any]]
    parameters_schema: dict[str, Any] = field(default_factory=dict)

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    async def execute(self, client: AsyncClient, arguments_json: str) -> dict[str, Any]:
        try:
            raw = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as e:
            return {
                "status": "error",
                "error": "invalid_tool_arguments",
                "detail": str(e),
            }
        if not isinstance(raw, dict):
            return {"status": "error", "error": "tool_arguments_must_be_object"}
        try:
            sig = inspect.signature(self.fn)
            bound = sig.bind(client=client, **raw)
            bound.apply_defaults()
            out = await self.fn(*bound.args, **bound.kwargs)
            if isinstance(out, dict) and out.get("status") in ("success", "error"):
                return out
            return {"status": "success", "data": out}
        except TypeError as e:
            logger.warning("Tool %s bad args: %s", self.name, e)
            return {
                "status": "error",
                "error": "bad_arguments",
                "detail": str(e),
            }
        except Exception as e:
            logger.exception("Tool %s failed", self.name)
            return {
                "status": "error",
                "error": "tool_execution_failed",
                "detail": str(e),
            }


def tool(name: str, description: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator — registers ``fn`` under ``name`` for the agent."""

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        schema = _build_parameters_schema(fn)
        _TOOL_REGISTRY[name] = Tool(name=name, description=description, fn=fn, parameters_schema=schema)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)

        return wrapper

    return decorator


def get_all_tools() -> list[Tool]:
    return list(_TOOL_REGISTRY.values())


def get_tool(name: str) -> Tool | None:
    return _TOOL_REGISTRY.get(name)
