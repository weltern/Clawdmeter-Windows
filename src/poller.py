"""Claude usage polling for Clawdmeter-Windows.

Ported from HermannBjorgvin/Clawdmeter daemon. The BLE/asyncio plumbing is
gone; this is a QThread that posts UsageSample objects via a Qt signal.

Token resolution order on Windows:
  1. CLAUDE_CREDENTIALS_PATH env var (explicit override)
  2. ~/.claude/.credentials.json (Claude Code default)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal

import app_settings
import token_refresh

API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS_TEMPLATE = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.5",
}
API_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}

DEFAULT_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
POLL_INTERVAL_SECONDS = 60


@dataclass
class UsageSample:
    """One snapshot of Claude rate-limit state. Mirrors the BLE payload."""

    session_pct: int
    session_reset_minutes: int
    weekly_pct: int
    weekly_reset_minutes: int
    status: str
    ok: bool
    error: str | None = None
    timestamp: float = 0.0
    # Usage past the weekly cap (paid "overage" tier). 0 for the vast majority
    # of accounts/time; the UI only surfaces it when overage_pct > 0. Its own
    # reset clock (longer than weekly), so it's tracked separately.
    overage_pct: int = 0
    overage_reset_minutes: int = 0


def credentials_path() -> Path:
    override = os.environ.get("CLAUDE_CREDENTIALS_PATH")
    return Path(override) if override else DEFAULT_CREDENTIALS_PATH


def _extract_access_token(blob: str) -> str | None:
    """Pull accessToken from a credentials blob — JSON, nested JSON, or raw."""
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("accessToken"), str):
            return data["accessToken"]
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                return v["accessToken"]
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-.~+/=]{20,}", blob):
        return blob
    return None


def read_token() -> str | None:
    path = credentials_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _extract_access_token(raw)


def sample_from_headers(headers, now: float) -> UsageSample:
    """Build a UsageSample from the API's rate-limit response headers.

    Pure (no network) so it's unit-testable. ``headers`` is any mapping with a
    ``.get(name, default)`` method (httpx ``Headers`` or a plain dict).
    """
    def hdr(name: str, default: str = "0") -> str:
        return headers.get(name, default)

    def reset_minutes(reset_ts: str) -> int:
        try:
            r = float(reset_ts)
        except (TypeError, ValueError):
            return 0
        mins = (r - now) / 60.0
        return int(round(mins)) if mins > 0 else 0

    def pct(util: str) -> int:
        try:
            return int(round(float(util) * 100))
        except (TypeError, ValueError):
            return 0

    return UsageSample(
        session_pct=pct(hdr("anthropic-ratelimit-unified-5h-utilization")),
        session_reset_minutes=reset_minutes(hdr("anthropic-ratelimit-unified-5h-reset")),
        weekly_pct=pct(hdr("anthropic-ratelimit-unified-7d-utilization")),
        weekly_reset_minutes=reset_minutes(hdr("anthropic-ratelimit-unified-7d-reset")),
        status=hdr("anthropic-ratelimit-unified-5h-status", "unknown"),
        ok=True,
        error=None,
        timestamp=now,
        overage_pct=pct(hdr("anthropic-ratelimit-unified-overage-utilization")),
        overage_reset_minutes=reset_minutes(
            hdr("anthropic-ratelimit-unified-overage-reset")
        ),
    )


def _poll_once(token: str) -> UsageSample:
    """Make one rate-limit probe. Returns a sample with ok=False on failure."""
    headers = dict(API_HEADERS_TEMPLATE)
    headers["Authorization"] = f"Bearer {token}"
    now = time.time()
    try:
        with httpx.Client(timeout=20.0) as http:
            resp = http.post(API_URL, headers=headers, json=API_BODY)
    except httpx.HTTPError as exc:
        return UsageSample(0, 0, 0, 0, "error", False, str(exc), now)
    return sample_from_headers(resp.headers, now)


class UsagePoller(QThread):
    """Background polling thread. Emits sample(UsageSample) on every poll.

    Also keeps the OAuth access token fresh (BETA): when the stored token is
    expired it refreshes it via `token_refresh` before polling, so the
    dashboard doesn't go blank every ~8h. A manual refresh can be requested
    with request_manual_refresh(); outcomes are reported on `refresh_status`.
    All refresh work runs on this thread (never the GUI thread).
    """

    sample = Signal(UsageSample)
    refresh_status = Signal(object)  # token_refresh.RefreshResult

    REFRESH_COOLDOWN_MIN = 60.0    # seconds between auto attempts
    REFRESH_COOLDOWN_MAX = 900.0   # backoff ceiling after a 429

    def __init__(self, interval_seconds: int = POLL_INTERVAL_SECONDS, parent=None) -> None:
        super().__init__(parent)
        self._interval = interval_seconds
        self._stop = False
        self._auto_refresh = app_settings.get_auto_refresh()
        self._manual_refresh = False
        self._last_refresh_attempt = 0.0
        self._cooldown = self.REFRESH_COOLDOWN_MIN

    def stop(self) -> None:
        self._stop = True

    def set_auto_refresh(self, on: bool) -> None:
        self._auto_refresh = bool(on)

    def set_interval(self, seconds: int) -> None:
        """Change the poll cadence. Takes effect on the next cycle: the current
        sleep already snapshotted the old interval via range(), so a mid-sleep
        change won't wake it early (instant apply would need a wake flag like
        _manual_refresh — deferred for now)."""
        self._interval = max(1, int(seconds))

    def request_manual_refresh(self) -> None:
        """Ask the poll thread to refresh the token ASAP (bypasses cooldown)."""
        self._manual_refresh = True

    def _do_refresh(self, manual: bool) -> None:
        self._last_refresh_attempt = time.time()
        result = token_refresh.refresh(credentials_path())
        if result.ok:
            self._cooldown = self.REFRESH_COOLDOWN_MIN
        elif result.http_status == 429 and not manual:
            self._cooldown = min(self._cooldown * 2, self.REFRESH_COOLDOWN_MAX)
        self.refresh_status.emit(result)

    def _maybe_auto_refresh(self) -> None:
        if not self._auto_refresh:
            return
        if not token_refresh.is_expired(credentials_path()):
            return
        if time.time() - self._last_refresh_attempt < self._cooldown:
            return
        self._do_refresh(manual=False)

    def run(self) -> None:  # QThread entry
        while not self._stop:
            if self._manual_refresh:
                self._manual_refresh = False
                self._do_refresh(manual=True)
            self._maybe_auto_refresh()

            token = read_token()
            if not token:
                self.sample.emit(UsageSample(
                    0, 0, 0, 0, "no-token", False,
                    f"No token at {credentials_path()}", time.time(),
                ))
            else:
                self.sample.emit(_poll_once(token))

            for _ in range(self._interval):
                if self._stop:
                    return
                if self._manual_refresh:
                    break  # service the request promptly at the top of the loop
                self.msleep(1000)
