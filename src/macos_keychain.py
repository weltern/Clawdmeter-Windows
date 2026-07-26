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
import threading

# The generic-password service name Claude Code registers its credentials under.
# Overridable via env for verification against a real install without a rebuild.
DEFAULT_SERVICE_NAME = "Claude Code-credentials"

# NO timeout by default, and that is deliberate.
#
# The first read on a machine pops a Keychain authorization dialog, and
# `security` blocks until it is answered. If we kill it first, SecurityAgent has
# no live client to hand the approval to: it discards the grant and re-presents
# the prompt, so the user can NEVER authorise the app and the dashboard stays
# empty forever. Measured on real hardware 2026-07-26 — with a 10s limit, four
# "Allow" clicks and two "Always Allow" clicks produced zero ACL grants; raising
# it to 120s and answering at 177s failed identically.
#
# Any fixed number here is a guess about how fast a human reads a security
# dialog they have never seen before, so there is no right value — the timeout
# itself was the bug. Real apps do not hit this because the framework API blocks
# for as long as the prompt is up; the fuse was an artifact of shelling out to a
# subprocess. Waiting is safe: this runs on a QThread worker (never the UI
# thread), only one read is ever in flight (see the lock below), `security`
# exits as soon as the dialog is answered OR cancelled, and once the grant is
# stored the call returns in milliseconds forever after.
#
# CLAWD_KEYCHAIN_TIMEOUT sets a limit in seconds for tests and for anyone who
# wants a hard bound. Unset = wait.
_SECURITY_TIMEOUT_SECONDS = (
    int(os.environ["CLAWD_KEYCHAIN_TIMEOUT"])
    if os.environ.get("CLAWD_KEYCHAIN_TIMEOUT") else None
)

# `security`'s exit code for errSecInteractionNotAllowed (-25308 & 0xFF): the OS
# needed to prompt but could not — no GUI session (SSH, or a LaunchAgent running
# before login). Distinct from 44 (item not found), though both mean "no token
# this cycle" to callers.
RC_INTERACTION_NOT_ALLOWED = 36

# Only ever one `security` in flight. Without this, a poll every 60s stacks a new
# authorization request behind the dialog the user is still reading.
_read_lock = threading.Lock()


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
    # Single-flight. The first read on a machine blocks on a Keychain dialog for
    # as long as the user takes; without this the 60s poll would queue another
    # request behind the one they are still reading.
    if not _read_lock.acquire(blocking=False):
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
    finally:
        _read_lock.release()
    if proc.returncode != 0:
        # Non-zero => item not found (44), access denied (45), or the OS needing
        # to prompt with no GUI session to prompt in (36 — see the constant).
        return None
    blob = (proc.stdout or "").strip()
    return blob or None
