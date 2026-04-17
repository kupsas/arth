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
    """Return True if an email-sourced row can skip the human review queue.

    Controlled by ``ARTH_EMAIL_AUTO_REVIEW`` (default ``1``): when disabled, never
    auto-marks. When enabled, ``HIGH`` confidence rows are auto-reviewed; set
    ``ARTH_EMAIL_AUTO_REVIEW_INCLUDE_MEDIUM=1`` to also auto-review ``MEDIUM``.

    For manual QA of the review UI, ``ARTH_REVIEW_QUEUE_INCLUDE_HIGH=1`` keeps
    ``HIGH`` rows in the queue (``is_reviewed=False``) instead of auto-approving them.
    Remove or unset that variable when you only want ``MEDIUM`` and ``LOW`` in the queue.
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
