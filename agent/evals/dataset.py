"""
Load eval questions from ``questions.yaml``.

Filtering supports tier and/or explicit question ids for quick iteration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

# questions.yaml lives next to this module
_QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"

ToolMatchMode = Literal["exact", "contains_all", "skip"]
ExpectedScreening = Literal["allow", "block"]


@dataclass(frozen=True)
class EvalQuestion:
    """
    One eval row — mirrors ``questions.yaml`` schema.

    ``tool_match_mode``:
      * ``exact`` — tool names used (set) must exactly match ``expected_tools`` (set).
      * ``contains_all`` — every name in ``expected_tools`` must appear at least once; extras OK.
      * ``skip`` — do not auto-judge tool selection (still logged for humans).
    """

    id: str
    tier: int
    question: str
    expected_tools: tuple[str, ...]
    tool_match_mode: ToolMatchMode
    expected_screening: ExpectedScreening
    expected_screening_categories: tuple[str, ...] | None
    boundary_type: str | None
    expected_behavior: str
    scoring_notes: str | None


def _as_tuple_strs(v: Any) -> tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, list):
        return tuple(str(x) for x in v)
    return (str(v),)


def _parse_question(raw: dict[str, Any]) -> EvalQuestion:
    tid = str(raw["id"])
    tier = int(raw["tier"])
    qtext = str(raw["question"])
    tools = _as_tuple_strs(raw.get("expected_tools"))
    mode = str(raw.get("tool_match_mode") or "exact").strip().lower()
    if mode not in ("exact", "contains_all", "skip"):
        raise ValueError(f"{tid}: invalid tool_match_mode {mode!r}")
    es = str(raw.get("expected_screening") or "allow").strip().lower()
    if es not in ("allow", "block"):
        raise ValueError(f"{tid}: invalid expected_screening {es!r}")
    cats = raw.get("expected_screening_categories")
    if cats is None:
        esc: tuple[str, ...] | None = None
    else:
        esc = tuple(str(c).strip().lower() for c in cats)
    boundary = raw.get("boundary_type")
    boundary_s = str(boundary).strip().lower() if boundary is not None else None
    if boundary_s == "" or boundary_s == "null":
        boundary_s = None
    return EvalQuestion(
        id=tid,
        tier=tier,
        question=qtext,
        expected_tools=tools,
        tool_match_mode=mode,  # type: ignore[arg-type]
        expected_screening=es,  # type: ignore[arg-type]
        expected_screening_categories=esc,
        boundary_type=boundary_s,
        expected_behavior=str(raw.get("expected_behavior") or ""),
        scoring_notes=(
            str(raw["scoring_notes"]) if raw.get("scoring_notes") not in (None, "") else None
        ),
    )


def load_eval_questions(
    *,
    path: Path | None = None,
    tier: int | None = None,
    question_ids: frozenset[str] | None = None,
) -> list[EvalQuestion]:
    """
    Load and optionally filter questions.

    :param path: Override YAML path (defaults to packaged ``questions.yaml``).
    :param tier: If set, only questions with this tier (1–4).
    :param question_ids: If set, only these ids (e.g. frozenset({\"t1_q01\"})).
    """
    p = path or _QUESTIONS_PATH
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    rows = doc.get("questions")
    if not isinstance(rows, list):
        raise ValueError(f"{p}: expected top-level 'questions' list")
    out: list[EvalQuestion] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        q = _parse_question(raw)
        if tier is not None and q.tier != tier:
            continue
        if question_ids is not None and q.id not in question_ids:
            continue
        out.append(q)
    return out


def all_question_ids(*, path: Path | None = None) -> frozenset[str]:
    """Return every question id in the suite (handy for CLI validation)."""
    return frozenset(q.id for q in load_eval_questions(path=path))
