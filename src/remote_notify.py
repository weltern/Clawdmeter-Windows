"""Send a usage-limit-reset push via any of several services.

This is the optional "ping me" companion to the local reset toast. Each sender
is a single HTTPS POST, so no new dependency is needed — httpx already ships
with the app:
  - ntfy (https://ntfy.sh): no account or API key; you pick a hard-to-guess
    topic name and subscribe to it in the ntfy mobile app.
  - Telegram: a bot token (from @BotFather) plus the destination chat ID.
  - Discord: an incoming-webhook URL for a channel (Channel Settings ->
    Integrations -> Webhooks), so alerts land in a Discord channel.
  - Slack: an incoming-webhook URL (api.slack.com/messaging/webhooks).
  - Generic webhook: a JSON {title, body, app} POST to any URL (Zapier / Make /
    IFTTT / n8n / Home Assistant / custom endpoints).
  - Pushover: an application API token + your user key.
  - Gotify: a self-hosted server URL + an application token.

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


def send_slack(
    webhook_url: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST to a Slack channel via an incoming webhook URL. Returns (ok, message).

    Create the webhook at api.slack.com/messaging/webhooks. Slack mrkdwn uses a
    single asterisk for bold. Errors reported, never raised.
    """
    webhook_url = (webhook_url or "").strip()
    if not webhook_url:
        return False, "Slack webhook URL is empty"
    if not webhook_url.startswith(("http://", "https://")):
        return False, "Slack webhook URL must start with https://"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    text = f"*{title}*\n{body}" if title else body
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(webhook_url, json={"text": text})
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Slack push failed: {exc}"
    return True, "sent"


def send_webhook(
    url: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST a generic JSON payload to any URL. Returns (ok, message).

    The body is ``{"title", "body", "app"}`` so it can drive Zapier / Make /
    IFTTT / n8n / Home Assistant / a custom endpoint. Errors reported, never
    raised.
    """
    url = (url or "").strip()
    if not url:
        return False, "Webhook URL is empty"
    if not url.startswith(("http://", "https://")):
        return False, "Webhook URL must start with https://"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    payload = {"title": title, "body": body, "app": "Clawdmeter"}
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Webhook push failed: {exc}"
    return True, "sent"


def send_pushover(
    token: str,
    user: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST to Pushover. Returns (ok, message).

    Needs an application API token (create an app at pushover.net) and your user
    key (from the Pushover dashboard). Errors reported, never raised.
    """
    token = (token or "").strip()
    user = (user or "").strip()
    if not token or not user:
        return False, "Pushover API token and user key are both required"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    data = {"token": token, "user": user, "title": title, "message": body}
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post("https://api.pushover.net/1/messages.json", data=data)
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Pushover push failed: {exc}"
    return True, "sent"


def send_gotify(
    server: str,
    token: str,
    title: str,
    body: str,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """POST to a self-hosted Gotify server. Returns (ok, message).

    Needs the server URL and an application token (Gotify -> Apps). The token
    rides the X-Gotify-Key header (kept out of the URL). Errors reported, never
    raised.
    """
    server = (server or "").strip().rstrip("/")
    token = (token or "").strip()
    if not server or not token:
        return False, "Gotify server URL and app token are both required"
    if not server.startswith(("http://", "https://")):
        return False, "Gotify server URL must start with https://"

    import httpx  # lazy, mirrors send_ntfy — keeps this Qt/httpx-free at import

    payload = {"title": title, "message": body, "priority": 5}
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(f"{server}/message",
                             json=payload, headers={"X-Gotify-Key": token})
            resp.raise_for_status()
    except Exception as exc:
        return False, f"Gotify push failed: {exc}"
    return True, "sent"
