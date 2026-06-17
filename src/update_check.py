"""Update checking for Clawdmeter-Windows (Path 1: notify only).

We ship a single, unsigned, self-contained Clawdmeter.exe via GitHub Releases.
There's no installer or package manager, so this module just *detects* a newer
release and surfaces it — the user downloads and swaps the exe themselves.

The network + parsing live in plain, dependency-light functions (`fetch_latest`,
`parse_version`, `is_newer`, `extract_sha256`) so they're unit-testable and
reusable by a future one-click downloader. `UpdateInfo` already carries the
asset download URL and the published SHA-256, so adding download+verify+swap
(Path 2) is a drop-in later — nothing here needs to change.

`UpdateChecker` mirrors poller.UsagePoller: a QThread that wakes on a slow
cadence, throttles real network hits to roughly once a day via a persisted
timestamp, swallows every network error, and emits `update_available` on the
GUI thread when a newer, non-skipped release appears.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx
from PySide6.QtCore import QThread, Signal

import app_settings

REPO = "weltern/Clawdmeter-Windows"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# GitHub's REST API rejects requests with no User-Agent (HTTP 403). Unauthed
# calls are limited to 60/hr/IP, which is plenty for a once-a-day check.
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": f"Clawdmeter/{app_settings.APP_VERSION}",
    "X-GitHub-Api-Version": "2022-11-28",
}

CHECK_INTERVAL_SECONDS = 24 * 60 * 60   # only actually hit the network once/day
_INITIAL_DELAY_SECONDS = 8              # let the UI settle before the first check
_WAKE_SECONDS = 60                      # re-evaluate stop / manual request cadence


@dataclass(frozen=True)
class UpdateInfo:
    """A newer release worth telling the user about."""
    version: str        # normalized dotted version, e.g. "2.2.0"
    tag: str            # raw git tag, e.g. "v2.2.0"
    url: str            # release page (where the user downloads)
    notes: str          # release body markdown (may be "")
    asset_url: str      # browser_download_url of the .exe asset, or "" (Path-2 hook)
    sha256: str         # published SHA-256 of the .exe, or "" (Path-2 hook)


def parse_version(s: str) -> tuple[int, ...]:
    """'v2.10.1' -> (2, 10, 1). Leading v/V and any '-pre'/'+build' suffix are
    dropped; parsing stops at the first non-numeric component. Returns () for
    anything unparseable, which compares as the oldest possible version."""
    if not s:
        return ()
    s = str(s).strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    out: list[int] = []
    for part in re.split(r"[._]", s):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group()))
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    """True if version string `latest` is strictly newer than `current`.
    Numeric, component-wise compare — so v1.10.0 > v1.9.0 (a string compare
    would get that wrong) and 2.1 == 2.1.0."""
    lv, cv = parse_version(latest), parse_version(current)
    if not lv:
        return False
    width = max(len(lv), len(cv))
    lv += (0,) * (width - len(lv))
    cv += (0,) * (width - len(cv))
    return lv > cv


def extract_sha256(body: str, exe_name: str = "Clawdmeter.exe") -> str:
    """Pull a SHA-256 (64 hex chars) out of release notes. Prefers a line that
    also names the exe (e.g. 'Clawdmeter.exe: <hash>' or '<hash>  Clawdmeter.exe'
    as produced by build.ps1); otherwise falls back to the first hash found.
    Returns "" when there's no hash to find."""
    if not body:
        return ""
    pattern = re.compile(r"\b[0-9a-fA-F]{64}\b")
    for line in body.splitlines():
        if exe_name.lower() in line.lower():
            m = pattern.search(line)
            if m:
                return m.group().lower()
    m = pattern.search(body)
    return m.group().lower() if m else ""


def fetch_latest(timeout: float = 10.0) -> UpdateInfo | None:
    """Query GitHub's 'latest release' endpoint. Returns an UpdateInfo, or None
    on any network/parse error or if the endpoint hands back something unusable.
    Never raises — callers run on a background thread and just want a result or
    a None."""
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS,
                          follow_redirects=True) as http:
            resp = http.get(RELEASES_API)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None

    asset_url = ""
    exe_name = "Clawdmeter.exe"
    for asset in (data.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe"):
            asset_url = str(asset.get("browser_download_url") or "")
            exe_name = name
            break

    body = str(data.get("body") or "")
    normalized = ".".join(str(x) for x in parse_version(tag)) or tag.lstrip("vV")
    return UpdateInfo(
        version=normalized,
        tag=tag,
        url=str(data.get("html_url") or RELEASES_PAGE),
        notes=body,
        asset_url=asset_url,
        sha256=extract_sha256(body, exe_name),
    )


class UpdateChecker(QThread):
    """Background poll of GitHub Releases.

    Emits `update_available(UpdateInfo)` when a newer, non-skipped release is
    found. `check_finished(object)` fires after every *manual* check so the UI
    can confirm "you're up to date" (carries the UpdateInfo if one was found,
    else None). Mirrors UsagePoller's stop()/msleep loop so shutdown is prompt.
    """

    update_available = Signal(object)   # UpdateInfo
    check_finished = Signal(object)     # UpdateInfo | None (manual checks only)

    def __init__(self, interval_seconds: int = CHECK_INTERVAL_SECONDS,
                 parent=None) -> None:
        super().__init__(parent)
        self._interval = interval_seconds
        self._stop = False
        # A manual "Check for updates" bypasses both the once-a-day throttle and
        # the "skip this version" preference, and reports back via check_finished.
        self._force = False

    def stop(self) -> None:
        self._stop = True

    def request_check(self) -> None:
        """Ask for an immediate, user-initiated check (ignores throttle/skip)."""
        self._force = True

    def _due(self) -> bool:
        return (time.time() - app_settings.get_last_update_check()) >= self._interval

    def _run_check(self) -> None:
        manual = self._force
        self._force = False
        if not manual:
            if not app_settings.get_auto_check_updates():
                return
            if not self._due():
                return

        info = fetch_latest()
        if info is not None:
            app_settings.set_last_update_check(time.time())

        pending = None
        if info and is_newer(info.tag, app_settings.APP_VERSION):
            if manual or info.version != app_settings.get_skip_version():
                pending = info
                self.update_available.emit(info)

        if manual:
            # None => no update (UI says "up to date"); UpdateInfo => newer found.
            self.check_finished.emit(pending)

    def run(self) -> None:  # QThread entry
        for _ in range(_INITIAL_DELAY_SECONDS):
            if self._stop:
                return
            self.msleep(1000)

        while not self._stop:
            self._run_check()   # throttled internally; services a forced check
            for _ in range(_WAKE_SECONDS):
                if self._stop:
                    return
                if self._force:
                    break       # service the manual request at the top of the loop
                self.msleep(1000)
