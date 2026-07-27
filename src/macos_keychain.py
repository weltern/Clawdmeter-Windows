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
def _timeout_from_env() -> int | None:
    """Read CLAWD_KEYCHAIN_TIMEOUT, ignoring anything that isn't a number.

    This runs at import, and `poller` imports this module on every platform, so
    a typo like CLAWD_KEYCHAIN_TIMEOUT=off used to stop the app launching on
    Windows and Linux too — with a bare traceback and nothing to explain it.
    """
    raw = os.environ.get("CLAWD_KEYCHAIN_TIMEOUT")
    if not raw:
        return None
    try:
        secs = int(raw)
    except ValueError:
        return None                      # unparseable -> behave as if unset
    return secs if secs > 0 else None


_SECURITY_TIMEOUT_SECONDS = _timeout_from_env()

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


def _read_via_framework() -> str | None | bool:
    """Read the item in-process with SecItemCopyMatching (the Security framework).

    Preferred over shelling out to ``security`` for two reasons that the CLI
    cannot fix:

    * **The prompt names US.** macOS attributes a Keychain authorisation dialog
      to the calling process, so via the CLI the user is asked to approve
      *"security"* — a binary they have never heard of — rather than Clawdmeter.
    * **An "Always Allow" grant scopes to us.** Through the CLI the grant
      attaches to the shared ``/usr/bin/security``, so approving Clawdmeter
      silently hands every other script on the machine standing access to the
      same item.

    It also removes the subprocess entirely, and with it the whole class of bug
    where killing the child discards the user's grant.

    Returns the blob, None for "no credentials", or False when the Security
    binding isn't available so the caller can fall back to the CLI.
    """
    try:
        from Security import (  # noqa: PLC0415 - optional, macOS-only
            SecItemCopyMatching, kSecAttrService, kSecClass,
            kSecClassGenericPassword, kSecMatchLimit, kSecMatchLimitOne,
            kSecReturnData,
        )
    except Exception:   # noqa: BLE001 - pyobjc-framework-Security not bundled
        return False
    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service_name(),
        kSecReturnData: True,
        kSecMatchLimit: kSecMatchLimitOne,
    }
    try:
        status, data = SecItemCopyMatching(query, None)
    except Exception:   # noqa: BLE001 - never let a Keychain call crash the poll
        return None
    if status != 0 or data is None:
        # Non-zero: item not found (-25300), denied (-25308/-25293), etc. All of
        # them mean "no token this cycle" to callers.
        return None
    try:
        blob = bytes(data).decode("utf-8").strip()
    except (UnicodeDecodeError, TypeError):
        return None
    return blob or None


def read_credentials() -> str | None:
    """Return the raw credentials JSON blob from the login Keychain, or None.

    Uses the Security framework in-process where available and falls back to the
    ``security`` CLI otherwise. Returns None (never raises) when not on macOS,
    when the item isn't present, when access is denied, or when the item is
    empty — every failure mode a caller should treat as "no token here, move on".
    """
    if not is_macos():
        return None
    # Single-flight. The first read on a machine blocks on a Keychain dialog for
    # as long as the user takes; without this the 60s poll would queue another
    # request behind the one they are still reading. Applies to BOTH paths — the
    # framework call blocks on the same prompt the CLI does.
    if not _read_lock.acquire(blocking=False):
        return None
    try:
        blob = _read_via_framework()
        if blob is not False:            # the framework path handled it
            return blob
        return _read_via_cli()
    finally:
        _read_lock.release()


def _read_via_cli() -> str | None:
    """Fallback for builds without pyobjc-framework-Security: shell out to
    ``security``. Same result, but the authorisation dialog names *security*
    rather than Clawdmeter and an "Always Allow" grant attaches to that shared
    binary — see _read_via_framework. Caller holds _read_lock."""
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
        # Non-zero => item not found (44), access denied (45), or the OS needing
        # to prompt with no GUI session to prompt in (36 — see the constant).
        return None
    blob = (proc.stdout or "").strip()
    return blob or None
