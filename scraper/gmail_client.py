"""
GmailClient — authenticated access to the Gmail API.

Responsibilities:
  1. OAuth2 authentication (opens browser on first run, refreshes token automatically)
  2. Searching for emails by sender and date
  3. Extracting the HTML body from each email (handles multipart MIME)

How OAuth works (plain English):
  - First run: opens your browser, you click "Allow", Google saves a token to
    data/gmail_token.json.
  - Normal runs: the library refreshes the short-lived access token using the
    saved refresh token — no browser.
  - If Google revokes the refresh token (password change, idle timeout, you
    removed app access): you must consent again via the same OAuth flow.

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
import html
import logging
import webbrowser
import wsgiref.simple_server
import wsgiref.util
from dataclasses import dataclass
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from scraper.config import GMAIL_CREDENTIALS_PATH, GMAIL_SCOPES, GMAIL_TOKEN_PATH

logger = logging.getLogger(__name__)


class _OAuthLocalRequestHandler(wsgiref.simple_server.WSGIRequestHandler):
    """HTTP access log lines go to our logger (same idea as google_auth_oauthlib)."""

    def log_message(self, format: str, *args: object) -> None:
        logger.info(format, *args)


class _OAuthSuccessHtmlApp:
    """WSGI app for the OAuth redirect that returns HTML with a real ``<title>``.

    The stock ``google_auth_oauthlib`` handler responds with ``text/plain`` only.
    With no document title, many browsers use the **full callback URL** (query string
    with ``state``, ``code``, ``scope``…) as the tab label — unreadable. Serving a
    minimal HTML page fixes the tab title while keeping the same success copy.
    """

    def __init__(self, body_text: str, page_title: str) -> None:
        self.last_request_uri: str | None = None
        self._body_text = body_text
        self._page_title = page_title

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
        self.last_request_uri = wsgiref.util.request_uri(environ)
        safe_title = html.escape(self._page_title)
        safe_body = html.escape(self._body_text)
        doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{safe_title}</title>
  <style>
    body {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      background: #141414;
      color: #f5f5f5;
      display: flex;
      min-height: 100vh;
      margin: 0;
      align-items: center;
      justify-content: center;
      padding: 1rem;
      text-align: center;
    }}
  </style>
</head>
<body><p>{safe_body}</p></body>
</html>"""
        return [doc.encode("utf-8")]


def _run_oauth_local_server_with_tab_title(
    flow: InstalledAppFlow,
    *,
    page_title: str = "Authentication complete",
    host: str = "localhost",
    bind_addr: str | None = None,
    port: int = 0,
    success_message: str = (
        "The authentication flow has completed. You may close this window."
    ),
    open_browser: bool = True,
    redirect_uri_trailing_slash: bool = True,
    timeout_seconds: int | None = None,
    token_audience: str | None = None,
    browser: str | None = None,
    **kwargs: Any,
) -> Credentials:
    """Run the installed-app OAuth flow with a browser tab title that is not the URL.

    Mirrors ``InstalledAppFlow.run_local_server`` from ``google_auth_oauthlib`` but
    uses :class:`_OAuthSuccessHtmlApp` so the success response includes ``<title>``.
    """
    wsgi_app = _OAuthSuccessHtmlApp(success_message, page_title)
    wsgiref.simple_server.WSGIServer.allow_reuse_address = False
    local_server = wsgiref.simple_server.make_server(
        bind_addr or host,
        port,
        wsgi_app,
        handler_class=_OAuthLocalRequestHandler,
    )
    try:
        redirect_uri_format = (
            "http://{}:{}/" if redirect_uri_trailing_slash else "http://{}:{}"
        )
        flow.redirect_uri = redirect_uri_format.format(host, local_server.server_port)
        auth_url, _ = flow.authorization_url(**kwargs)

        if open_browser:
            webbrowser.get(browser).open(auth_url, new=1, autoraise=True)

        local_server.timeout = timeout_seconds
        local_server.handle_request()

        try:
            authorization_response = wsgi_app.last_request_uri.replace(
                "http", "https",
            )
        except AttributeError as e:
            raise WSGITimeoutError(
                "Timed out waiting for response from authorization server"
            ) from e

        flow.fetch_token(
            authorization_response=authorization_response,
            audience=token_audience,
        )
    finally:
        local_server.server_close()

    return flow.credentials


