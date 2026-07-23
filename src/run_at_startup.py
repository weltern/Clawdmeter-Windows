"""Launch Clawdmeter automatically at sign-in.

Windows uses the per-user Run key — HKCU\\Software\\Microsoft\\Windows\\
CurrentVersion\\Run — which is the supported, no-admin way to auto-start a
desktop app for the current user. The registry value (not a QSettings key) is
the single source of truth for whether the feature is on, mirroring how
``start_menu`` treats the shortcut file: the Settings checkbox just reflects
what's actually registered.

Linux uses the freedesktop XDG autostart spec — a ``clawdmeter.desktop`` entry
under ``$XDG_CONFIG_HOME/autostart`` (``~/.config/autostart``) — which every
major desktop (GNOME, KDE, XFCE, …) honors at login. The presence of that file
is the single source of truth there. The Windows code paths below are unchanged
from the Windows-only version; the Linux branches are purely additive.

macOS uses a per-user LaunchAgent — a ``com.clawdmeter.startup.plist`` under
``~/Library/LaunchAgents`` with ``RunAtLoad`` — which ``launchd`` runs at login.
As with Linux, the presence of that plist is the single source of truth; the
macOS branches are purely additive and mirror the Linux ones. (Writing the file
is enough for the *next* login — we don't ``launchctl load`` it, matching the
file-presence model and avoiding cross-version ``launchctl`` quirks.)

The stored command points at the running executable plus ``--startup`` so the
login launch goes straight to the tray instead of popping the window open.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

try:
    import winreg  # Windows-only; None everywhere else.
except ImportError:  # pragma: no cover - non-Windows safety net
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Clawdmeter"

# Passed on the registered command line; main.py reads it to start hidden.
STARTUP_FLAG = "--startup"

# XDG autostart entry written on Linux.
DESKTOP_FILE_NAME = "clawdmeter.desktop"

# macOS LaunchAgent label + plist filename (filename convention is <label>.plist).
LAUNCH_AGENT_LABEL = "com.clawdmeter.startup"
PLIST_FILE_NAME = f"{LAUNCH_AGENT_LABEL}.plist"


def _is_linux() -> bool:
    """True on any Linux platform, where XDG autostart applies."""
    return sys.platform.startswith("linux")


def _is_macos() -> bool:
    """True on macOS, where a LaunchAgent plist drives run-at-login."""
    return sys.platform == "darwin"


def is_supported() -> bool:
    """True where run-at-login is implemented: Windows, Linux (XDG), macOS (LaunchAgent)."""
    if _is_linux() or _is_macos():
        return True
    return winreg is not None


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


# --- Linux XDG autostart helpers ------------------------------------------

def _autostart_dir() -> Path:
    """The XDG autostart directory: ``$XDG_CONFIG_HOME/autostart`` or ~/.config."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart"


def _desktop_file_path() -> Path:
    """Full path to the ``clawdmeter.desktop`` autostart entry."""
    return _autostart_dir() / DESKTOP_FILE_NAME


def _linux_exec() -> str:
    """The POSIX ``Exec=`` command line, fully quoted, plus ``--startup``.

    Mirrors ``launch_command``'s frozen-vs-dev split: a frozen build execs
    itself; a dev checkout execs the interpreter plus main.py. For an AppImage,
    ``sys.executable`` is the ephemeral ``/tmp/.mount_*`` squashfs path that no
    longer exists at the next login, so prefer ``$APPIMAGE`` (the stable path to
    the .AppImage file that the runtime exports) when it is set. A plain
    PyInstaller onefile has a stable ``sys.executable`` and no ``$APPIMAGE``.
    """
    if getattr(sys, "frozen", False):
        exe = os.environ.get("APPIMAGE") or sys.executable
        return f'"{exe}" {STARTUP_FLAG}'
    exe = sys.executable
    script = Path(__file__).resolve().parent / "main.py"
    return f'"{exe}" "{script}" {STARTUP_FLAG}'


