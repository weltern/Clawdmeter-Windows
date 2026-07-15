"""Create/remove a Clawdmeter shortcut in the user's Start Menu Programs folder.

Modern Windows does not allow third-party Win32 apps to programmatically pin
items to Start. The supported route is: drop a .lnk in the user's Start Menu
Programs folder, then the user right-clicks it in Start and chooses Pin to
Start themselves.

We delegate the actual .lnk creation to PowerShell's WScript.Shell COM
binding to avoid pulling in pywin32 or hand-rolling IShellLink in ctypes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Clawdmeter.lnk"


def is_supported() -> bool:
    """Start-menu .lnk shortcuts (via PowerShell's WScript.Shell) are Windows-
    only. Off Windows the Settings UI disables the button."""
    return sys.platform == "win32"


def _start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def shortcut_path() -> Path:
    return _start_menu_dir() / SHORTCUT_NAME


def has_shortcut() -> bool:
    return shortcut_path().exists()


def _target_exe() -> Path:
    """Path to the running Clawdmeter executable (or python.exe in dev)."""
    return Path(sys.executable).resolve()


def _ps_quote(s: str) -> str:
    """Escape a string for a single-quoted PowerShell literal."""
    return s.replace("'", "''")


def create_shortcut() -> tuple[bool, str]:
    """Returns (success, message)."""
    target = _target_exe()
    sm_dir = _start_menu_dir()
    try:
        sm_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not create Start Menu folder: {exc}"

    sc = shortcut_path()
    script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s  = $ws.CreateShortcut('{_ps_quote(str(sc))}'); "
        f"$s.TargetPath = '{_ps_quote(str(target))}'; "
        f"$s.WorkingDirectory = '{_ps_quote(str(target.parent))}'; "
        f"$s.IconLocation = '{_ps_quote(str(target))}'; "
        f"$s.Description = 'Clawdmeter - Claude Code usage dashboard'; "
        f"$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return False, "powershell.exe not found"
    except subprocess.TimeoutExpired:
        return False, "PowerShell timed out"
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        return False, f"PowerShell failed: {stderr[:200] or exc.returncode}"

    if not sc.exists():
        return False, "Shortcut file not found after creation"
    return True, str(sc)


def remove_shortcut() -> tuple[bool, str]:
    sc = shortcut_path()
    try:
        sc.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Could not remove shortcut: {exc}"
    return True, str(sc)
