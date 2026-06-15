"""Send a usage-limit-reset push, via ntfy, Telegram, or a Discord webhook.

This is the optional "ping me" companion to the local reset toast. Each sender
is a single HTTPS POST, so no new dependency is needed — httpx already ships
with the app:
  - ntfy (https://ntfy.sh): no account or API key; you pick a hard-to-guess
    topic name and subscribe to it in the ntfy mobile app.
  - Telegram: a bot token (from @BotFather) plus the destination chat ID.
  - Discord: an incoming-webhook URL for a channel (Channel Settings ->
    Integrations -> Webhooks), so alerts land in a Discord channel.

This module stays Qt-free and does the network calls itself; the dashboard runs
them off the UI thread. ntfy URL building is split out (resolve_url) so it can
be tested without touching the network.
"""

from __future__ import annotations

DEFAULT_SERVER = "https://ntfy.sh"


def resolve_url(topic: str, server: str = DEFAULT_SERVER) -> str:
    """Map a topic (or a full ntfy URL) to the endpoint to POST to.

    A bare topic like "clawd-nick-7f3a" is appended to the default server; a
    value that already looks like a URL is used as-is, so self-hosted ntfy
    servers work too. Raises ValueError on an empty topic.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("ntfy topic is empty")
    if topic.startswith(("http://", "https://")):
        return topic.rstrip("/")
    return f"{server.rstrip('/')}/{topic.strip('/')}"


def send_ntfy(
    topic: str,
    title: str,
    body: str,
    *,
    server: str = DEFAULT_SERVER,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST a notification to an ntfy topic. Returns (ok, message).

    Network, HTTP and config errors are caught and reported rather than raised,
    so a flaky phone push never disrupts the local notification path.
    """
    try:
        url = resolve_url(topic, server)
    except ValueError as exc:
        return False, str(exc)

    # Lazy import mirrors token_refresh — keeps this module importable in tests
    # without httpx, and Qt-free at import time.
    import httpx

    # Title goes in a header (ASCII-safe); the message body is the POST content.
    headers = {"Title": title, "Priority": "default", "Tags": "bell"}
    # Broad except on purpose: beyond HTTPError, a malformed topic can raise
    # httpx.InvalidURL (not an HTTPError subclass) — report it, don't raise.
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(url, content=body.encode("utf-8"), headers=headers)
            resp.raise_for_status()
    except Exception as exc:
        return False, f"ntfy push failed: {exc}"
    return True, "sent"


def send_telegram(
    token: str,
    chat_id: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST a notification to a Telegram chat via the Bot API. Returns (ok, message).

    Needs a bot token (from @BotFather) and the destination chat ID. The title
    becomes the first line of the message. Errors are reported, never raised, so
    a flaky push never disrupts the local notification path.
    """
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return False, "Telegram bot token and chat ID are both required"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"{title}\n{body}" if title else body
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(url, json={"chat_id": chat_id, "text": text})
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Telegram push failed: {exc}"
    return True, "sent"


def send_discord(
    webhook_url: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST a notification to a Discord channel via an incoming webhook URL.
    Returns (ok, message).

    Create the webhook in Discord: Channel Settings -> Integrations -> Webhooks
    -> New Webhook -> Copy URL. The title becomes a bold first line. Errors are
    reported, never raised, so a flaky push never disrupts the local path.
    """
    webhook_url = (webhook_url or "").strip()
    if not webhook_url:
        return False, "Discord webhook URL is empty"
    if not webhook_url.startswith(("http://", "https://")):
        return False, "Discord webhook URL must start with https://"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    # Markdown-bold title, then the body. Discord caps content at 2000 chars;
    # trim to 1900 to leave headroom for the bold-title markup.
    content = f"**{title}**\n{body}" if title else body
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(webhook_url, json={"content": content[:1900]})
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Discord push failed: {exc}"
    return True, "sent"
