"""
Gmail auto-discovery for onboarding (Track 2 Phase 2a).

Scans the mailbox for **configured bank senders** using a single ``messages.list``
call per sender (IDs only — no per-message ``get`` for metadata). This does **not**
download email bodies or run parsers — it only answers: “Do we see mail from this
sender, and roughly how many IDs on the first page?”

Typical flow:
  1. User completes Gmail OAuth.
  2. API loads :func:`scraper.config_loader.get_bank_senders_config`.
  3. :func:`discover_sources` / :func:`discover_sources_iter` run one ``list`` per sender.
  4. Results are stored in :class:`~api.models.OnboardingState.discovery_results_json`
     for the wizard UI.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator
from dataclasses import dataclass

from scraper.config_loader import BankSendersConfig
from scraper.gmail_client import GmailClient

logger = logging.getLogger(__name__)


def _is_nse_deferred_discovery_sender(sender_email: str) -> bool:
    """True for official NSE trade-mailbox addresses (``*@nse.co.in``).

    Discovery streams rows to the dashboard as each sender is probed. The UI hides the
    NSE demat bucket when **any** other broker row exists — but that comparison is only
    correct once other brokers have been scanned. Alphabetically ``ebix@nse.co.in`` ran
    before ``service@icicisecurities.com``, so users briefly saw “NSE only” with ICICI
    still queued. Probing every ``@nse.co.in`` sender **last** keeps broker rows ahead
    of NSE in the stream without changing final persisted results.
    """
    lower = sender_email.strip().lower()
    if "@" not in lower:
        return False
    host = lower.split("@", 1)[1]
    return host == "nse.co.in" or host.endswith(".nse.co.in")


def _discovery_sender_probe_order(bank: BankSendersConfig) -> list[str]:
    """Stable iteration order: all non-NSE senders (sorted), then all ``@nse.co.in`` (sorted)."""
    keys = sorted(bank.keys())
    primary = [k for k in keys if not _is_nse_deferred_discovery_sender(k)]
    deferred = [k for k in keys if _is_nse_deferred_discovery_sender(k)]
    return primary + deferred


@dataclass(frozen=True)
class DiscoveredSource:
    """One configured sender row after a discovery pass."""

    sender_email: str
    display_name: str
    instrument_type: str
    email_count_estimate: int
    # Message IDs from list() — persist-sources fetches bodies by ID (no re-search).
    sample_message_ids: list[str] = dataclasses.field(default_factory=list)


def discover_sources_iter(
    gmail_client: GmailClient,
    bank_senders_config: BankSendersConfig,
    *,
    list_max_results: int = 100,
) -> Iterator[DiscoveredSource]:
    """Yield one :class:`DiscoveredSource` per configured sender as each probe completes.

    Useful for streaming NDJSON progress to the dashboard during onboarding.

    Senders are probed in :func:`_discovery_sender_probe_order` so ``@nse.co.in`` rows
    (redundant when ICICI Direct exists) do not stream ahead of other broker addresses.

    Args:
        gmail_client: Authenticated Gmail client.
        bank_senders_config: Per-user merged dict (same shape as ``BANK_SENDERS``).
        list_max_results: Cap for ``messages.list`` ``maxResults`` (Gmail max 500).
    """
    for sender_email in _discovery_sender_probe_order(bank_senders_config):
        cfg = bank_senders_config[sender_email]
        display_name = str(cfg.get("display_name") or sender_email)
        instrument_type = str(cfg.get("instrument_type") or "unknown")

        query = f"from:{sender_email}"
        ids = gmail_client.list_message_ids(query, max_results=list_max_results)

        yield DiscoveredSource(
            sender_email=sender_email,
            display_name=display_name,
            instrument_type=instrument_type,
            email_count_estimate=len(ids),
            sample_message_ids=ids[:5],
        )


def discover_sources(
    gmail_client: GmailClient,
    bank_senders_config: BankSendersConfig,
    *,
    list_max_results: int = 100,
) -> list[DiscoveredSource]:
    """Probe Gmail for each configured sender (one ``messages.list`` per sender).

    For every key in ``bank_senders_config`` we run ``from:<sender_email>`` with a
    single list call (up to ``list_max_results`` IDs). ``email_count_estimate`` is
    the number of IDs returned (0 if none). The first five IDs are stored for
    :func:`scraper.source_builder.persist_scraper_sources_from_discovery`.

    Returns:
        One :class:`DiscoveredSource` per configured sender key. Order matches
        :func:`_discovery_sender_probe_order` (non-``@nse.co.in`` first, then NSE).
    """
    return list(
        discover_sources_iter(
            gmail_client,
            bank_senders_config,
            list_max_results=list_max_results,
        )
    )


def discovered_sources_to_json(rows: list[DiscoveredSource]) -> list[dict[str, object]]:
    """Serialize discovery rows for JSON / DB storage."""
    encoded: list[dict[str, object]] = []
    for r in rows:
        encoded.append(
            {
                "sender_email": r.sender_email,
                "display_name": r.display_name,
                "instrument_type": r.instrument_type,
                "email_count_estimate": r.email_count_estimate,
                "sample_message_ids": list(r.sample_message_ids),
            }
        )
    return encoded
