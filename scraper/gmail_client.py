"""
GmailClient — authenticated access to the Gmail API.

Responsibilities:
  1. OAuth2 authentication (opens browser on first run, refreshes token automatically)
  2. Searching for emails by sender and date
  3. Extracting the HTML body from each email (handles multipart MIME)

How OAuth works (plain English):
  - First run: opens your browser, you click "Allow", Google saves a token to
    data/gmail_token.json.  You never have to do this again.
  - Every subsequent run: the library reads the token file and refreshes it
    silently if it's expired.  No browser needed.

Usage:
    import logging

    logger = logging.getLogger(__name__)
    client = GmailClient()
    client.authenticate()                              # one-time browser prompt

    messages = client.fetch_emails(
        sender="alerts@hdfcbank.net",
        after_date=datetime.date(2026, 3, 1),
    )
    for msg in messages:
        html = client.get_message_body(msg["id"])
        logger.debug("HTML preview: %s...", html[:200])
"""

from __future__ import annotations

import base64
import datetime
import logging
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from scraper.config import GMAIL_CREDENTIALS_PATH, GMAIL_SCOPES, GMAIL_TOKEN_PATH

logger = logging.getLogger(__name__)


# ─── Data class for a raw Gmail message (just the metadata we need) ────────────

@dataclass
class GmailMessage:
    """Lightweight container for a Gmail message — only what the scraper needs."""
    id: str                         # Gmail message ID (unique, used for dedup)
    thread_id: str                  # Gmail thread ID
    sender: str                     # The "From" header value
    subject: str                    # The "Subject" header value
    received_at: datetime.datetime  # When Gmail received it (UTC)


# ─── Main client class ─────────────────────────────────────────────────────────

