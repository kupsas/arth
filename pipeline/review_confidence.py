"""
Heuristic review confidence for email-sourced transactions (DESKTOP_PREREQS item 5).

Uses :class:`pipeline.models.ClassificationSource` — rules-only rows are treated as
higher confidence than LLM-touched rows.
"""

from __future__ import annotations

import os

from pipeline.models import CanonicalTransaction, ClassificationSource

# HIGH | MEDIUM | LOW
ReviewConfidence = str


def compute_review_confidence(txn: CanonicalTransaction) -> ReviewConfidence:
    """Assign a coarse confidence tier for whether a human must review this row."""
    src = txn.classification_source

    if src in (ClassificationSource.RULES_GENERIC, ClassificationSource.RULES_USER):
        return "HIGH"

    if src == ClassificationSource.LLM:
        return "LOW"

    # Partially filled or unknown — treat as medium risk.
    return "MEDIUM"


def should_auto_review_email(confidence: ReviewConfidence) -> bool:
    """Legacy helper for env-driven HIGH→auto-review experiments.

    **Note:** :func:`pipeline.db_writer.write_to_db` no longer calls this for Gmail inserts —
    live scraper rows always stay on the Review queue (``is_reviewed=False``); historical
    sweeps pass ``email_presumes_reviewed=True`` instead. Kept for unit tests and tooling.
    """
    raw = (os.getenv("ARTH_EMAIL_AUTO_REVIEW") or "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False

    show_high = (os.getenv("ARTH_REVIEW_QUEUE_INCLUDE_HIGH") or "").strip().lower()
    if confidence == "HIGH" and show_high in ("1", "true", "yes", "on"):
        return False

    if confidence == "HIGH":
        return True

    mid = (os.getenv("ARTH_EMAIL_AUTO_REVIEW_INCLUDE_MEDIUM") or "").strip().lower()
    if mid in ("1", "true", "yes") and confidence == "MEDIUM":
        return True

    return False
