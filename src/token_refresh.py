"""OAuth access-token refresh for Clawdmeter-Windows  (BETA).

Claude Code OAuth access tokens live ~8 hours. When one expires the usage API
returns 401 and the dashboard goes blank. This module refreshes the access
token using the stored refresh token and writes the rotated tokens back into
the credentials file (the same `~/.claude/.credentials.json` Claude Code uses).

SAFETY (the failsafe):
  * A refresh is only attempted when the token is actually expired (+ a small
    skew), so we never hammer the endpoint.
  * Before writing, the current credentials are copied to a `.clawdmeter-bak`
    backup; it is deleted once the write is verified good (so the old plaintext
    tokens don't linger on disk) and kept only if the write/revert failed.
  * The write is atomic: temp file + os.replace (no half-written file).
  * After writing we re-read and validate; if anything is wrong we restore the
    backup automatically.
  * The OAuth token endpoint throttles hard (HTTP 429) — callers must back off.

Limitation: revert restores the *file*. A refresh that already succeeded has
rotated the token server-side, so revert protects file integrity, not the
server rotation. The backup + `claude /login` remain the ultimate recovery.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
# Public Claude Code OAuth client id (the same value Claude Code itself uses).
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_HEADERS = {"Content-Type": "application/json", "User-Agent": "anthropic"}

EXPIRY_SKEW_SECONDS = 120  # treat the token as expired this many seconds early
BACKUP_SUFFIX = ".clawdmeter-bak"


@dataclass
class RefreshResult:
    ok: bool
    status: str                    # human-readable, for the UI
    http_status: int | None = None
    new_expiry_ms: int | None = None
    reverted: bool = False


def _oauth_block(data: dict) -> dict | None:
    """Return the dict holding the Claude Code accessToken/refreshToken."""
    if isinstance(data.get("claudeAiOauth"), dict):
        return data["claudeAiOauth"]
    if isinstance(data.get("refreshToken"), str):
        return data
    for v in data.values():
        if isinstance(v, dict) and isinstance(v.get("refreshToken"), str):
            return v
    return None


def token_expiry_ms(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    blk = _oauth_block(data)
    if blk and isinstance(blk.get("expiresAt"), (int, float)):
        return int(blk["expiresAt"])
    return None


def is_expired(path: Path, skew_seconds: int = EXPIRY_SKEW_SECONDS) -> bool:
    exp = token_expiry_ms(path)
    if exp is None:
        return False  # unknown expiry -> don't trigger a refresh
    return time.time() * 1000 >= (exp - skew_seconds * 1000)


def _backup_path(path: Path) -> Path:
    return Path(str(path) + BACKUP_SUFFIX)


def _atomic_write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_tokens_safely(path: Path, original_raw: str, data: dict,
                         expected_access: str) -> RefreshResult:
    """Backup -> atomic write -> verify -> revert on any failure."""
    backup = _backup_path(path)
    try:
        backup.write_text(original_raw, encoding="utf-8")
    except OSError as exc:
        return RefreshResult(False, f"Could not write backup, aborting: {exc}", 200)

    try:
        _atomic_write(path, data)
        check = json.loads(path.read_text(encoding="utf-8"))
        blk = _oauth_block(check)
        if not blk or blk.get("accessToken") != expected_access:
            raise ValueError("post-write verification mismatch")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        try:
            path.write_text(original_raw, encoding="utf-8")
            return RefreshResult(
                False, f"Write failed, reverted from backup ({exc})", 200, reverted=True
            )
        except OSError as exc2:
            return RefreshResult(
                False, f"Write failed AND revert failed ({exc2}) — backup at {backup}", 200
            )
    # Verified-good write — drop the plaintext backup of the now-stale old tokens.
    try:
        backup.unlink()
    except OSError:
        pass
    return RefreshResult(True, "Token refreshed", 200)


def refresh(path: Path, *, timeout: float = 20.0) -> RefreshResult:
    """Refresh the access token in `path`. Safe: backs up + reverts on failure."""
    try:
        original_raw = path.read_text(encoding="utf-8")
        data = json.loads(original_raw)
    except (OSError, json.JSONDecodeError) as exc:
        return RefreshResult(False, f"Cannot read credentials: {exc}")

    blk = _oauth_block(data)
    if not blk or not isinstance(blk.get("refreshToken"), str):
        return RefreshResult(False, "No refresh token found in credentials")
    refresh_tok = blk["refreshToken"]

    # httpx imported lazily so this module stays importable/testable without it.
    import httpx

    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_tok,
        "client_id": CLIENT_ID,
    }
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(OAUTH_TOKEN_URL, headers=REFRESH_HEADERS, json=body)
    except httpx.HTTPError as exc:
        return RefreshResult(False, f"Refresh request failed: {exc}")

    if resp.status_code == 429:
        return RefreshResult(False, "Rate limited by token endpoint — backing off", 429)
    if resp.status_code != 200:
        return RefreshResult(
            False, f"Refresh rejected (HTTP {resp.status_code}) — re-login may be needed",
            resp.status_code,
        )

    try:
        tok = resp.json()
        new_access = tok["access_token"]
        expires_in = int(tok["expires_in"])
    except (ValueError, KeyError, TypeError) as exc:
        return RefreshResult(False, f"Unexpected refresh response: {exc}", 200)

    blk["accessToken"] = new_access
    blk["refreshToken"] = tok.get("refresh_token") or refresh_tok
    new_expiry_ms = int(time.time() * 1000) + expires_in * 1000
    blk["expiresAt"] = new_expiry_ms

    result = _write_tokens_safely(path, original_raw, data, new_access)
    if result.ok:
        result.new_expiry_ms = new_expiry_ms
    return result
