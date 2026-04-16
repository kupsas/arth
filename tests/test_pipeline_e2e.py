"""
End-to-end pipeline regression test.

Runs the full pipeline with validation and checks that per-field accuracy
doesn't regress below known baselines. This test makes real LLM API calls
and is therefore slow and expensive.

Run explicitly with:  pytest tests/test_pipeline_e2e.py -m slow
Skip by default with: pytest tests/ -m "not slow"
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

# Known accuracy baselines (from the current pipeline at time of prompt migration).
# If accuracy drops below these, the test fails.
ACCURACY_BASELINES = {
    "txn_type": 90.0,
    # Observed 87.5% on 2026-04 with gemini-3.1-flash-lite + cache; allow small LLM drift.
    "counterparty": 87.0,
    # Observed 87.5% after prompt anonymization / cache drift (2026-04).
    "counterparty_category": 87.0,
}


@pytest.mark.slow
def test_pipeline_accuracy_no_regression() -> None:
    """Run the full pipeline with --validate and assert accuracy baselines.

    Parses the validation report output for per-field accuracy percentages
    and asserts each one is above the known baseline.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.run", "--validate", "--llm", "auto"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert result.returncode == 0, (
        f"Pipeline failed with exit code {result.returncode}:\n"
        f"stdout: {result.stdout[-500:]}\n"
        f"stderr: {result.stderr[-500:]}"
    )

    output = result.stdout

    # Parse accuracy lines like:
    #   counterparty_category     612/648 (94.4%)
    accuracy_pattern = re.compile(
        r"(\w+)\s+\d+/\d+\s+\((\d+\.\d+)%\)"
    )

    found_fields: dict[str, float] = {}
    for match in accuracy_pattern.finditer(output):
        field_name = match.group(1)
        accuracy = float(match.group(2))
        found_fields[field_name] = accuracy

    assert found_fields, (
        "Could not parse any accuracy numbers from pipeline output.\n"
        f"Output tail: {output[-1000:]}"
    )

    for field, baseline in ACCURACY_BASELINES.items():
        assert field in found_fields, (
            f"Field {field!r} not found in validation output"
        )
        actual = found_fields[field]
        assert actual >= baseline, (
            f"{field} accuracy regressed: {actual:.1f}% < {baseline:.1f}% baseline"
        )
