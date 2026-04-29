"""
Gmail auto-discovery for onboarding (Track 2 Phase 2a).

Scans the mailbox for **configured bank senders** using cheap Gmail ``list``
queries (small ``maxResults``). This does **not** download email bodies or run
parsers — it only answers: “Do we see mail from this sender, and roughly how much?”

Typical flow:
  1. User completes Gmail OAuth.
  2. API loads :func:`scraper.config_loader.get_bank_senders_config`.
  3. :func:`discover_sources` / :func:`discover_sources_iter` run one lightweight search per sender.
  4. Results are stored in :class:`~api.models.OnboardingState.discovery_results_json`
     for the wizard UI.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass

from scraper.config_loader import BankSendersConfig
from scraper.gmail_client import GmailClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveredSource:
    """One configured sender row after a discovery pass."""

    sender_email: str
    display_name: str
    source_type: str
    email_count_estimate: int
    earliest_email_date: datetime.date | None
    latest_email_date: datetime.date | None


def _estimate_total_messages(
    client: GmailClient,
    query: str,
    *,
    max_total: int,
) -> int:
    """Return how many messages match ``query``, capped at ``max_total`` (paginated)."""
    messages = client.search_messages(
        query,
        paginate=True,
        max_results_per_page=min(100, max_total),
        max_total=max_total,
    )
    return len(messages)


def discover_sources_iter(
    gmail_client: GmailClient,
    bank_senders_config: BankSendersConfig,
    *,
    existence_sample_size: int = 5,
    estimate_cap: int = 100,
    subject_patterns_must_match_sample: bool = False,
) -> Iterator[DiscoveredSource]:
    """Same probing logic as :func:`discover_sources`, but yield each row as it completes.

    Useful for streaming NDJSON progress to the dashboard during onboarding.
    """
    for sender_email in sorted(bank_senders_config.keys()):
        cfg = bank_senders_config[sender_email]
        display_name = str(cfg.get("display_name") or sender_email)
        source_type = str(cfg.get("source_type") or "unknown")
        patterns_raw = cfg.get("discovery_subject_patterns") or []
        compiled: list[re.Pattern[str]] = []
        for p in patterns_raw:
            try:
                compiled.append(re.compile(str(p)))
            except re.error:
                logger.warning(
                    "Invalid discovery_subject_patterns regex %r for sender %r — skipping pattern",
                    p,
                    sender_email,
                )

        query = f"from:{sender_email}"
        sample = gmail_client.search_messages(
            query,
            paginate=False,
            max_results_per_page=max(1, existence_sample_size),
        )

        if subject_patterns_must_match_sample and compiled:
            sample = [
                m
                for m in sample
                if any(rx.search(m.subject or "") for rx in compiled)
            ]

        if not sample:
            yield DiscoveredSource(
                sender_email=sender_email,
                display_name=display_name,
                source_type=source_type,
                email_count_estimate=0,
                earliest_email_date=None,
                latest_email_date=None,
            )
            continue

        earliest = min(m.received_at.date() for m in sample)
        latest = max(m.received_at.date() for m in sample)

        # Rough volume: capped paginated sweep (still far cheaper than parsing bodies).
        if subject_patterns_must_match_sample and compiled:
            full = gmail_client.search_messages(
                query,
                paginate=True,
                max_results_per_page=min(100, estimate_cap),
                max_total=estimate_cap,
            )
            estimate = sum(
                1 for m in full if any(rx.search(m.subject or "") for rx in compiled)
            )
        else:
            estimate = _estimate_total_messages(gmail_client, query, max_total=estimate_cap)

        yield DiscoveredSource(
            sender_email=sender_email,
            display_name=display_name,
            source_type=source_type,
            email_count_estimate=estimate,
            earliest_email_date=earliest,
            latest_email_date=latest,
        )


def discover_sources(
    gmail_client: GmailClient,
    bank_senders_config: BankSendersConfig,
    *,
    existence_sample_size: int = 5,
    estimate_cap: int = 100,
    subject_patterns_must_match_sample: bool = False,
) -> list[DiscoveredSource]:
    """Probe Gmail for each configured sender (fast existence + rough volume).

    For every key in ``bank_senders_config`` we run::

        from:<sender_email>

    with a **single** list page (``maxResults = existence_sample_size``) to grab
    date bounds without scanning the whole mailbox. When at least one message
    exists, we optionally run a second paginated query (capped at
    ``estimate_cap``) to approximate total mail volume from that sender.

    Args:
        gmail_client: An authenticated :class:`~scraper.gmail_client.GmailClient`.
        bank_senders_config: Per-user merged dict (same shape as ``BANK_SENDERS``).
        existence_sample_size: Page size for the initial cheap probe (1–5 recommended).
        estimate_cap: Upper bound on how many IDs we enumerate for the estimate pass.
        subject_patterns_must_match_sample: When True, an entry only counts as
            “present” if **at least one** message in the small sample matches one
            of ``discovery_subject_patterns`` (regex strings). Volume estimate then
            applies the same filter on the capped paginated list (may under-count).

    Returns:
        One :class:`DiscoveredSource` per configured sender key (sorted by email).
    """
    return list(
        discover_sources_iter(
            gmail_client,
            bank_senders_config,
            existence_sample_size=existence_sample_size,
            estimate_cap=estimate_cap,
            subject_patterns_must_match_sample=subject_patterns_must_match_sample,
        )
    )


def discovered_sources_to_json(rows: list[DiscoveredSource]) -> list[dict[str, object]]:
    """Serialize discovery rows for JSON / DB storage (ISO dates)."""
    encoded: list[dict[str, object]] = []
    for r in rows:
        encoded.append(
            {
                "sender_email": r.sender_email,
                "display_name": r.display_name,
                "source_type": r.source_type,
                "email_count_estimate": r.email_count_estimate,
                "earliest_email_date": r.earliest_email_date.isoformat()
                if r.earliest_email_date
                else None,
                "latest_email_date": r.latest_email_date.isoformat()
                if r.latest_email_date
                else None,
            }
        )
    return encoded
