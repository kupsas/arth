"""Deployment-wide constants shared across API, scraper, and tests."""

from __future__ import annotations

# Single local identity when auth is disabled (open-source / localhost trust model).
# All SQLite rows keyed by user_id use this string unless overridden (e.g. household maps).
DEFAULT_LOCAL_USER: str = "local"
