"""Persistent app settings. QSettings on Windows writes to HKCU\\Software\\Clawdmeter."""

from __future__ import annotations

from PySide6.QtCore import QSettings

ORG = "Clawdmeter"
APP = "Clawdmeter"
APP_VERSION = "1.1.1"

KEY_CRED_PATH = "credentials/path"
KEY_ALWAYS_ON_TOP = "window/always_on_top"
KEY_AUTO_HIDE_TITLEBAR = "window/auto_hide_titlebar"
KEY_QUIT_ON_CLOSE = "window/quit_on_close"
KEY_COMPACT_POS = "window/compact_pos"
KEY_AUTO_REFRESH = "token/auto_refresh"


def _settings() -> QSettings:
    return QSettings(ORG, APP)


def get_credentials_override() -> str:
    v = _settings().value(KEY_CRED_PATH, "")
    return str(v) if v else ""


def set_credentials_override(path: str) -> None:
    _settings().setValue(KEY_CRED_PATH, path or "")


def get_always_on_top() -> bool:
    v = _settings().value(KEY_ALWAYS_ON_TOP, False)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_always_on_top(on: bool) -> None:
    _settings().setValue(KEY_ALWAYS_ON_TOP, bool(on))


def get_auto_hide_titlebar() -> bool:
    v = _settings().value(KEY_AUTO_HIDE_TITLEBAR, False)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_auto_hide_titlebar(on: bool) -> None:
    _settings().setValue(KEY_AUTO_HIDE_TITLEBAR, bool(on))


def get_quit_on_close() -> bool:
    v = _settings().value(KEY_QUIT_ON_CLOSE, False)  # default: minimize to tray
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_quit_on_close(on: bool) -> None:
    _settings().setValue(KEY_QUIT_ON_CLOSE, bool(on))


def get_compact_pos() -> tuple[int, int] | None:
    """Last on-screen position of the compact widget, or None if never moved."""
    v = _settings().value(KEY_COMPACT_POS, "")
    if not v:
        return None
    try:
        x, y = str(v).split(",")
        return int(x), int(y)
    except (ValueError, TypeError):
        return None


def set_compact_pos(x: int, y: int) -> None:
    _settings().setValue(KEY_COMPACT_POS, f"{int(x)},{int(y)}")


def get_auto_refresh() -> bool:
    v = _settings().value(KEY_AUTO_REFRESH, True)  # beta: on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_auto_refresh(on: bool) -> None:
    _settings().setValue(KEY_AUTO_REFRESH, bool(on))