class GmailReauthRequiredError(Exception):
    """Raised when the saved refresh token is dead and OAuth must be done again.

    Google's ``invalid_grant`` means the refresh token was revoked, expired under
    policy, or the user changed their password — it cannot be silently renewed.
    Callers that cannot open a browser (e.g. APScheduler) should catch this and
    tell the user to visit ``POST /api/scraper/oauth/init``.
    """


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

    def authenticate(self, *, allow_interactive_oauth: bool = True) -> None:
        """Authenticate with Gmail via OAuth2.

        On first run (or after a revoked refresh token): opens the browser for
        Google consent — unless ``allow_interactive_oauth=False`` (scheduler),
        in which case :class:`GmailReauthRequiredError` is raised instead.

        On normal subsequent runs: loads the saved token and refreshes the
        short-lived access token without a browser.

        Args:
            allow_interactive_oauth: If False, never call ``run_local_server()``;
                used by the background scheduler so it does not try to open a
                browser on the server. Revoked refresh tokens then raise
                ``GmailReauthRequiredError``.

        Raises:
            FileNotFoundError: if gmail_credentials.json doesn't exist yet.
            GmailReauthRequiredError: if re-consent is needed but interactive
                OAuth is disabled.
            Exception: if the OAuth flow fails for other reasons.
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

        # Already usable (access token still valid).
        if creds and creds.valid:
            self._service = build("gmail", "v1", credentials=creds)
            logger.info("Gmail API authenticated successfully.")
            return

        # Access token expired but we can ask Google for a new one using the
        # long-lived refresh token — no browser.
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Gmail token expired, refreshing...")
                creds.refresh(Request())
            except RefreshError as e:
                # invalid_grant: refresh token revoked, app restricted, password
                # change, etc. — cannot recover without a new consent screen.
                logger.error(
                    "Gmail refresh token rejected by Google (revoked or expired): %s",
                    e,
                )
                try:
                    GMAIL_TOKEN_PATH.unlink(missing_ok=True)
                except OSError:
                    pass
                creds = None
                if not allow_interactive_oauth:
                    raise GmailReauthRequiredError(
                        "Gmail disconnected: Google rejected the saved login "
                        "(token revoked or expired). Reconnect: POST "
                        "/api/scraper/oauth/init on this machine (opens browser)."
                    ) from e
                logger.info(
                    "Removed invalid token file; opening browser for new consent..."
                )

        # After a successful refresh, persist the updated access/refresh pair.
        if creds and creds.valid:
            GMAIL_TOKEN_PATH.write_text(creds.to_json())
            logger.info("Gmail token saved to %s", GMAIL_TOKEN_PATH)
            self._service = build("gmail", "v1", credentials=creds)
            logger.info("Gmail API authenticated successfully.")
            return

        # No working credentials — need a browser consent flow, or fail clearly.
        if not allow_interactive_oauth:
            raise GmailReauthRequiredError(
                "Gmail is not connected (no valid token). "
                "Complete OAuth: POST /api/scraper/oauth/init."
            )

        logger.info(
            "No valid Gmail token found. Opening browser for OAuth consent..."
        )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(GMAIL_CREDENTIALS_PATH), GMAIL_SCOPES
        )
        # Custom local server: default library response is plain text → ugly tab titles.
        creds = _run_oauth_local_server_with_tab_title(flow, port=0)
        GMAIL_TOKEN_PATH.write_text(creds.to_json())
        logger.info("Gmail token saved to %s", GMAIL_TOKEN_PATH)

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

    def search_messages(
        self,
        query: str,
        *,
        paginate: bool = False,
        max_results_per_page: int = 100,
        max_total: int | None = None,
    ) -> list[GmailMessage]:
        """Search Gmail with an arbitrary query string (same syntax as the web UI).

        Used by backfill / validation scripts that need subject-based or compound
        queries. The scraper's :meth:`fetch_emails` delegates here.

        Args:
            query: Full Gmail search string, e.g.
                ``from:alerts@hdfcbank.net after:2026/01/01``.
            paginate: If False (default), only the first ``list()`` page is fetched
                — fast for interactive scripts. If True, follows ``nextPageToken``
                until all matching messages are retrieved (historical backfill).
            max_results_per_page: Page size for each ``messages().list()`` call
                (max 500 per Gmail API docs; we default to 100).
            max_total: When paginating, stop after this many messages (``None`` =
                no cap). Ignored when ``paginate`` is False.

        Returns:
            Newest-first list of :class:`GmailMessage` (metadata only).

        Raises:
            RuntimeError: if called before authenticate().
            HttpError: if the Gmail API returns an error.
        """
        self._require_auth()
        logger.debug("Gmail search_messages query: %s paginate=%s", query, paginate)

        raw_messages: list[dict] = []
        page_token: str | None = None

        try:
            while True:
                request = (
                    self._service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        maxResults=max_results_per_page,
                        pageToken=page_token,
                    )
                )
                response = request.execute()
                batch = response.get("messages", [])
                raw_messages.extend(batch)

                if not paginate:
                    break
                if max_total is not None and len(raw_messages) >= max_total:
                    raw_messages = raw_messages[:max_total]
                    break
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as e:
            logger.error("Gmail API list() failed for query %r: %s", query, e)
            raise

        if not raw_messages:
            logger.debug("No emails found for query: %s", query)
            return []

        logger.info("Found %d email(s) matching query (paginate=%s)", len(raw_messages), paginate)

        result: list[GmailMessage] = []
        for raw in raw_messages:
            try:
                msg = (
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=raw["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                    .execute()
                )
                result.append(self._parse_metadata(msg))
            except HttpError as e:
                logger.warning("Failed to fetch metadata for message %s: %s", raw["id"], e)
                continue

        return result

    def fetch_emails(
        self,
        sender: str,
        after_date: datetime.date,
        max_results: int = 100,
        *,
        paginate: bool = False,
        max_total: int | None = None,
    ) -> list[GmailMessage]:
        """Search Gmail for emails from a specific sender after a given date.

        Args:
            sender:      Email address to filter by, e.g. "alerts@hdfcbank.net"
            after_date:  Only return emails received on or after this date.
                         Gmail's "after:" filter is inclusive and uses YYYY/MM/DD format.
            max_results: Page size for ``messages().list()`` (default 100). When
                         ``paginate`` is False, only one page is returned.
            paginate:    If True, follow ``nextPageToken`` until all pages are read
                         (needed for multi-year statement backfills). The 15-minute
                         scraper should keep the default False so each poll stays a
                         single cheap API round-trip.
            max_total:   When ``paginate`` is True, optional cap on total messages.

        Returns:
            List of GmailMessage objects, ordered newest-first (Gmail default).
            Returns an empty list if no matching emails are found.

        Raises:
            RuntimeError: if called before authenticate().
            HttpError: if the Gmail API returns an error.
        """
        query = f"from:{sender} after:{after_date.strftime('%Y/%m/%d')}"
        return self.search_messages(
            query,
            paginate=paginate,
            max_results_per_page=max_results,
            max_total=max_total,
        )

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

    def fetch_message_by_id(self, message_id: str) -> GmailMessage:
        """Load :class:`GmailMessage` metadata (From, Subject, received time) by internal id."""
        self._require_auth()
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata")
            .execute()
        )
        return self._parse_metadata(msg)

    # ── Attachment extraction ───────────────────────────────────────────────────

    def get_attachments(self, message_id: str) -> list[tuple[str, bytes]]:
        """Download all PDF parts from a message and return ``(filename, raw_bytes)`` pairs.

        Walks the MIME tree (including nested multiparts), decodes inline bodies, and
        uses the Gmail ``attachments.get`` API for large parts that only expose an
        ``attachmentId``.

        Non-PDF parts are skipped. Filenames default to ``attachment.pdf`` when the
        MIME part has no name.

        Args:
            message_id: Gmail message id (same as :attr:`GmailMessage.id`).

        Returns:
            A list in document order; may be empty if there are no PDF attachments.

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
            logger.error("Failed to fetch full message %s for attachments: %s", message_id, e)
            raise

        out: list[tuple[str, bytes]] = []
        self._walk_payload_for_pdfs(msg.get("payload") or {}, message_id, out)
        logger.debug(
            "get_attachments(%s): %d PDF part(s)", message_id, len(out),
        )
        return out

    def _walk_payload_for_pdfs(
        self,
        payload: dict,
        message_id: str,
        out: list[tuple[str, bytes]],
    ) -> None:
        """Recursively collect PDF leaf parts (same MIME walk idea as HTML extraction)."""
        parts = payload.get("parts")
        if parts:
            for part in parts:
                self._walk_payload_for_pdfs(part, message_id, out)
            return

        mime_type = payload.get("mimeType", "")
        filename = (payload.get("filename") or "").strip()
        is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")
        if not is_pdf:
            return

        raw = self._download_mime_part_bytes(message_id, payload)
        if not raw:
            logger.warning(
                "PDF MIME part present but no bytes for message %s (%s)",
                message_id,
                filename or mime_type,
            )
            return
        out.append((filename or "attachment.pdf", raw))

    def _download_mime_part_bytes(self, message_id: str, part: dict) -> bytes:
        """Decode inline base64 body or fetch by attachmentId."""
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if attachment_id:
            try:
                att = (
                    self._service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=attachment_id)
                    .execute()
            )
            except HttpError as e:
                logger.error(
                    "attachments.get failed for message %s id %s: %s",
                    message_id,
                    attachment_id,
                    e,
                )
                raise
            data = att.get("data", "")
            if not data:
                return b""
            return base64.urlsafe_b64decode(data + "==")

        inline = body.get("data")
        if inline:
            return base64.urlsafe_b64decode(inline + "==")
        return b""

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
