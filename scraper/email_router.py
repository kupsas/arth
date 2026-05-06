"""
Email router — maps a GmailMessage to the correct parser.

Usage:
    parser = find_parser(msg.sender, msg.subject)
    if parser:
        txns = parser.parse(html_body, msg.received_at.date())

The router normalises sender addresses before lookup because Gmail's "From"
header often includes a display name:
    "HDFC Bank <alerts@hdfcbank.net>"  →  "alerts@hdfcbank.net"
"""

from __future__ import annotations

import re
import logging

from parsers.email_registry import EMAIL_PARSER_REGISTRY
from parsers.alerts.base import BaseEmailParser

logger = logging.getLogger(__name__)

# Matches the email address inside angle brackets, e.g. "HDFC Bank <alerts@hdfcbank.net>"
_EMAIL_IN_BRACKETS = re.compile(r"<([^>]+)>")


def _normalise_sender(raw_sender: str) -> str:
    """Extract just the email address from a raw From header value.

    "HDFC Bank <alerts@hdfcbank.net>"  →  "alerts@hdfcbank.net"
    "alerts@hdfcbank.net"              →  "alerts@hdfcbank.net"
    """
    m = _EMAIL_IN_BRACKETS.search(raw_sender)
    if m:
        return m.group(1).strip().lower()
    return raw_sender.strip().lower()


def find_parser(
    raw_sender: str,
    subject: str,
    *,
    registry: dict[str, list[BaseEmailParser]] | None = None,
) -> BaseEmailParser | None:
    """Return the correct parser for this email, or None if no parser matches.

    Args:
        raw_sender: The raw "From" header value (may include display name).
        subject:    The email subject line.
        registry:   Optional per-user parser list map (from
                    :func:`parsers.email_registry.build_email_parser_registry`).
                    Defaults to the static :data:`EMAIL_PARSER_REGISTRY`.

    Returns:
        The first parser whose can_parse() returns True, or None.
        None means the orchestrator should skip this email.
    """
    sender = _normalise_sender(raw_sender)
    reg = registry if registry is not None else EMAIL_PARSER_REGISTRY
    parsers = reg.get(sender, [])

    if not parsers:
        logger.debug("No parsers registered for sender '%s'", sender)
        return None

    for parser in parsers:
        if parser.can_parse(sender, subject):
            logger.debug(
                "Routing subject_len=%d from '%s' → %s",
                len(subject or ""),
                sender,
                type(parser).__name__,
            )
            return parser

    logger.debug(
        "Sender '%s' is registered but no parser matched (subject_len=%d)",
        sender,
        len(subject or ""),
    )
    return None
