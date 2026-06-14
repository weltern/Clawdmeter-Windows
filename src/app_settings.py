"""Persistent app settings. QSettings on Windows writes to HKCU\\Software\\Clawdmeter."""

from __future__ import annotations

from PySide6.QtCore import QSettings

ORG = "Clawdmeter"
APP = "Clawdmeter"
APP_VERSION = "1.5.0"

KEY_CRED_PATH = "credentials/path"
KEY_ALWAYS_ON_TOP = "window/always_on_top"
KEY_AUTO_HIDE_TITLEBAR = "window/auto_hide_titlebar"
KEY_QUIT_ON_CLOSE = "window/quit_on_close"
KEY_MINI_POS = "window/mini_pos"
KEY_COMPACT_POS = "window/compact_pos"
KEY_VIEW_MODE = "window/view_mode"
KEY_SHOW_MULTIPLE_SESSIONS = "sessions/show_multiple"
KEY_SHOW_SUBAGENTS = "sessions/show_subagents"
KEY_SHOW_TOKEN_USAGE = "tokens/show_usage"
KEY_AUTO_REFRESH = "token/auto_refresh"
KEY_POLL_INTERVAL = "poll/interval_seconds"
KEY_RESET_NOTIFY = "notify/reset_enabled"
KEY_RESET_NOTIFY_SOUND = "notify/reset_sound"
KEY_RESET_NOTIFY_POPUP = "notify/reset_popup"
KEY_RESET_NOTIFY_PUSH = "notify/reset_push"
KEY_RESET_NOTIFY_PUSH_PROVIDER = "notify/reset_push_provider"
KEY_RESET_NOTIFY_PUSH_TOPIC = "notify/reset_push_topic"
KEY_RESET_NOTIFY_PUSH_TG_TOKEN = "notify/reset_push_tg_token"
KEY_RESET_NOTIFY_PUSH_TG_CHAT = "notify/reset_push_tg_chat"

PUSH_PROVIDERS = ("ntfy", "telegram")

# API usage poll cadence (seconds). The floor keeps the self-billed 1-token
# probe from tripping per-minute rate limits; the ceiling keeps the usage %
# from going too stale and the per-cycle token-refresh check from lagging.
POLL_INTERVAL_MIN = 10
POLL_INTERVAL_MAX = 600
POLL_INTERVAL_DEFAULT = 60


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


def get_mini_pos() -> tuple[int, int] | None:
    """Last on-screen position of the mini widget, or None if never moved."""
    v = _settings().value(KEY_MINI_POS, "")
    if not v:
        return None
    try:
        x, y = str(v).split(",")
        return int(x), int(y)
    except (ValueError, TypeError):
        return None


def set_mini_pos(x: int, y: int) -> None:
    _settings().setValue(KEY_MINI_POS, f"{int(x)},{int(y)}")


def get_compact_pos() -> tuple[int, int] | None:
    """Last on-screen position of the compact (list) window, or None."""
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


def get_view_mode() -> str:
    """Last-used view mode: 'full', 'compact', or 'mini' (defaults to full)."""
    v = _settings().value(KEY_VIEW_MODE, "full")
    v = str(v).lower()
    return v if v in ("full", "compact", "mini") else "full"


def set_view_mode(mode: str) -> None:
    if mode in ("full", "compact", "mini"):
        _settings().setValue(KEY_VIEW_MODE, mode)


def get_show_multiple_sessions() -> bool:
    v = _settings().value(KEY_SHOW_MULTIPLE_SESSIONS, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_show_multiple_sessions(on: bool) -> None:
    _settings().setValue(KEY_SHOW_MULTIPLE_SESSIONS, bool(on))


def get_show_subagents() -> bool:
    v = _settings().value(KEY_SHOW_SUBAGENTS, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_show_subagents(on: bool) -> None:
    _settings().setValue(KEY_SHOW_SUBAGENTS, bool(on))


def get_show_token_usage() -> bool:
    v = _settings().value(KEY_SHOW_TOKEN_USAGE, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_show_token_usage(on: bool) -> None:
    _settings().setValue(KEY_SHOW_TOKEN_USAGE, bool(on))


def get_auto_refresh() -> bool:
    v = _settings().value(KEY_AUTO_REFRESH, True)  # beta: on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_auto_refresh(on: bool) -> None:
    _settings().setValue(KEY_AUTO_REFRESH, bool(on))


def _clamp_poll_interval(value: int) -> int:
    return max(POLL_INTERVAL_MIN, min(POLL_INTERVAL_MAX, value))


def get_poll_interval() -> int:
    """Seconds between API usage polls. QSettings hands values back as strings
    on Windows, so parse defensively and clamp into [MIN, MAX]; fall back to
    the default on anything unparseable."""
    raw = _settings().value(KEY_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
    try:
        secs = int(raw)
    except (TypeError, ValueError):
        return POLL_INTERVAL_DEFAULT
    return _clamp_poll_interval(secs)


def set_poll_interval(seconds: int) -> int:
    """Clamp to [MIN, MAX], persist, and return the value actually stored."""
    clamped = _clamp_poll_interval(int(seconds))
    _settings().setValue(KEY_POLL_INTERVAL, clamped)
    return clamped


def get_reset_notify() -> bool:
    v = _settings().value(KEY_RESET_NOTIFY, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_reset_notify(on: bool) -> None:
    _settings().setValue(KEY_RESET_NOTIFY, bool(on))


def get_reset_notify_sound() -> bool:
    v = _settings().value(KEY_RESET_NOTIFY_SOUND, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_reset_notify_sound(on: bool) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_SOUND, bool(on))


def get_reset_notify_popup() -> bool:
    v = _settings().value(KEY_RESET_NOTIFY_POPUP, True)  # on by default
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_reset_notify_popup(on: bool) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_POPUP, bool(on))


def get_reset_notify_push() -> bool:
    v = _settings().value(KEY_RESET_NOTIFY_PUSH, False)  # off until a topic is set
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def set_reset_notify_push(on: bool) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_PUSH, bool(on))


def get_reset_notify_push_provider() -> str:
    v = _settings().value(KEY_RESET_NOTIFY_PUSH_PROVIDER, "ntfy")
    s = str(v).lower() if v else "ntfy"
    return s if s in PUSH_PROVIDERS else "ntfy"


def set_reset_notify_push_provider(provider: str) -> None:
    p = (provider or "ntfy").lower()
    _settings().setValue(
        KEY_RESET_NOTIFY_PUSH_PROVIDER, p if p in PUSH_PROVIDERS else "ntfy"
    )


def get_reset_notify_push_topic() -> str:
    v = _settings().value(KEY_RESET_NOTIFY_PUSH_TOPIC, "")
    return str(v) if v else ""


def set_reset_notify_push_topic(topic: str) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_PUSH_TOPIC, (topic or "").strip())


def get_reset_notify_push_tg_token() -> str:
    v = _settings().value(KEY_RESET_NOTIFY_PUSH_TG_TOKEN, "")
    return str(v) if v else ""


def set_reset_notify_push_tg_token(token: str) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_PUSH_TG_TOKEN, (token or "").strip())


def get_reset_notify_push_tg_chat() -> str:
    v = _settings().value(KEY_RESET_NOTIFY_PUSH_TG_CHAT, "")
    return str(v) if v else ""


def set_reset_notify_push_tg_chat(chat: str) -> None:
    _settings().setValue(KEY_RESET_NOTIFY_PUSH_TG_CHAT, (chat or "").strip())
