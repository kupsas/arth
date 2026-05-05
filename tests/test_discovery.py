"""Unit tests for ``scraper.discovery`` — Gmail source discovery (Track 2 Phase 2a).

We mock :class:`scraper.gmail_client.GmailClient` so tests never call the real Gmail API.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from scraper.discovery import (
    DiscoveredSource,
    discover_sources,
    discover_sources_iter,
    discovered_sources_to_json,
)


def test_discover_sources_iter_probes_nse_senders_last() -> None:
    """``@nse.co.in`` mailboxes run after other configured senders so streaming UI is not
    misled into showing an NSE-only demat row while ICICI Direct is still queued
    (alphabetical order used to probe ``ebix@nse.co.in`` before ``service@icicisecurities.com``).
    """
    client = MagicMock()
    client.list_message_ids.return_value = []

    bank = {
        "ebix@nse.co.in": {"display_name": "NSE ebix", "source_type": "broker"},
        "service@icicisecurities.com": {"display_name": "ICICI Direct", "source_type": "broker"},
        "alerts@hdfcbank.net": {"display_name": "HDFC", "source_type": "savings"},
    }
    list(discover_sources_iter(client, bank))

    from_queries = [c.args[0] for c in client.list_message_ids.call_args_list]
    assert len(from_queries) == 3
    assert from_queries[0] == "from:alerts@hdfcbank.net"
    assert from_queries[1] == "from:service@icicisecurities.com"
    assert from_queries[2] == "from:ebix@nse.co.in"


def test_discover_sources_empty_mailbox() -> None:
    """When Gmail returns no IDs for a sender, estimate is 0 and sample IDs are empty."""
    client = MagicMock()
    client.list_message_ids.return_value = []

    bank = {
        "nobody@example.com": {
            "display_name": "Empty Bank",
            "source_type": "savings",
        }
    }
    out = discover_sources(client, bank)
    assert len(out) == 1
    r = out[0]
    assert r.sender_email == "nobody@example.com"
    assert r.email_count_estimate == 0
    assert r.sample_message_ids == []
    client.list_message_ids.assert_called()


def test_discover_sources_finds_ids_and_caps_estimate() -> None:
    """``email_count_estimate`` is len(ids) from a single list call; first 3 IDs are samples."""
    client = MagicMock()
    ids = [f"m{i}" for i in range(10)]
    client.list_message_ids.return_value = ids

    bank = {
        "alerts@hdfcbank.net": {
            "display_name": "HDFC Test",
            "source_type": "savings",
        }
    }
    out = discover_sources(client, bank, list_max_results=100)
    assert len(out) == 1
    r = out[0]
    assert r.email_count_estimate == 10
    assert r.sample_message_ids == ["m0", "m1", "m2"]
    client.list_message_ids.assert_called_once_with("from:alerts@hdfcbank.net", max_results=100)


def test_discovered_sources_to_json_round_trip() -> None:
    """JSON helper matches API contract (no surprise types)."""
    row = DiscoveredSource(
        sender_email="a@b.com",
        display_name="Bank",
        source_type="savings",
        email_count_estimate=3,
        sample_message_ids=["x1", "x2"],
    )
    payload = discovered_sources_to_json([row])[0]
    assert payload["sender_email"] == "a@b.com"
    assert payload["email_count_estimate"] == 3
    assert payload["sample_message_ids"] == ["x1", "x2"]
    assert "earliest_email_date" not in payload
    assert "latest_email_date" not in payload
