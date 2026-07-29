"""macOS run-at-login via SMAppService — Apple's supported API since macOS 13.

Hand-writing a plist into ``~/Library/LaunchAgents`` still works, but it is the
old way and macOS treats it as such: the entry shows up in System Settings under
"Allow in the Background" as the raw label ``com.clawdmeter.startup`` with no
icon, and macOS posts a "Clawdmeter added items that can run in the background"
notification that reads like something went wrong.

``SMAppService.mainAppService`` registers the *app bundle itself*. It appears in
System Settings › General › Login Items as "Clawdmeter" with its icon under
"Open at Login", the user can toggle it there, and we can read back whether they
did. It also tracks the bundle rather than a path, so moving or replacing the
.app doesn't strand the login item the way a plist's hard-coded
``ProgramArguments`` does.

Verified on macOS 15.6.1 with an **ad-hoc signed** build (no Developer ID, and
outside /Applications): status went NotFound -> Enabled on register and back to
NotRegistered on unregister. Code signing is not a prerequisite, so this works
for the unsigned builds we ship today and keeps working once we notarize.

Frozen builds only. In a dev checkout there is no .app around the interpreter,
so ``mainAppService`` would resolve to Python's own framework bundle and
register *that*; ``run_at_startup`` falls back to the plist there.
"""

from __future__ import annotations

import sys

# SMAppServiceStatus. Enabled is the only value that means "will launch at
# login": RequiresApproval means we registered but the user switched it off in
# System Settings, and reporting that as on would be a lie the checkbox tells.
STATUS_NOT_REGISTERED = 0
STATUS_ENABLED = 1
STATUS_REQUIRES_APPROVAL = 2
STATUS_NOT_FOUND = 3

_STATUS_NAMES = {
    STATUS_NOT_REGISTERED: "not registered",
    STATUS_ENABLED: "enabled",
    STATUS_REQUIRES_APPROVAL: "requires approval",
    STATUS_NOT_FOUND: "not found",
}

_APPROVAL_HINT = (
    "macOS is holding the login item for approval. Open System Settings › "
    "General › Login Items & Extensions and switch Clawdmeter on under "
    "\"Open at Login\"."
)


def _service():
    """The SMAppService for this app bundle, or None where it doesn't apply.

    Everything is imported lazily and defensively: pyobjc's ServiceManagement
    bindings may not be bundled, and SMAppService itself doesn't exist before
    macOS 13. Either way the caller falls back to the LaunchAgent plist.
    """
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    try:
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()
    except Exception:      # noqa: BLE001 - missing binding or pre-13 macOS
        return None


def available() -> bool:
    """True when run-at-login should go through SMAppService on this build."""
    return _service() is not None


def status() -> int | None:
    """Current SMAppServiceStatus, or None if SMAppService doesn't apply."""
    svc = _service()
    if svc is None:
        return None
    try:
        return int(svc.status())
    except Exception:      # noqa: BLE001 - never let a status read crash Settings
        return None


def _svc_status(svc) -> int | None:
    """``svc.status()`` as an int, or None if the bridge call raises — the same
    guard ``status()`` applies, for callers that already hold the service. A raw
    ``int(svc.status())`` on the launch/migration path would otherwise crash the
    app before its window ever appears."""
    try:
        return int(svc.status())
    except Exception:      # noqa: BLE001 - never let a status read crash a caller
        return None


def is_enabled() -> bool:
    """True only when macOS will actually launch us at login."""
    return status() == STATUS_ENABLED


def register(*, interactive: bool = True) -> tuple[bool, str]:
    """Register the app as a login item. Returns (success, message).

    ``interactive`` says whether a person is watching. The approval hint below
    opens System Settings, which is right when they just ticked the checkbox and
    wrong when this is the silent upgrade migration — that runs before the app
    has a window, so the user would get System Settings in their face at every
    single launch, with nothing on screen to explain why.
    """
    svc = _service()
    if svc is None:
        return False, "SMAppService is unavailable"
    if _svc_status(svc) == STATUS_ENABLED:
        return True, "already registered"     # re-registering is pointless
    try:
        ok, err = svc.registerAndReturnError_(None)
    except Exception as exc:                  # noqa: BLE001
        return False, f"Could not register the login item: {exc}"
    if not ok:
        detail = err.localizedDescription() if err is not None else "unknown error"
        return False, f"Could not register the login item: {detail}"
    # A successful register can still land on RequiresApproval when the user has
    # previously switched us off in System Settings — macOS remembers that and
    # will not silently re-enable. Send them to the exact pane rather than
    # leaving a checkbox that ticks but does nothing.
    if _svc_status(svc) == STATUS_REQUIRES_APPROVAL:
        if interactive:
            _open_login_items_settings()
        return False, _APPROVAL_HINT
    return True, "registered as a login item"


def unregister() -> tuple[bool, str]:
    """Remove the login-item registration. Absent registration counts as done."""
    svc = _service()
    if svc is None:
        return False, "SMAppService is unavailable"
    try:
        ok, err = svc.unregisterAndReturnError_(None)
    except Exception as exc:                  # noqa: BLE001
        return False, f"Could not remove the login item: {exc}"
    if not ok:
        # Unregistering something that was never registered is the state the
        # caller asked for, so don't report it as a failure.
        if _svc_status(svc) in (STATUS_NOT_REGISTERED, STATUS_NOT_FOUND):
            return True, ""
        detail = err.localizedDescription() if err is not None else "unknown error"
        return False, f"Could not remove the login item: {detail}"
    return True, ""


def _open_login_items_settings() -> None:
    """Open System Settings on the Login Items pane (best effort)."""
    try:
        from ServiceManagement import SMAppService
        SMAppService.openSystemSettingsLoginItems()
    except Exception:      # noqa: BLE001 - a settings shortcut is never critical
        pass


def status_name() -> str:
    """Human-readable status, for logs and the diagnostics view."""
    st = status()
    return _STATUS_NAMES.get(st, "unavailable") if st is not None else "unavailable"
