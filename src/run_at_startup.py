"""Launch Clawdmeter automatically at Windows sign-in.

Uses the per-user Run key — HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\
Run — which is the supported, no-admin way to auto-start a desktop app for the
current user. The registry value (not a QSettings key) is the single source of
truth for whether the feature is on, mirroring how ``start_menu`` treats the
shortcut file: the Settings checkbox just reflects what's actually registered.

The stored command points at the running executable plus ``--startup`` so the
login launch goes straight to the tray instead of popping the window open.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import winreg  # Windows-only; the whole app is Windows-only.
except ImportError:  # pragma: no cover - non-Windows safety net
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Clawdmeter"

# Passed on the registered command line; main.py reads it to start hidden.
STARTUP_FLAG = "--startup"


def is_supported() -> bool:
    """True on Windows, where the Run key exists."""
    return winreg is not None and sys.platform == "win32"


def launch_command() -> str:
    """The command Windows should run at sign-in, fully quoted.

    Frozen (PyInstaller) build: the .exe itself. Dev checkout: the interpreter
    plus main.py, so running from source still auto-starts sensibly.
    """
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{exe}" {STARTUP_FLAG}'
    script = Path(__file__).resolve().parent / "main.py"
    return f'"{exe}" "{script}" {STARTUP_FLAG}'


def is_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:  # key or value absent
        return False


def enable() -> tuple[bool, str]:
    """Register (or refresh) the startup entry. Returns (success, message)."""
    if winreg is None:
        return False, "Run-at-startup is only supported on Windows."
    cmd = launch_command()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    except OSError as exc:
        return False, f"Could not write the startup entry: {exc}"
    return True, cmd


def disable() -> tuple[bool, str]:
    """Remove the startup entry. Treats an already-absent value as success."""
    if winreg is None:
        return False, "Run-at-startup is only supported on Windows."
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return True, ""  # key doesn't exist -> nothing to remove
    except OSError as exc:
        # DeleteValue raises FileNotFoundError when the value is missing; any
        # other OSError is a real failure.
        if getattr(exc, "winerror", None) == 2:  # ERROR_FILE_NOT_FOUND
            return True, ""
        return False, f"Could not remove the startup entry: {exc}"
    return True, ""


def sync_if_enabled() -> None:
    """If startup is on, rewrite the entry to the current .exe location.

    A loose .exe gets replaced in place on update, or moved — re-pointing the
    entry on each frozen launch keeps it from going stale. Restricted to frozen
    builds so a dev run never overwrites a real user's entry with a python path.
    """
    if getattr(sys, "frozen", False) and is_enabled():
        enable()
