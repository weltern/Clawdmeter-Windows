"""Read Claude Code's OAuth credentials from the macOS login Keychain.

On macOS the Claude Code CLI does **not** keep its OAuth token in
``~/.claude/.credentials.json`` the way it does on Windows and Linux — it stores
the exact same JSON blob as a *generic password* item in the login Keychain.
This module wraps Apple's ``security`` CLI so the poller and token-refresh code
can treat macOS the same way they treat the credentials file elsewhere: they
get back the raw JSON string and parse it with the shared helpers.

The stored blob is the JSON Claude Code writes, e.g.::

    {"claudeAiOauth": {"accessToken": "sk-ant-oat...",
                       "refreshToken": "sk-ant-ort...",
                       "expiresAt": 1712345678901, ...}}

so callers reuse ``poller._extract_access_token`` / ``token_refresh._oauth_block``
unchanged.

Read-only for now: writing the rotated token back into the Keychain (for
in-app token refresh) is a deliberate follow-up — see the note in
``token_refresh.refresh``. This module only ever *reads*.

Service name: Claude Code registers the item under the service
``Claude Code-credentials``. That's the default below; it can be overridden at
runtime with ``CLAUDE_KEYCHAIN_SERVICE`` (used while verifying the exact name on
a real install). Nothing here runs off macOS — every entry point short-circuits
via ``is_macos()`` — so importing this module is harmless on Windows/Linux.
"""

from __future__ import annotations

import os
import subprocess
import sys

# The generic-password service name Claude Code registers its credentials under.
# Overridable via env for verification against a real install without a rebuild.
DEFAULT_SERVICE_NAME = "Claude Code-credentials"

# Guard rail: the Keychain blob can be large-ish JSON, but a runaway read should
# never hang the poll thread. `security` returns promptly for a present item.
_SECURITY_TIMEOUT_SECONDS = 10


def is_macos() -> bool:
    """True only on macOS, where credentials live in the login Keychain."""
    return sys.platform == "darwin"


def service_name() -> str:
    """The Keychain service name to read, honoring the env override."""
    return os.environ.get("CLAUDE_KEYCHAIN_SERVICE") or DEFAULT_SERVICE_NAME


def read_credentials() -> str | None:
    """Return the raw credentials JSON blob from the login Keychain, or None.

    Returns None (never raises) when not on macOS, when the ``security`` tool is
    missing or times out, when the item isn't present, or when the item is
    empty — every failure mode a caller should treat as "no token here, move on".
    """
    if not is_macos():
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service_name(), "-w"],
            capture_output=True,
            text=True,
            timeout=_SECURITY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        # `security` not found, killed, or timed out — indistinguishable from
        # "no credentials" for our purposes.
        return None
    if proc.returncode != 0:
        # Non-zero => item not found (44) or access denied (45), etc.
        return None
    blob = (proc.stdout or "").strip()
    return blob or None
