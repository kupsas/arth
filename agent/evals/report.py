"""
Markdown reports: single-run summary, human review template, multi-model comparison.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from agent.evals.runner import _RESULTS_DIR, load_eval_json


def _tier_counts(rows: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in rows:
        t = int(r.get("tier") or 0)
        bucket = out.setdefault(t, {"pass": 0, "fail": 0, "total": 0})
        bucket["total"] += 1
        if r.get("auto_scores", {}).get("overall_pass"):
            bucket["pass"] += 1
        else:
            bucket["fail"] += 1
    return out


def generate_report(json_path: Path, *, out_path: Path | None = None) -> Path:
    """
    Write a markdown report next to the JSON (or to ``out_path``).

    Includes per-question auto-score breakdown and manual scores when present.
    """
    data = load_eval_json(json_path)
    rows: list[dict[str, Any]] = data.get("questions") or []
    totals = data.get("totals") or {}
    if out_path is None:
        out_path = json_path.with_suffix(".report.md")

    lines: list[str] = []
    lines.append("# Arth agent eval report\n")
    lines.append(f"- **Run id:** `{data.get('run_id')}`\n")
    lines.append(f"- **Started (UTC):** {data.get('started_utc')}\n")
    lines.append(f"- **Model:** `{data.get('agent_model')}`\n")
    lines.append(f"- **Screening:** {'on' if data.get('screening_enabled') else 'off'}\n")
    lines.append("\n## Summary\n\n")
    lines.append(f"- Questions: **{totals.get('question_count', len(rows))}**\n")
    lines.append(f"- Auto pass: **{totals.get('auto_pass_count', 0)}** / fail: **{totals.get('auto_fail_count', 0)}**\n")
    lines.append(f"- Wall time: **{totals.get('wall_duration_s', '?')}** s\n")
    lines.append(f"- Est. LLM cost (session delta): **${totals.get('total_cost_usd', 0)}** USD\n")

    tc = _tier_counts(rows)
    if tc:
        lines.append("\n### By tier (auto overall)\n\n")
        lines.append("| Tier | Pass | Fail | Total |\n|------|------|------|-------|\n")
        for tier in sorted(tc.keys()):
            b = tc[tier]
            lines.append(f"| {tier} | {b['pass']} | {b['fail']} | {b['total']} |\n")

    lines.append("\n## Failures (auto)\n\n")
    fails = [r for r in rows if not r.get("auto_scores", {}).get("overall_pass")]
    if not fails:
        lines.append("_None — all auto checks passed._\n")
    else:
        for r in fails:
            lines.append(f"### `{r.get('id')}` (tier {r.get('tier')})\n\n")
            lines.append(f"- **Q:** {r.get('question')}\n")
            if r.get("error"):
                lines.append(f"- **Error:** `{r.get('error')}`\n")
            for c in r.get("auto_scores", {}).get("checks", []):
                if not c.get("pass"):
                    lines.append(f"- **{c.get('name')}:** {c.get('detail')}\n")
            lines.append("\n")

    lines.append("\n## Per question\n\n")
    for r in rows:
        aid = r.get("id")
        lines.append(f"### `{aid}`\n\n")
        lines.append(f"**Question:** {r.get('question')}\n\n")
        lines.append(f"- **Auto overall:** {'PASS' if r.get('auto_scores', {}).get('overall_pass') else 'FAIL'}")
        lines.append(f" | **Duration:** {r.get('duration_s')} s")
        lines.append(f" | **Cost Δ:** ${r.get('cost_usd_delta', 0)}\n")
        ms = r.get("manual_scores") or {}
        if any(ms.get(k) is not None for k in ("parameter_accuracy", "synthesis_quality", "boundary_awareness")):
            lines.append(
                f"- **Manual:** params={ms.get('parameter_accuracy')} "
                f"synth={ms.get('synthesis_quality')} boundary={ms.get('boundary_awareness')}\n"
            )
            if ms.get("notes"):
                lines.append(f"  - Notes: {ms['notes']}\n")
        tools = r.get("tools_called") or []
        if tools:
            lines.append(f"- **Tools:** {', '.join(str(t.get('name')) for t in tools)}\n")
        if r.get("screening"):
            s = r["screening"]
            lines.append(
                f"- **Screening:** allowed={s.get('allowed')} category={s.get('category')!r} "
                f"layer={s.get('layer')!r}\n"
            )
        lines.append("\n<details><summary>Assistant reply</summary>\n\n")
        lines.append(f"```\n{(r.get('response') or '').strip()}\n```\n\n</details>\n\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def generate_review_template(json_path: Path, *, out_path: Path | None = None) -> Path:
    """
    Readable markdown for sequential human review (manual_scores in JSON still canonical).
    """
    data = load_eval_json(json_path)
    rows: list[dict[str, Any]] = data.get("questions") or []
    if out_path is None:
        out_path = json_path.with_suffix(".review.md")

    lines: list[str] = []
    lines.append("# Arth agent eval — manual review worksheet\n\n")
    lines.append(
        "Fill **`manual_scores`** in the JSON file (`parameter_accuracy`, "
        "`synthesis_quality`, `boundary_awareness` as 1–5 integers, plus `notes`).\n\n"
    )
    lines.append(f"_Source JSON:_ `{json_path.name}`\n\n---\n\n")

    for r in rows:
        lines.append(f"## {r.get('id')} (tier {r.get('tier')})\n\n")
        lines.append(f"**Question:** {r.get('question')}\n\n")
        lines.append(f"**Expected (human spec):** {r.get('expected_behavior')}\n\n")
        if r.get("scoring_notes"):
            lines.append(f"_Scoring notes:_ {r.get('scoring_notes')}\n\n")
        lines.append("### Agent reply\n\n")
        lines.append(f"> {(r.get('response') or '').strip().replace(chr(10), ' ')}\n\n")
        lines.append("### Your scores (copy into JSON `manual_scores`)\n\n")
        lines.append("- parameter_accuracy (1–5): \n")
        lines.append("- synthesis_quality (1–5): \n")
        lines.append("- boundary_awareness (1–5): \n")
        lines.append("- notes: \n\n")
        lines.append("---\n\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def compare_runs(results_dir: Path | None = None, *, out_path: Path | None = None) -> Path:
    """
    Load every ``*.json`` in ``agent/evals/results/`` and write a comparison table.

    Manual-score averages are included only for files that contain filled scores.
    """
    rdir = results_dir or _RESULTS_DIR
    files = sorted(rdir.glob("*.json"))
    if out_path is None:
        out_path = rdir / "compare_runs.md"

    lines: list[str] = []
    lines.append("# Arth agent eval — multi-model / multi-run comparison\n\n")
    if not files:
        lines.append("_No ``*.json`` result files in ``agent/evals/results/`` yet._\n")
        out_path.write_text("".join(lines), encoding="utf-8")
        return out_path

    summaries: list[dict[str, Any]] = []
    for fp in files:
        try:
            data = load_eval_json(fp)
        except (OSError, json.JSONDecodeError):
            continue
        rows = data.get("questions") or []
        synth_vals: list[float] = []
        for r in rows:
            ms = r.get("manual_scores") or {}
            v = ms.get("synthesis_quality")
            if isinstance(v, (int, float)):
                synth_vals.append(float(v))
        summaries.append(
            {
                "file": fp.name,
                "run_id": data.get("run_id"),
                "model": data.get("agent_model"),
                "n": len(rows),
                "auto_pass": (data.get("totals") or {}).get("auto_pass_count", 0),
                "cost_usd": (data.get("totals") or {}).get("total_cost_usd", 0),
                "wall_s": (data.get("totals") or {}).get("wall_duration_s", 0),
                "synth_avg": statistics.mean(synth_vals) if synth_vals else None,
            }
        )

    lines.append("## Run overview\n\n")
    lines.append(
        "| File | Model | Q | Auto pass | Cost USD | Wall s | Avg synth (manual) |\n"
        "|------|-------|---|-----------|----------|--------|---------------------|\n"
    )
    for s in summaries:
        sa = f"{s['synth_avg']:.2f}" if s["synth_avg"] is not None else "—"
        lines.append(
            f"| `{s['file']}` | `{s['model']}` | {s['n']} | {s['auto_pass']} | "
            f"{s['cost_usd']} | {s['wall_s']} | {sa} |\n"
        )

    # Per-question tool-name disagreement (only when exactly two files with same question ids)
    lines.append("\n## Tool selection diffs (first 2 runs only if same question count)\n\n")
    if len(files) >= 2:
        a = load_eval_json(files[0])
        b = load_eval_json(files[1])
        qa = {r["id"]: r for r in (a.get("questions") or [])}
        qb = {r["id"]: r for r in (b.get("questions") or [])}
        common = sorted(set(qa.keys()) & set(qb.keys()))
        diffs = 0
        for qid in common:
            ta = [t.get("name") for t in (qa[qid].get("tools_called") or [])]
            tb = [t.get("name") for t in (qb[qid].get("tools_called") or [])]
            if ta != tb:
                diffs += 1
                lines.append(f"- **`{qid}`:** {files[0].name}: `{ta}` vs {files[1].name}: `{tb}`\n")
        if diffs == 0:
            lines.append("_No tool-name list differences between the first two JSON files._\n")
    else:
        lines.append("_Need at least two result JSON files to diff._\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