def _desktop_entry() -> str:
    """The full text of the autostart .desktop file."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Clawdmeter\n"
        "Comment=Claude Code usage dashboard\n"
        f'Exec={_linux_exec()}\n'
        "Icon=clawdmeter\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _linux_is_enabled() -> bool:
    return _desktop_file_path().exists()


def _linux_enable() -> tuple[bool, str]:
    """Write (or refresh) the autostart .desktop entry. Returns (success, message)."""
    path = _desktop_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_desktop_entry(), encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write the startup entry: {exc}"
    return True, _linux_exec()


def _linux_disable() -> tuple[bool, str]:
    """Remove the autostart entry. Treats an already-absent file as success."""
    try:
        _desktop_file_path().unlink()
    except FileNotFoundError:
        return True, ""  # file doesn't exist -> nothing to remove
    except OSError as exc:
        return False, f"Could not remove the startup entry: {exc}"
    return True, ""


def _linux_sync_if_enabled() -> None:
    """If autostart is on, rewrite ``Exec=`` to the current binary location.

    An AppImage gets moved or replaced on update; re-pointing the entry on each
    frozen launch keeps it from going stale. Restricted to frozen builds so a
    dev run never overwrites a real user's entry with a python path.
    """
    if getattr(sys, "frozen", False) and _linux_is_enabled():
        _linux_enable()


# --- macOS LaunchAgent helpers ---------------------------------------------

def _launch_agents_dir() -> Path:
    """The per-user LaunchAgents directory: ``~/Library/LaunchAgents``."""
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    """Full path to the ``com.clawdmeter.startup.plist`` LaunchAgent."""
    return _launch_agents_dir() / PLIST_FILE_NAME


def _macos_program_arguments() -> list[str]:
    """``ProgramArguments`` for the LaunchAgent, as a list (no shell quoting).

    Mirrors ``launch_command``'s frozen-vs-dev split. Frozen build (inside a
    ``Clawdmeter.app``): ``sys.executable`` is the binary under
    ``Contents/MacOS`` — exec it directly with ``--startup``. Dev checkout: the
    interpreter plus ``main.py``. launchd passes these as an argv array, so
    spaces in paths need no quoting.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, STARTUP_FLAG]
    script = str(Path(__file__).resolve().parent / "main.py")
    return [sys.executable, script, STARTUP_FLAG]


def _plist_contents() -> str:
    """The full XML text of the LaunchAgent plist."""
    args_xml = "".join(
        f"        <string>{_xml_escape(a)}</string>\n"
        for a in _macos_program_arguments()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{LAUNCH_AGENT_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{args_xml}"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        # Interactive: this is a GUI menu-bar app, not a background daemon.
        "    <key>ProcessType</key>\n"
        "    <string>Interactive</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _macos_is_enabled() -> bool:
    return _plist_path().exists()


def _macos_enable() -> tuple[bool, str]:
    """Write (or refresh) the LaunchAgent plist. Returns (success, message)."""
    path = _plist_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_plist_contents(), encoding="utf-8")
    except OSError as exc:
        return False, f"Could not write the startup entry: {exc}"
    return True, " ".join(_macos_program_arguments())


def _macos_disable() -> tuple[bool, str]:
    """Remove the LaunchAgent plist. Treats an already-absent file as success."""
    try:
        _plist_path().unlink()
    except FileNotFoundError:
        return True, ""  # file doesn't exist -> nothing to remove
    except OSError as exc:
        return False, f"Could not remove the startup entry: {exc}"
    return True, ""


def _macos_sync_if_enabled() -> None:
    """If autostart is on, rewrite the plist to the current binary location.

    A ``.app`` gets moved or replaced on update; re-pointing the plist on each
    frozen launch keeps ``ProgramArguments`` from going stale. Frozen-only so a
    dev run never overwrites a real user's plist with a python path.
    """
    if getattr(sys, "frozen", False) and _macos_is_enabled():
        _macos_enable()


# --- Public, platform-dispatching API --------------------------------------

def is_enabled() -> bool:
    if _is_linux():
        return _linux_is_enabled()
    if _is_macos():
        return _macos_is_enabled()
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
    if _is_linux():
        return _linux_enable()
    if _is_macos():
        return _macos_enable()
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
    if _is_linux():
        return _linux_disable()
    if _is_macos():
        return _macos_disable()
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
    """If startup is on, rewrite the entry to the current executable location.

    A loose .exe gets replaced in place on update, or moved — re-pointing the
    entry on each frozen launch keeps it from going stale. Restricted to frozen
    builds so a dev run never overwrites a real user's entry with a python path.
    """
    if _is_linux():
        _linux_sync_if_enabled()
        return
    if _is_macos():
        _macos_sync_if_enabled()
        return
    if getattr(sys, "frozen", False) and is_enabled():
        enable()