class GmailClient:
    """Thin wrapper around the Gmail API v1.

    Handles authentication, email search, and HTML body extraction.
    All methods raise informative errors rather than returning None silently.
    """

    def __init__(self) -> None:
        # _service is the authenticated Gmail API object.
        # It's None until authenticate() is called.
        self._service = None

    # ── Authentication ──────────────────────────────────────────────────────────

    def authenticate(self) -> None:
        """Authenticate with Gmail via OAuth2.

        On first run: opens your browser for the Google consent screen.
        On subsequent runs: loads the saved token and refreshes it if expired.

        Raises:
            FileNotFoundError: if gmail_credentials.json doesn't exist yet.
            Exception: if the OAuth flow fails for any reason.
        """
        if not GMAIL_CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Gmail credentials not found at {GMAIL_CREDENTIALS_PATH}.\n"
                "Download credentials.json from GCP Console → APIs & Services → "
                "Credentials and save it to data/gmail_credentials.json."
            )

        creds: Credentials | None = None

        # Try loading an existing token first — avoids re-opening the browser.
        if GMAIL_TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(
                str(GMAIL_TOKEN_PATH), GMAIL_SCOPES
            )
            logger.debug("Loaded existing Gmail token from %s", GMAIL_TOKEN_PATH)

        # If there's no valid token (first run or expired + can't refresh), run the flow.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Token exists but is expired — refresh silently without opening browser.
                logger.info("Gmail token expired, refreshing...")
                creds.refresh(Request())
            else:
                # No token at all — open browser for one-time consent.
                logger.info(
                    "No valid Gmail token found. Opening browser for OAuth consent..."
                )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(GMAIL_CREDENTIALS_PATH), GMAIL_SCOPES
                )
                # run_local_server() starts a tiny temporary HTTP server on a
                # random local port, opens the browser, catches the callback,
                # and returns the credentials.  No manual copy-paste needed.
                creds = flow.run_local_server(port=0)

            # Save the token so we don't need to re-authenticate next time.
            GMAIL_TOKEN_PATH.write_text(creds.to_json())
            logger.info("Gmail token saved to %s", GMAIL_TOKEN_PATH)

        # Build the Gmail API service object — this is what we call methods on.
        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail API authenticated successfully.")

    @property
    def is_authenticated(self) -> bool:
        """True if authenticate() has been called successfully."""
        return self._service is not None

    def _require_auth(self) -> None:
        """Raise a clear error if someone calls a method before authenticating."""
        if not self.is_authenticated:
            raise RuntimeError(
                "GmailClient is not authenticated. Call authenticate() first."
            )

    # ── Email fetching ──────────────────────────────────────────────────────────

    def fetch_emails(
        self,
        sender: str,
        after_date: datetime.date,
        max_results: int = 100,
    ) -> list[GmailMessage]:
        """Search Gmail for emails from a specific sender after a given date.

        Args:
            sender:      Email address to filter by, e.g. "alerts@hdfcbank.net"
            after_date:  Only return emails received on or after this date.
                         Gmail's "after:" filter is inclusive and uses YYYY/MM/DD format.
            max_results: Cap on how many messages to return (default 100 is plenty
                         for a 15-minute polling window).

        Returns:
            List of GmailMessage objects, ordered newest-first (Gmail default).
            Returns an empty list if no matching emails are found.

        Raises:
            RuntimeError: if called before authenticate().
            HttpError: if the Gmail API returns an error.
        """
        self._require_auth()

        # Gmail search query syntax — same as the search bar in the browser.
        # "from:" filters by sender, "after:" filters by date (YYYY/MM/DD).
        query = f"from:{sender} after:{after_date.strftime('%Y/%m/%d')}"
        logger.debug("Gmail query: %s", query)

        try:
            # First call: get the list of matching message IDs.
            # Gmail's list() endpoint only returns IDs + thread IDs, not full messages.
            response = (
                self._service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        except HttpError as e:
            logger.error("Gmail API list() failed: %s", e)
            raise

        raw_messages = response.get("messages", [])
        if not raw_messages:
            logger.debug("No emails found for query: %s", query)
            return []

        logger.info(
            "Found %d email(s) from %s since %s", len(raw_messages), sender, after_date
        )

        # Second call (one per message): fetch full metadata for each message.
        # We use format="metadata" to get headers without downloading the body yet —
        # we only fetch bodies for emails that pass the dedup check in the orchestrator.
        result: list[GmailMessage] = []
        for raw in raw_messages:
            try:
                msg = (
                    self._service.users()
                    .messages()
                    .get(userId="me", id=raw["id"], format="metadata",
                         metadataHeaders=["From", "Subject", "Date"])
                    .execute()
                )
                result.append(self._parse_metadata(msg))
            except HttpError as e:
                logger.warning("Failed to fetch metadata for message %s: %s", raw["id"], e)
                continue

        return result

    def _parse_metadata(self, msg: dict) -> GmailMessage:
        """Extract the fields we care about from a raw Gmail API message dict."""
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Gmail stores internalDate as milliseconds since Unix epoch.
        received_ms = int(msg.get("internalDate", 0))
        received_at = datetime.datetime.fromtimestamp(
            received_ms / 1000, tz=datetime.timezone.utc
        )

        return GmailMessage(
            id=msg["id"],
            thread_id=msg["threadId"],
            sender=headers.get("From", ""),
            subject=headers.get("Subject", ""),
            received_at=received_at,
        )

    # ── Body extraction ─────────────────────────────────────────────────────────

    def get_message_body(self, message_id: str) -> str:
        """Fetch and return the HTML body of a Gmail message.

        Gmail stores email bodies as base64url-encoded MIME parts.  This method
        handles both simple (single-part) and multipart emails, always preferring
        the HTML part over the plain-text part.

        Args:
            message_id: The Gmail message ID (from GmailMessage.id).

        Returns:
            The decoded HTML body as a string.
            If no HTML part exists, falls back to the plain-text part.
            Returns an empty string if the body is completely missing.

        Raises:
            RuntimeError: if called before authenticate().
            HttpError: if the Gmail API returns an error.
        """
        self._require_auth()

        try:
            msg = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as e:
            logger.error("Failed to fetch body for message %s: %s", message_id, e)
            raise

        return self._extract_html(msg.get("payload", {}))

    def _extract_html(self, payload: dict) -> str:
        """Recursively walk the MIME payload tree to find the HTML (or text) part.

        Email MIME structure can be nested:
          - Simple email:     payload has a body directly
          - Multipart/mixed:  payload.parts = [text/plain, text/html, attachments...]
          - Multipart/related: payload.parts = [multipart/alternative, images...]

        We walk the tree recursively and collect all text/* parts, preferring HTML.
        """
        mime_type = payload.get("mimeType", "")
        parts = payload.get("parts", [])

        if parts:
            # Multipart message — recurse into each sub-part and collect results.
            html_parts: list[str] = []
            text_parts: list[str] = []

            for part in parts:
                result = self._extract_html(part)
                if result:
                    if part.get("mimeType", "") == "text/html":
                        html_parts.append(result)
                    elif part.get("mimeType", "") == "text/plain":
                        text_parts.append(result)
                    else:
                        # Sub-multipart result — treat as HTML since we already picked it
                        html_parts.append(result)

            # Prefer HTML; fall back to plain text
            if html_parts:
                return "\n".join(html_parts)
            if text_parts:
                return "\n".join(text_parts)
            return ""

        # Leaf node — decode the base64url body data.
        if mime_type in ("text/html", "text/plain"):
            body_data = payload.get("body", {}).get("data", "")
            if not body_data:
                return ""
            # Gmail uses base64url encoding (uses - and _ instead of + and /)
            decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
            return decoded

        return ""
