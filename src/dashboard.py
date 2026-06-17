"""Main dashboard window for Clawdmeter-Windows.

Frameless top-level window with a custom title bar (drag-to-move, in-app
min/max/close buttons), a sprite player driven by Claude usage rate, and a
slide-in settings panel on the right.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QPoint,
    QRect,
    QRegularExpression,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import app_settings
import start_menu
import token_refresh
import winutil
from mood import GROUP_ANIMS, GROUP_NAMES, RateGroupTracker
from poller import UsagePoller, UsageSample, credentials_path, DEFAULT_CREDENTIALS_PATH
import remote_notify
import stats
from statviz import DailyBars, Heatmap, ModelBreakdown, PercentBars
from usage_history import UsageHistory
from reset_notify import ResetDecision, ResetNotifier
import update_check
from update_check import UpdateChecker
from session_shelf import (
    CompactView, SessionShelf, UsageBar, apply_overage_bar,
)
from sprite_player import SpritePlayer, assets_root
from transcript import (
    ACTIVITY_ANIMS,
    ACTIVITY_LABELS,
    Activity as TranscriptActivity,
    AgentState as TranscriptAgentState,
    TokenUsage,
    TranscriptState,
    TranscriptWatcher,
    account_window_tokens,
    fmt_tokens,
)
from uiutil import format_minutes as _format_minutes, heat as _heat


# Stable tile id used in single-mascot mode (Settings: show multiple sessions
# off) so the one tile updates in place rather than animating a swap when the
# focused session changes.
_SINGLE_TILE_ID = "__single__"


def _view_states(raw, show_multiple, show_subagents, single_id=_SINGLE_TILE_ID):
    """Apply the Settings session-view toggles to the watcher's raw states.

    - show_subagents off -> strip each session's child agents.
    - show_multiple off  -> keep only the focused (newest) session, re-keyed to a
      stable id so its tile updates in place instead of animating a swap when the
      focused session changes.
    Pure (returns a new list) so it's testable without the Qt UI.
    """
    states = raw
    if not show_subagents:
        states = [replace(s, agents=[]) for s in states]
    if not show_multiple and states:
        states = [replace(states[0], session_id=single_id)]
    return states


def _should_release_autofit(height_changed, fitting, armed, max_involved, titlebar_animating):
    """Decide whether a resize is a genuine user height-drag (so we should stop
    auto-fitting the window height). True only when the height actually changed
    and it wasn't one of OUR programmatic resizes — the fit animation (`fitting`),
    a maximize/restore (`max_involved`), or the auto-hide title-bar animation —
    and only after the first show has settled (`armed`)."""
    return (
        height_changed
        and armed
        and not fitting
        and not max_involved
        and not titlebar_animating
    )


# Valid view modes, largest -> smallest.
VIEW_ORDER = ("full", "compact", "mini")


STYLESHEET = """
QWidget#root {
    background-color: #0e1116;
    border: 1px solid #1f2937;
}

QWidget#titleBar { background-color: #0a0d12; }
QLabel#titleAppName {
    font-size: 12px; color: #e6edf3; font-weight: 600; letter-spacing: 1px;
}
QToolButton#titleBtn, QToolButton#closeBtn, QToolButton#settingsBtn {
    background: transparent; color: #CE7D6B; border: 0;
    min-width: 38px; min-height: 30px;
    font-family: "Font Awesome 6 Free"; font-weight: 900;
}
QToolButton#titleBtn, QToolButton#closeBtn { font-size: 13px; }
QToolButton#settingsBtn { font-size: 15px; }
QToolButton#titleBtn:hover, QToolButton#settingsBtn:hover { background-color: #1f2937; color: #CE7D6B; }
QToolButton#closeBtn:hover { background-color: #c13434; color: #ffffff; }

QLabel#title { font-size: 22px; font-weight: 700; letter-spacing: 1px; color: #e6edf3; }
QLabel#group { font-size: 13px; font-weight: 600; color: #9ca3af; letter-spacing: 2px; }
QLabel#rowLabel { font-size: 14px; color: #9ca3af; }
QLabel#pct { font-size: 40px; font-weight: 700; color: #e6edf3; }
QLabel#reset { font-size: 12px; color: #9ca3af; }
QLabel#statusText { font-size: 12px; font-weight: 600; }
QLabel#statusText[level="warn"] { color: #f59e0b; }
QLabel#statusText[level="block"] { color: #dc2626; }
QLabel#statusIcon { font-size: 14px; font-family: "Segoe UI Emoji"; }

QPushButton {
    background-color: #1f2937; color: #e6edf3; border: 1px solid #374151;
    padding: 6px 12px; border-radius: 6px;
}
QPushButton:hover { background-color: #374151; }
QPushButton:disabled { background-color: #161b22; color: #4b5563; border-color: #21262d; }

QWidget#settingsPanel {
    background-color: #0a0d12;
}
/* Left tab rail in the settings page (sits right of the app nav rail). */
QWidget#settingsNav {
    background-color: #0e1116;
    border-right: 1px solid #1f2937;
}
/* QPushButton (not QToolButton) so QSS text-align actually left-aligns the
   glyph+label. Segoe UI is primary so the Latin label stays crisp — FA Free
   ships its own (ugly) Latin, so listing it first would hijack the words. The
   leading FA glyph isn't in Segoe UI, so Qt falls back to Font Awesome for it.
   FA is registered at startup in main.py via QFontDatabase. */
QPushButton#navBtn {
    background: transparent; color: #9ca3af; border: 0;
    border-radius: 6px; padding: 9px 14px;
    text-align: left; font-size: 13px; font-weight: 600;
    font-family: "Segoe UI", "Font Awesome 6 Free";
}
QPushButton#navBtn:hover { background-color: #1f2937; color: #e6edf3; }
QPushButton#navBtn:checked { background-color: #1f2937; color: #CE7D6B; }
QScrollArea#settingsScroll, QWidget#settingsBody { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #374151; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4b5563; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QLabel#settingsTitle {
    font-size: 16px; font-weight: 700; color: #e6edf3; letter-spacing: 2px;
}
QLabel#sectionLabel {
    font-size: 10px; color: #6b7280; letter-spacing: 2px; font-weight: 600;
}
QLabel#pathDisplay {
    font-size: 10px; color: #9ca3af;
    background: #0e1116; border: 1px solid #1f2937; border-radius: 4px;
    padding: 8px;
}
QLabel#credStatus { font-size: 10px; color: #6b7280; }
QLabel#sectionHint { font-size: 10px; color: #6b7280; }
QLabel#pollNote { font-size: 10px; color: #f59e0b; font-weight: 600; }

/* Stats page cards */
QFrame#statCard {
    background-color: #0e1116; border: 1px solid #1f2937; border-radius: 8px;
}
QLabel#statLabel { font-size: 10px; color: #6b7280; letter-spacing: 2px; font-weight: 600; }
QLabel#statBig { font-size: 32px; font-weight: 700; color: #e6edf3; }
QPushButton#resetLink {
    background: transparent; color: #9ca3af; border: 0; padding: 2px 4px;
    text-decoration: underline; font-size: 10px;
}
QPushButton#resetLink:hover { color: #e6edf3; }
QCheckBox { color: #e6edf3; font-size: 12px; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #374151;
    background-color: #1f2937; border-radius: 2px;
}
QCheckBox::indicator:hover { border-color: #6b7280; }
QCheckBox::indicator:checked {
    background-color: #CE7D6B; border-color: #CE7D6B;
    image: none;
}

/* Slim left nav rail (overlay). Same icon+label language as the settings tabs:
   Segoe UI primary so labels stay crisp; the leading FA glyph falls back to FA.
   Labels are clipped while the rail is collapsed and revealed as it expands. */
QWidget#navRail {
    background-color: #0e1116;
    border-right: 1px solid #1f2937;
}
QPushButton#railBtn {
    background: transparent; color: #9ca3af; border: 0;
    border-radius: 6px; padding: 9px 0px 9px 8px;  /* no right pad: icon never clips,
                                                       and stays put as the rail widens */
    text-align: left; font-size: 15px; font-weight: 600;
    font-family: "Segoe UI", "Font Awesome 6 Free";
}
QPushButton#railBtn:hover { background-color: #1f2937; color: #e6edf3; }
QPushButton#railBtn:checked { background-color: #1f2937; color: #CE7D6B; }

/* Push-notification channel cards (Settings -> Notifications). */
QWidget#pushCard {
    background-color: #0e1116; border: 1px solid #1f2937; border-radius: 6px;
}
QLabel#pushSummary { font-size: 12px; }
QToolButton#pushEditBtn {
    background: transparent; color: #9ca3af; border: 0;
    padding: 2px 6px; border-radius: 4px; font-size: 11px;
}
QToolButton#pushEditBtn:hover { color: #CE7D6B; background-color: #1f2937; }
QToolButton#pushRemoveBtn {
    background: transparent; color: #6b7280; border: 0;
    padding: 2px 7px; border-radius: 4px; font-size: 12px;
}
QToolButton#pushRemoveBtn:hover { color: #ffffff; background-color: #c13434; }
QToolButton#addChannelBtn {
    background: transparent; color: #CE7D6B; border: 1px dashed #374151;
    padding: 5px 12px; border-radius: 6px; font-size: 11px;
}
QToolButton#addChannelBtn:hover { background-color: #1f2937; border-color: #CE7D6B; }
QToolButton#addChannelBtn:disabled { color: #4b5563; border-color: #21262d; }
QToolButton#addChannelBtn::menu-indicator { image: none; width: 0; }

QWidget#miniRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#miniPct { font-size: 17px; font-weight: 700; color: #e6edf3; }
QLabel#miniPctSub { font-size: 13px; font-weight: 700; color: #9ca3af; }
QLabel#miniReset { font-size: 12px; color: #9ca3af; }

QWidget#toastRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#toastTitle {
    font-size: 14px; font-weight: 700; color: #e6edf3; letter-spacing: 0.5px;
}
QLabel#toastBody { font-size: 12px; color: #9ca3af; }
"""


def _tray_pixmap(pct: int) -> QPixmap:
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1f2937"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 28, 28)
    fill = {"cool": "#4f46e5", "warm": "#d97706", "hot": "#dc2626"}[_heat(pct)]
    p.setBrush(QColor(fill))
    span = int(360 * 16 * max(0, min(pct, 100)) / 100)
    p.drawPie(2, 2, 28, 28, 90 * 16, -span)
    p.setBrush(QColor("#0e1116"))
    p.drawEllipse(9, 9, 14, 14)
    p.end()
    return pm


def _tray_alert_pixmap() -> QPixmap:
    """High-contrast green "go / resume" variant used by the reset-flash."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#22c55e"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(2, 2, 28, 28)
    p.setBrush(QColor("#0e1116"))
    p.drawEllipse(9, 9, 14, 14)
    p.end()
    return pm


PUSH_CHANNEL_NAMES = {
    "ntfy": "ntfy", "telegram": "Telegram", "discord": "Discord",
    "slack": "Slack", "pushover": "Pushover", "gotify": "Gotify",
    "webhook": "Webhook",
}


def _active_push_channels() -> list[str]:
    """Added channels that actually have the value(s) needed to send."""
    return [c for c in app_settings.get_reset_notify_push_channels()
            if app_settings.push_channel_configured(c)]


def _push_configured() -> bool:
    """True if at least one added push channel is ready to send."""
    return bool(_active_push_channels())


def _send_one_channel(provider: str, title: str, body: str) -> tuple[bool, str]:
    if provider == "ntfy":
        return remote_notify.send_ntfy(
            app_settings.get_reset_notify_push_topic(), title, body)
    if provider == "telegram":
        return remote_notify.send_telegram(
            app_settings.get_reset_notify_push_tg_token(),
            app_settings.get_reset_notify_push_tg_chat(), title, body)
    if provider == "discord":
        return remote_notify.send_discord(
            app_settings.get_reset_notify_push_discord(), title, body)
    if provider == "slack":
        return remote_notify.send_slack(
            app_settings.get_reset_notify_push_slack(), title, body)
    if provider == "webhook":
        return remote_notify.send_webhook(
            app_settings.get_reset_notify_push_webhook(), title, body)
    if provider == "pushover":
        return remote_notify.send_pushover(
            app_settings.get_reset_notify_push_po_token(),
            app_settings.get_reset_notify_push_po_user(), title, body)
    if provider == "gotify":
        return remote_notify.send_gotify(
            app_settings.get_reset_notify_push_gotify_url(),
            app_settings.get_reset_notify_push_gotify_token(), title, body)
    return False, f"unknown channel {provider}"


def _dispatch_push(title: str, body: str) -> tuple[bool, str]:
    """Send the push to EVERY added+configured channel. Shared by the reset
    notification and the Settings test button so both exercise one code path.
    Returns (all_ok, summary) — partial failures are reported, never raised."""
    channels = _active_push_channels()
    if not channels:
        return False, "no push channels configured"
    sent, failed = [], []
    for c in channels:
        ok, msg = _send_one_channel(c, title, body)
        (sent if ok else failed).append((c, msg))
    names = lambda items: ", ".join(PUSH_CHANNEL_NAMES.get(c, c) for c, _ in items)
    if not failed:
        return True, f"sent to {names(sent)}"
    detail = "; ".join(f"{PUSH_CHANNEL_NAMES.get(c, c)}: {m}" for c, m in failed)
    if sent:
        return False, f"sent to {names(sent)}; failed — {detail}"
    return False, f"failed — {detail}"


class MiniWidget(QWidget):
    """Tiny always-on-top floating readout: mini mascot + session/weekly bars.

    A frameless, draggable tool window with no taskbar entry. Double-click (or
    right-click -> Expand) returns to the full dashboard. The owning Dashboard
    feeds it usage values and sprite animations so it mirrors the main window.
    """

    expand_requested = Signal()
    quit_requested = Signal()

    SPRITE = 37

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("miniRoot")
        self.setWindowTitle("Clawdmeter")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLESHEET)
        # Opaque background matching the main window (#0e1116). Intentionally NOT a
        # WA_TranslucentBackground window: translucent compositing needs Qt's bundled
        # opengl32sw.dll fallback in the frozen build, which the spec prunes for size.
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        icon_path = assets_root() / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._press_pos: QPoint | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 11, 5)
        row.setSpacing(9)

        self.sprite = SpritePlayer(size=self.SPRITE)
        row.addWidget(self.sprite)

        # Two stacked rows — session (bright) over weekly (dim). Each pairs a
        # right-aligned percentage with a small absolute reset time so the
        # rolling 5h / 7d windows are visible at a glance.
        stack = QVBoxLayout()
        stack.setSpacing(3)
        self.session_pct, self.session_reset, self.session_bar = self._row(stack, "miniPct")
        self.weekly_pct, self.weekly_reset, self.weekly_bar = self._row(stack, "miniPctSub")
        row.addLayout(stack, 1)

        menu = QMenu(self)
        act_expand = QAction("Expand", self)
        act_expand.triggered.connect(self.expand_requested.emit)
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_expand)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._menu = menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._menu.exec(self.mapToGlobal(pos))
        )
        self.setToolTip("Session (top) · Weekly (bottom)\nDouble-click to expand · drag to move")

    def _row(self, parent_layout: QVBoxLayout, pct_object: str):
        line = QHBoxLayout()
        line.setSpacing(7)
        pct = QLabel("-", objectName=pct_object)
        pct.setMinimumWidth(42)
        pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        reset = QLabel("", objectName="miniReset")
        reset.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        line.addWidget(pct)
        line.addWidget(reset)
        line.addStretch(1)
        parent_layout.addLayout(line)
        # A thin usage bar under each line so the mini mirrors the full view's
        # bars, including the red overage restart past 100%.
        bar = UsageBar(height=6)
        bar.setMinimumWidth(130)
        parent_layout.addWidget(bar)
        return pct, reset, bar

    def update_usage(self, session_pct: int, weekly_pct: int,
                     session_reset_minutes: int, weekly_reset_minutes: int) -> None:
        self._set_bar(self.session_pct, self.session_bar, session_pct)
        self._set_bar(self.weekly_pct, self.weekly_bar, weekly_pct)
        self.set_resets(session_reset_minutes, weekly_reset_minutes)

    @staticmethod
    def _set_bar(pct_label, bar, pct: int) -> None:
        """Mirror the full-view bar: heat fill under 100%, red restart past it
        (the bar empties and fills red by the amount over 100)."""
        pct = max(0, int(pct))
        over = max(0, pct - 100)
        if over > 0:
            bar.set_values(0, over, "cool")
        else:
            bar.set_values(pct, 0, _heat(pct))
        pct_label.setText(f"{pct}%")

    def set_resets(self, session_reset_minutes: int, weekly_reset_minutes: int) -> None:
        """Reset labels in the same relative form as the main window."""
        self.session_reset.setText(f"resets in {_format_minutes(session_reset_minutes)}")
        self.weekly_reset.setText(f"resets in {_format_minutes(weekly_reset_minutes)}")
        self.lock_size()

    # Qt's QWIDGETSIZE_MAX — the "no constraint" sentinel for max size.
    _SIZE_MAX = 16777215
    # Horizontal breathing room so high-DPI rounding can't clip the reset text.
    _WIDTH_SLACK_PX = 8

    def lock_size(self) -> None:
        """Pin the window to its current content size as a hard cap.

        Belt-and-suspenders with the native-drag move: even if a mixed-DPI
        geometry glitch tries to resize the window, min == max blocks it, so it
        can't balloon across monitors. Recomputed on every content change so a
        longer "resets in ..." string is never clipped.

        Locks to sizeHint() (not size()): adjustSize() on a visible top-level
        window doesn't update size() synchronously, so reading it back pinned a
        stale, narrower width and clipped the trailing "m"/"h" of the reset text.
        A few px of horizontal slack is added on top: sizeHint() comes out exactly
        text-tight, so at fractional/200% scaling rounding would otherwise shave
        the last glyph. The trailing stretch in each row soaks up the slack.
        """
        self.setMinimumSize(0, 0)
        self.setMaximumSize(self._SIZE_MAX, self._SIZE_MAX)
        self.layout().activate()
        hint = self.sizeHint()
        self.setFixedSize(hint.width() + self._WIDTH_SLACK_PX, hint.height())

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        # Once the pointer passes the drag threshold, hand the move to
        # Windows' native move loop instead of repositioning the window
        # ourselves. The manual self.move() approach made Qt recompute the
        # window geometry on every step, which — when the frameless window
        # straddled a higher-DPI monitor — doubled the size each pass and
        # ballooned it across the desktop. The native loop is DPI-aware and
        # never triggers that recompute. Movement under the threshold is left
        # alone so double-click-to-expand still registers.
        if (e.buttons() & Qt.LeftButton) and self._press_pos is not None:
            moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._press_pos = None
                winutil.start_native_move(int(self.winId()))
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._press_pos = None

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.expand_requested.emit()
            e.accept()


class ResetToast(QWidget):
    """Themed limit-reset toast: a frameless card with the mascot, a title and
    a one-line body. Fades in at the bottom-right of the primary screen, auto-
    dismisses after DURATION_MS, and emits `clicked` (then dismisses) on click
    so the dashboard can pop to the foreground.

    Replaces QSystemTrayIcon.showMessage so the reset alert matches the app's
    look. Pure QtWidgets/QtGui/QtCore — no QtMultimedia, no new dependency.
    """

    clicked = Signal()

    MARGIN = 18           # gap from the screen working-area edges
    DURATION_MS = 8000    # visible time before the auto fade-out
    FADE_MS = 220
    # A reset is good news, so the mascot does its DJ bounce rather than idling.
    ANIMS = ["dance bounce dj"]

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("toastShell")
        self.setWindowTitle("Clawdmeter")
        self._show_seq = 0  # bumps each show so the sprite restarts its anim
        # Frameless, on-top, no taskbar entry, and — critically — never steal
        # focus/activation from whatever the user is doing when it pops.
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        # Opaque, like the main/mini windows. NOT WA_TranslucentBackground:
        # the slim frozen build prunes opengl32sw.dll, without which translucent
        # compositing renders wrong. windowOpacity (the fade) is a separate OS
        # layered-window feature and keeps working.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QWidget(objectName="toastRoot")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(STYLESHEET)
        outer.addWidget(card)

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 16, 12)
        row.setSpacing(12)

        self.sprite = SpritePlayer(size=44)
        row.addWidget(self.sprite, 0, Qt.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel("", objectName="toastTitle")
        self.body = QLabel("", objectName="toastBody")
        self.body.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.body)
        row.addLayout(text, 1)

        self.setFixedWidth(330)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_hooked = False  # is _on_faded_out currently connected?

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

    def show_message(self, title: str, body: str) -> None:
        """Show (or re-show) the toast with new text and restart the timer."""
        self.title.setText(title)
        self.body.setText(body)
        # dismiss() stops the sprite, and set_anims() no-ops on an unchanged key —
        # so reuse of a fixed key would leave the mascot frozen on every toast
        # after the first. A fresh key each show forces a clean restart.
        self._show_seq += 1
        self.sprite.set_anims(f"toast{self._show_seq}", self.ANIMS)
        self.adjustSize()
        self._move_to_corner()

        # Cancel any in-flight fade-out so a fresh alert always lands at full
        # opacity, and drop the hide-on-finish hook from a prior dismiss().
        self._fade.stop()
        self._disconnect_fade_finished()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

        self._dismiss_timer.start(self.DURATION_MS)

    def dismiss(self) -> None:
        """Fade out and hide. Safe to call when already hidden."""
        self._dismiss_timer.stop()
        self._fade.stop()
        if not self._fade_hooked:
            self._fade.finished.connect(self._on_faded_out)
            self._fade_hooked = True
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_faded_out(self) -> None:
        # A new show_message() during the fade would have bumped opacity back
        # up; only actually hide if we really faded to zero.
        if self.windowOpacity() <= 0.01:
            self.sprite.stop()
            self.hide()

    def _disconnect_fade_finished(self) -> None:
        if self._fade_hooked:
            self._fade.finished.disconnect(self._on_faded_out)
            self._fade_hooked = False

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()  # excludes the taskbar
        x = geo.right() - self.width() - self.MARGIN
        y = geo.bottom() - self.height() - self.MARGIN
        self.move(x, y)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            self.dismiss()
            e.accept()
        else:
            super().mousePressEvent(e)


class TitleBar(QWidget):
    """Custom frameless title bar: icon, drag area, view + window buttons."""

    HEIGHT = 48
    ICON_SIZE = 36

    def __init__(self, window: QMainWindow, on_toggle, on_mini) -> None:
        super().__init__(window)
        self.setObjectName("titleBar")
        # Allow vertical animation: min=0, max=HEIGHT. Auto-hide animates
        # maximumHeight between these two values. When auto-hide is off,
        # _apply_auto_hide pins both ends back to HEIGHT.
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.HEIGHT)
        self._win = window
        self._press_pos: QPoint | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 0, 0)
        row.setSpacing(8)

        # The app icon now lives at the top of the nav rail (so it survives the
        # auto-hide title bar), not here. The title bar starts with the wordmark.
        name = QLabel("CLAWDMETER", objectName="titleAppName")
        row.addWidget(name)
        row.addStretch(1)

        # Font Awesome glyphs for the view toggles + window controls. (Settings
        # now lives in the nav rail, so there's no gear here.)
        # Full<->Compact toggle: angles that point DOWN in full (click to collapse
        # to compact) and UP in compact (click to expand to full).
        self.caret_btn = self._tool_btn("", "Compact view")
        self.caret_btn.clicked.connect(on_toggle)
        row.addWidget(self.caret_btn)

        # Mini button (always available): Windows' restore-to-center glyph.
        self.mini_btn = self._tool_btn(chr(0xF422), "Mini view")  # compress-to-center
        self.mini_btn.clicked.connect(on_mini)
        row.addWidget(self.mini_btn)

        self.set_active_mode("full")

        self.min_btn = self._tool_btn(chr(0xF2D1), "Minimize")        # ChromeMinimize
        self.min_btn.clicked.connect(self._win.showMinimized)
        row.addWidget(self.min_btn)

        self.close_btn = self._tool_btn(chr(0xF00D), "Close")         # ChromeClose
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.clicked.connect(self._win.close)
        row.addWidget(self.close_btn)

    def _tool_btn(self, glyph: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("titleBtn")
        b.setText(glyph)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def set_active_mode(self, mode: str) -> None:
        """Point the caret toward what the toggle does next: DOWN in full (click
        -> compact), UP in compact (click -> full)."""
        up = mode == "compact"
        self.caret_btn.setText(chr(0xF102) if up else chr(0xF103))  # angles up / down
        self.caret_btn.setToolTip("Full view" if up else "Compact view")

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        # Once past the drag threshold, hand the move to Windows' native move
        # loop rather than repositioning the window ourselves every step. The
        # manual self._win.move() approach made Qt recompute the window geometry
        # on each step, which ballooned the frameless window when it was dragged
        # onto a higher-DPI monitor. The native loop is DPI-aware and avoids it.
        # Under the threshold we do nothing so the double-click-to-reset still
        # registers.
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._press_pos = None
        if self._win.isMaximized():
            # If the OS maximized us (e.g. Win+Up), restore before the handoff so
            # the window follows the cursor at its normal size.
            self._win.showNormal()
        winutil.start_native_move(int(self._win.winId()))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._press_pos = None

    def mouseDoubleClickEvent(self, e) -> None:
        # Double-click snaps the window back to the snug auto-fit height (undoes
        # a manual height resize).
        if e.button() == Qt.LeftButton:
            self._win.reset_to_fit()
            e.accept()


# Per-channel editor fields: (getter, setter, placeholder, is_secret). Telegram
# needs two; ntfy/Discord one each.
_PUSH_FIELD_SPECS = {
    "ntfy": [(app_settings.get_reset_notify_push_topic,
              app_settings.set_reset_notify_push_topic,
              "ntfy topic (e.g. clawd-nick-7f3a) or full URL", False)],
    "telegram": [
        (app_settings.get_reset_notify_push_tg_token,
         app_settings.set_reset_notify_push_tg_token,
         "Telegram bot token (from @BotFather)", True),
        (app_settings.get_reset_notify_push_tg_chat,
         app_settings.set_reset_notify_push_tg_chat,
         "Telegram chat ID (e.g. 123456789)", False)],
    "discord": [(app_settings.get_reset_notify_push_discord,
                 app_settings.set_reset_notify_push_discord,
                 "Discord webhook URL", True)],
    "slack": [(app_settings.get_reset_notify_push_slack,
               app_settings.set_reset_notify_push_slack,
               "Slack incoming webhook URL", True)],
    "webhook": [(app_settings.get_reset_notify_push_webhook,
                 app_settings.set_reset_notify_push_webhook,
                 "Webhook URL (receives a JSON POST)", True)],
    "pushover": [
        (app_settings.get_reset_notify_push_po_token,
         app_settings.set_reset_notify_push_po_token,
         "Pushover application API token", True),
        (app_settings.get_reset_notify_push_po_user,
         app_settings.set_reset_notify_push_po_user,
         "Pushover user key", True)],
    "gotify": [
        (app_settings.get_reset_notify_push_gotify_url,
         app_settings.set_reset_notify_push_gotify_url,
         "Gotify server URL (e.g. https://gotify.example.com)", False),
        (app_settings.get_reset_notify_push_gotify_token,
         app_settings.set_reset_notify_push_gotify_token,
         "Gotify app token", True)],
}
_PUSH_HINTS = {
    "ntfy": "Subscribe to the same topic in the ntfy app (Android/iOS). Pick a "
            "long, hard-to-guess topic — anyone who knows it can read your alerts.",
    "telegram": "Message @BotFather to create a bot and copy its token, then DM "
                "the bot and read your chat ID from "
                "api.telegram.org/bot<token>/getUpdates. Keep the token private.",
    "discord": "In Discord: Channel Settings → Integrations → Webhooks → New "
               "Webhook → Copy URL. Anyone with the URL can post there, so keep "
               "it private.",
    "slack": "In Slack: create an Incoming Webhook for a channel "
             "(api.slack.com/messaging/webhooks) and paste its URL. Keep it private.",
    "webhook": "POSTs JSON {title, body, app} to any URL — wire it to Zapier, "
               "Make, IFTTT, n8n, Home Assistant, or your own endpoint.",
    "pushover": "Create an application at pushover.net for the API token; your "
                "user key is on your Pushover dashboard. Get the app on iOS/Android.",
    "gotify": "Self-hosted Gotify: your server URL plus an application token "
              "from the Gotify apps page.",
}


def _push_channel_summary(provider: str) -> str:
    """Short glanceable state for a channel row (the non-secret topic for ntfy,
    a 'set'/'not set' for the secret channels)."""
    def both(a: str, b: str, ok: str) -> str:
        if a and b:
            return ok
        return "incomplete" if (a or b) else "not set"

    if provider == "ntfy":
        return app_settings.get_reset_notify_push_topic() or "not set"
    if provider == "telegram":
        return both(app_settings.get_reset_notify_push_tg_token(),
                    app_settings.get_reset_notify_push_tg_chat(), "bot + chat set")
    if provider == "discord":
        return "webhook set" if app_settings.get_reset_notify_push_discord() else "not set"
    if provider == "slack":
        return "webhook set" if app_settings.get_reset_notify_push_slack() else "not set"
    if provider == "webhook":
        return "URL set" if app_settings.get_reset_notify_push_webhook() else "not set"
    if provider == "pushover":
        return both(app_settings.get_reset_notify_push_po_token(),
                    app_settings.get_reset_notify_push_po_user(), "token + user key set")
    if provider == "gotify":
        return both(app_settings.get_reset_notify_push_gotify_url(),
                    app_settings.get_reset_notify_push_gotify_token(), "server + token set")
    return ""


class _PushChannelRow(QWidget):
    """One added push channel as a small card: a glanceable status line (a
    coral/dim dot + name + muted summary) with flat Edit/✕ actions, and a
    collapsible editor (the channel's field(s) + hint) revealed by Edit."""

    removed = Signal()

    def __init__(self, provider: str, name: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._name = name
        self.setObjectName("pushCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 7, 7, 7)
        col.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(4)
        self._summary = QLabel(objectName="pushSummary")
        self._summary.setTextFormat(Qt.RichText)
        head.addWidget(self._summary)
        head.addStretch(1)
        self._edit_btn = QToolButton(objectName="pushEditBtn")
        self._edit_btn.setText("Edit")
        self._edit_btn.setCursor(Qt.PointingHandCursor)
        self._edit_btn.clicked.connect(self._toggle_edit)
        head.addWidget(self._edit_btn)
        rm = QToolButton(objectName="pushRemoveBtn")
        rm.setText("✕")
        rm.setToolTip(f"Remove {name}")
        rm.setCursor(Qt.PointingHandCursor)
        rm.clicked.connect(self.removed.emit)
        head.addWidget(rm)
        col.addLayout(head)

        self._editor = QWidget()
        ed = QVBoxLayout(self._editor)
        ed.setContentsMargins(2, 2, 0, 0)
        ed.setSpacing(4)
        for getter, setter, placeholder, secret in _PUSH_FIELD_SPECS[provider]:
            f = QLineEdit()
            f.setPlaceholderText(placeholder)
            if secret:
                f.setEchoMode(QLineEdit.Password)
            f.setText(getter())
            f.editingFinished.connect(
                lambda field=f, save=setter: self._on_field(field, save))
            ed.addWidget(f)
        hint = _PUSH_HINTS.get(provider)
        if hint:
            h = QLabel(hint, objectName="sectionHint")
            h.setWordWrap(True)
            ed.addWidget(h)
        self._first_field = self._editor.findChild(QLineEdit)
        col.addWidget(self._editor)
        self._editor.setVisible(False)
        self._refresh()

    def _on_field(self, field: QLineEdit, save) -> None:
        save(field.text())
        self._refresh()

    def _toggle_edit(self) -> None:
        self.set_editing(not self._editor.isVisible())

    def set_editing(self, on: bool) -> None:
        self._editor.setVisible(on)
        self._edit_btn.setText("Done" if on else "Edit")
        if on and self._first_field is not None:
            self._first_field.setFocus()

    def _refresh(self) -> None:
        configured = app_settings.push_channel_configured(self._provider)
        dot = "#CE7D6B" if configured else "#4b5563"
        summary = _push_channel_summary(self._provider)
        summary = (summary.replace("&", "&amp;").replace("<", "&lt;")
                   .replace(">", "&gt;"))  # the ntfy topic is user-supplied
        self._summary.setText(
            f"<span style='color:{dot}'>●</span>&nbsp;&nbsp;"
            f"<span style='color:#e6edf3'>{self._name}</span>&nbsp;&nbsp;"
            f"<span style='color:#6b7280'>· {summary}</span>")


class SettingsPanel(QWidget):
    """The Settings page — a destination in the app nav rail (not an overlay).
    Holds a left tab rail (General/Display/Connection/Notifications/About) plus
    the matching forms. Lives as a page in the content stack; you leave it by
    clicking another rail item."""

    # Emitted from the push-test worker thread; delivered on the UI thread.
    _push_test_result = Signal(bool, str)

    def __init__(self, parent: QWidget, on_always_on_top_changed, on_auto_hide_changed,
                 on_refresh_token=None, on_auto_refresh_changed=None,
                 on_poll_interval_changed=None, on_sessions_view_changed=None,
                 on_token_view_changed=None, on_check_updates=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_aot_changed = on_always_on_top_changed
        self._on_auto_hide_changed = on_auto_hide_changed
        self._on_refresh_token = on_refresh_token
        self._on_auto_refresh_changed = on_auto_refresh_changed
        self._on_poll_interval_changed = on_poll_interval_changed
        self._on_sessions_view_changed = on_sessions_view_changed
        self._on_token_view_changed = on_token_view_changed
        self._on_check_updates = on_check_updates

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Page header — just the title now; you leave Settings via the nav rail.
        header_w = QWidget()
        header = QHBoxLayout(header_w)
        header.setContentsMargins(20, 18, 20, 8)
        header.addWidget(QLabel("SETTINGS", objectName="settingsTitle"))
        header.addStretch(1)
        outer.addWidget(header_w)

        # Body: a left tab rail + one stacked page per tab. Each page scrolls on
        # its own so a long tab (Notifications) never drags the shorter ones, and
        # new settings slot into the tab that owns their concern.
        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(0)
        outer.addLayout(content_row, 1)

        nav_w = QWidget(objectName="settingsNav")
        nav_w.setFixedWidth(148)
        nav = QVBoxLayout(nav_w)
        nav.setContentsMargins(8, 8, 8, 8)
        nav.setSpacing(4)
        content_row.addWidget(nav_w)

        self._stack = QStackedWidget()
        content_row.addWidget(self._stack, 1)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        def _make_tab(glyph: str, label: str) -> QVBoxLayout:
            btn = QPushButton(f"{glyph}   {label}", objectName="navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            nav.addWidget(btn)
            page = QScrollArea()
            page.setObjectName("settingsScroll")
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.NoFrame)
            page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            page.viewport().setStyleSheet("background: transparent;")
            page_body = QWidget(objectName="settingsBody")
            page.setWidget(page_body)
            lay = QVBoxLayout(page_body)
            lay.setContentsMargins(20, 8, 20, 18)
            lay.setSpacing(12)
            self._nav_group.addButton(btn, self._stack.addWidget(page))
            return lay

        # Font Awesome 6 Free (Solid) glyphs: gear, display, circle-nodes, bell,
        # info. Rendered as text so they recolor with the QSS accent on
        # hover/active, exactly as the Segoe glyphs did.
        gen_layout = _make_tab("\uF013", "General")
        disp_layout = _make_tab("\uE163", "Display")
        conn_layout = _make_tab("\uE4E2", "Connection")
        notif_layout = _make_tab("\uF0F3", "Notifications")
        about_layout = _make_tab("\uF129", "About")
        nav.addStretch(1)
        self._nav_group.idClicked.connect(self._stack.setCurrentIndex)
        self._nav_group.button(0).setChecked(True)

        # `layout` is a moving cursor: each section appends to whichever tab page
        # it currently points at, reassigned at the section boundaries below.
        layout = conn_layout

        layout.addWidget(QLabel("CREDENTIALS", objectName="sectionLabel"))
        self.cred_btn = QPushButton("Use alternative credentials")
        self.cred_btn.clicked.connect(self._choose_credentials)
        layout.addWidget(self.cred_btn)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.cred_status = QLabel(objectName="credStatus")
        self.cred_status.setWordWrap(True)
        self.cred_reset_btn = QPushButton("reset", objectName="resetLink")
        self.cred_reset_btn.setCursor(Qt.PointingHandCursor)
        self.cred_reset_btn.setFocusPolicy(Qt.NoFocus)
        self.cred_reset_btn.clicked.connect(self._reset_credentials)
        status_row.addWidget(self.cred_status, 1)
        status_row.addWidget(self.cred_reset_btn)
        layout.addLayout(status_row)
        self._refresh_cred_status()

        layout.addSpacing(10)
        layout.addWidget(QLabel("TOKEN", objectName="sectionLabel"))
        self.token_status = QLabel(objectName="sectionHint")
        self.token_status.setWordWrap(True)
        layout.addWidget(self.token_status)
        self.auto_refresh_check = QCheckBox("Auto-refresh when expired")
        self.auto_refresh_check.setChecked(app_settings.get_auto_refresh())
        self.auto_refresh_check.toggled.connect(self._on_auto_refresh_toggled)
        layout.addWidget(self.auto_refresh_check)
        self.refresh_token_btn = QPushButton("Refresh token now")
        self.refresh_token_btn.clicked.connect(self._on_refresh_token_clicked)
        layout.addWidget(self.refresh_token_btn)
        self.refresh_token_status()

        layout = gen_layout
        layout.addWidget(QLabel("WINDOW", objectName="sectionLabel"))
        self.aot_check = QCheckBox("Always on top")
        self.aot_check.setChecked(app_settings.get_always_on_top())
        self.aot_check.toggled.connect(self._on_aot_toggled)
        layout.addWidget(self.aot_check)

        self.auto_hide_check = QCheckBox("Auto-hide title bar")
        self.auto_hide_check.setChecked(app_settings.get_auto_hide_titlebar())
        self.auto_hide_check.toggled.connect(self._on_auto_hide_toggled)
        layout.addWidget(self.auto_hide_check)

        self.quit_on_close_check = QCheckBox("Quit on close (don't minimize to tray)")
        self.quit_on_close_check.setChecked(app_settings.get_quit_on_close())
        self.quit_on_close_check.toggled.connect(self._on_quit_on_close_toggled)
        layout.addWidget(self.quit_on_close_check)

        layout.addSpacing(10)
        layout.addWidget(QLabel("UPDATES", objectName="sectionLabel"))
        updates_hint = QLabel(
            "Clawdmeter ships as a single .exe with no auto-installer. When a "
            "newer release is published on GitHub, the tray menu shows an "
            "“Update available” item — click it to open the download page.",
            objectName="sectionHint",
        )
        updates_hint.setWordWrap(True)
        layout.addWidget(updates_hint)
        self.auto_check_updates_check = QCheckBox("Automatically check for updates")
        self.auto_check_updates_check.setChecked(app_settings.get_auto_check_updates())
        self.auto_check_updates_check.toggled.connect(self._on_auto_check_updates_toggled)
        layout.addWidget(self.auto_check_updates_check)
        self.check_updates_btn = QPushButton("Check for updates now")
        self.check_updates_btn.clicked.connect(self._on_check_updates_clicked)
        layout.addWidget(self.check_updates_btn)

        layout = disp_layout
        layout.addWidget(QLabel("SESSIONS", objectName="sectionLabel"))
        sessions_hint = QLabel(
            "Show every active Claude Code session as its own mascot, and the "
            "child agents a session spins up. Turn off for a single mascot.",
            objectName="sectionHint",
        )
        sessions_hint.setWordWrap(True)
        layout.addWidget(sessions_hint)
        self.multi_sessions_check = QCheckBox("Show multiple sessions")
        self.multi_sessions_check.setChecked(app_settings.get_show_multiple_sessions())
        self.multi_sessions_check.toggled.connect(self._on_multi_sessions_toggled)
        layout.addWidget(self.multi_sessions_check)

        self.subagents_check = QCheckBox("Show subagents")
        self.subagents_check.setChecked(app_settings.get_show_subagents())
        self.subagents_check.toggled.connect(self._on_subagents_toggled)
        layout.addWidget(self.subagents_check)

        layout.addSpacing(10)
        layout.addWidget(QLabel("TOKEN USAGE", objectName="sectionLabel"))
        tokens_hint = QLabel(
            "Show how many tokens you've used — input+output for the 5h/7d "
            "windows beside the bars, with a full per-session breakdown when you "
            "hover a mascot. Read from your local transcripts, not the API.",
            objectName="sectionHint",
        )
        tokens_hint.setWordWrap(True)
        layout.addWidget(tokens_hint)
        self.token_usage_check = QCheckBox("Show token usage")
        self.token_usage_check.setChecked(app_settings.get_show_token_usage())
        self.token_usage_check.toggled.connect(self._on_token_usage_toggled)
        layout.addWidget(self.token_usage_check)

        layout = conn_layout
        layout.addSpacing(10)
        layout.addWidget(QLabel("USAGE POLLING", objectName="sectionLabel"))
        poll_hint = QLabel(
            f"How often to check your usage ({app_settings.POLL_INTERVAL_MIN}"
            f"–{app_settings.POLL_INTERVAL_MAX}s). Each check is a tiny API "
            "request, so lower = fresher but more requests. Takes effect on the "
            "next check.",
            objectName="sectionHint",
        )
        poll_hint.setWordWrap(True)
        layout.addWidget(poll_hint)
        poll_row = QHBoxLayout()
        poll_row.setSpacing(6)
        self.poll_interval_edit = QLineEdit()
        # Digits-only — NOT QIntValidator(0, MAX). An int validator marks
        # out-of-range-but-same-digit-count values like "999" as Intermediate,
        # and editingFinished is suppressed for non-Acceptable input, so the
        # commit/clamp would silently never run. A plain digit validator keeps
        # every commit firing; the handler does the clamping.
        self.poll_interval_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,6}"), self)
        )
        self.poll_interval_edit.setText(str(app_settings.get_poll_interval()))
        self.poll_interval_edit.setFixedWidth(64)
        self.poll_interval_edit.setToolTip(
            f"Whole seconds, {app_settings.POLL_INTERVAL_MIN}–"
            f"{app_settings.POLL_INTERVAL_MAX}. Out-of-range values are clamped."
        )
        self.poll_interval_edit.editingFinished.connect(self._on_poll_interval_committed)
        self.poll_interval_edit.textEdited.connect(self._clear_poll_note)
        poll_row.addWidget(self.poll_interval_edit)
        poll_row.addWidget(QLabel("seconds"))
        poll_row.addStretch(1)
        layout.addLayout(poll_row)

        # Transient amber note shown when a committed value gets clamped, so the
        # correction is obvious without a modal. Holds briefly, fades out, and
        # clears the moment the user edits the field again.
        self.poll_interval_note = QLabel("", objectName="pollNote")
        self.poll_interval_note.setWordWrap(True)
        self._poll_note_effect = QGraphicsOpacityEffect(self.poll_interval_note)
        self.poll_interval_note.setGraphicsEffect(self._poll_note_effect)
        self._poll_note_effect.setOpacity(0.0)
        self.poll_interval_note.hide()
        self._poll_note_fade = QPropertyAnimation(self._poll_note_effect, b"opacity", self)
        self._poll_note_fade.setDuration(500)
        self._poll_note_fade.setEndValue(0.0)
        self._poll_note_fade.finished.connect(self._clear_poll_note)
        self._poll_note_hold = QTimer(self)
        self._poll_note_hold.setSingleShot(True)
        self._poll_note_hold.setInterval(3200)
        self._poll_note_hold.timeout.connect(self._fade_poll_note)
        layout.addWidget(self.poll_interval_note)

        layout = notif_layout
        layout.addWidget(QLabel("NOTIFICATIONS", objectName="sectionLabel"))
        notify_hint = QLabel(
            "Alert me when a usage limit resets and I can resume — only when I "
            "was near the limit.",
            objectName="sectionHint",
        )
        notify_hint.setWordWrap(True)
        layout.addWidget(notify_hint)
        self.notify_check = QCheckBox("Notify on limit reset")
        self.notify_check.setChecked(app_settings.get_reset_notify())
        self.notify_check.toggled.connect(self._on_notify_toggled)
        layout.addWidget(self.notify_check)

        # Windows channel: the desktop toast + tray flash (indented under the
        # master), with Play-a-sound / Pop-to-front nested in an indented box so
        # they hide together when the channel — or the master — is off.
        self.notify_toast_check = QCheckBox("Show a Windows notification")
        self.notify_toast_check.setStyleSheet("margin-left: 22px;")
        self.notify_toast_check.setChecked(app_settings.get_reset_notify_toast())
        self.notify_toast_check.toggled.connect(self._on_notify_toast_toggled)
        layout.addWidget(self.notify_toast_check)

        self.notify_toast_box = QWidget()
        toast_box = QVBoxLayout(self.notify_toast_box)
        toast_box.setContentsMargins(44, 0, 0, 0)
        toast_box.setSpacing(6)
        self.notify_sound_check = QCheckBox("Play a sound")
        self.notify_sound_check.setChecked(app_settings.get_reset_notify_sound())
        self.notify_sound_check.toggled.connect(self._on_notify_sound_toggled)
        toast_box.addWidget(self.notify_sound_check)
        self.notify_popup_check = QCheckBox("Pop the window to front")
        self.notify_popup_check.setChecked(app_settings.get_reset_notify_popup())
        self.notify_popup_check.toggled.connect(self._on_notify_popup_toggled)
        toast_box.addWidget(self.notify_popup_check)
        layout.addWidget(self.notify_toast_box)

        self.notify_push_check = QCheckBox("Send a push notification")
        self.notify_push_check.setStyleSheet("margin-left: 22px;")
        self.notify_push_check.setChecked(app_settings.get_reset_notify_push())
        self.notify_push_check.toggled.connect(self._on_notify_push_toggled)
        layout.addWidget(self.notify_push_check)

        # Push sub-box: a list of the channels you've ADDED (each a summary row
        # with edit/remove), an "Add a channel" menu, and one test button that
        # fires every configured channel. Hides as a unit when push is off.
        self.notify_push_box = QWidget()
        push_box = QVBoxLayout(self.notify_push_box)
        push_box.setContentsMargins(44, 0, 0, 0)
        push_box.setSpacing(6)

        self.notify_push_list = QVBoxLayout()
        self.notify_push_list.setSpacing(6)
        push_box.addLayout(self.notify_push_list)

        self.notify_push_add_btn = QToolButton(objectName="addChannelBtn")
        self.notify_push_add_btn.setText("+ Add a channel  ▾")
        self.notify_push_add_btn.setCursor(Qt.PointingHandCursor)
        self.notify_push_add_btn.setPopupMode(QToolButton.InstantPopup)
        self.notify_push_add_menu = QMenu(self.notify_push_add_btn)
        self.notify_push_add_btn.setMenu(self.notify_push_add_menu)
        push_box.addWidget(self.notify_push_add_btn, alignment=Qt.AlignLeft)

        self.notify_push_test_btn = QPushButton("Send test notification")
        self.notify_push_test_btn.clicked.connect(self._on_test_push_clicked)
        push_box.addWidget(self.notify_push_test_btn)
        self.notify_push_test_status = QLabel("", objectName="sectionHint")
        self.notify_push_test_status.setWordWrap(True)
        push_box.addWidget(self.notify_push_test_status)
        layout.addWidget(self.notify_push_box)
        self._push_test_result.connect(self._on_test_push_result)
        self._push_rows: dict[str, _PushChannelRow] = {}
        self._rebuild_push_channels()

        self._sync_notify_subtoggles()

        layout = gen_layout
        layout.addSpacing(10)
        layout.addWidget(QLabel("START MENU", objectName="sectionLabel"))
        hint = QLabel(
            "Adds a Start menu shortcut. Right-click it in Start to Pin to Start.",
            objectName="sectionHint",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.start_btn = QPushButton()
        self.start_btn.clicked.connect(self._on_start_menu_toggled)
        layout.addWidget(self.start_btn)
        self._refresh_start_menu_btn()

        layout = about_layout
        layout.addWidget(QLabel("ABOUT", objectName="sectionLabel"))
        about = QLabel(
            f"Clawdmeter-Windows  v{app_settings.APP_VERSION}\n"
            "by Nick Welter (@weltern) & Claude\n"
            "github.com/weltern/Clawdmeter-Windows\n\n"
            "MIT licensed · the Clawd mascot is © Anthropic PBC and is "
            "not covered by the MIT license · unofficial, not affiliated "
            "with Anthropic.\n\n"
            "Icons by Font Awesome Free (fontawesome.com) · SIL OFL 1.1.",
            objectName="sectionHint",
        )
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(about)

        for _page_layout in (gen_layout, disp_layout, conn_layout, notif_layout, about_layout):
            _page_layout.addStretch(1)

    def _refresh_cred_status(self) -> None:
        override = app_settings.get_credentials_override()
        if override:
            self.cred_status.setText(f"Using: {override}")
            self.cred_reset_btn.show()
        else:
            self.cred_status.setText(f"Default: {DEFAULT_CREDENTIALS_PATH}")
            self.cred_reset_btn.hide()

    def refresh_token_status(self) -> None:
        """Show access-token validity; enable manual refresh only when it's
        actually needed (expired / near expiry) so a valid token can't be
        needlessly refreshed into a rate-limit error."""
        path = credentials_path()
        exp = token_refresh.token_expiry_ms(path)
        needs_refresh = token_refresh.is_expired(path)
        self.refresh_token_btn.setEnabled(needs_refresh)
        if needs_refresh:
            self.refresh_token_btn.setText("Refresh token now")
            self.refresh_token_btn.setToolTip("")
        else:
            self.refresh_token_btn.setText("Token valid — refresh disabled")
            self.refresh_token_btn.setToolTip(
                "Disabled because your token is still valid — it refreshes "
                "automatically when it expires."
            )
        if exp is None:
            self.token_status.setText("Token expiry unknown.")
            return
        secs = exp / 1000 - time.time()
        if secs <= 0:
            self.token_status.setText("Token expired — refresh now, or wait for auto-refresh.")
        elif needs_refresh:
            self.token_status.setText("Token expiring — refresh now, or wait for auto-refresh.")
        else:
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            self.token_status.setText(f"Valid for ~{h}h {m}m — refreshes automatically.")

    def set_token_status(self, text: str) -> None:
        self.token_status.setText(text)

    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        app_settings.set_auto_refresh(checked)
        if self._on_auto_refresh_changed:
            self._on_auto_refresh_changed(checked)

    def _on_poll_interval_committed(self) -> None:
        """Parse the box, clamp+persist, reflect the applied value back into the
        field, surface an amber note if it was corrected, and push it live."""
        raw = self.poll_interval_edit.text().strip()
        try:
            requested = int(raw)
            parsed = True
        except ValueError:
            requested = app_settings.get_poll_interval()
            parsed = False
        clamped = app_settings.set_poll_interval(requested)
        self.poll_interval_edit.setText(str(clamped))  # reflect what applied

        lo, hi = app_settings.POLL_INTERVAL_MIN, app_settings.POLL_INTERVAL_MAX
        if not parsed:
            self._show_poll_note(f"Enter a whole number {lo}–{hi}s — kept {clamped}.")
        elif clamped != requested:
            self._show_poll_note(f"Adjusted to {clamped}s — allowed range {lo}–{hi}.")
        else:
            self._clear_poll_note()

        if self._on_poll_interval_changed:
            self._on_poll_interval_changed(clamped)

    def _show_poll_note(self, text: str) -> None:
        """Show the amber clamp note at full opacity, then schedule a fade."""
        self._poll_note_hold.stop()
        self._poll_note_fade.stop()
        self.poll_interval_note.setText(text)
        self._poll_note_effect.setOpacity(1.0)
        self.poll_interval_note.show()
        self._poll_note_hold.start()

    def _fade_poll_note(self) -> None:
        self._poll_note_fade.stop()
        self._poll_note_fade.setStartValue(self._poll_note_effect.opacity())
        self._poll_note_fade.start()

    def _clear_poll_note(self, *args) -> None:
        """Hide the note immediately (called on next edit and on fade finish)."""
        self._poll_note_hold.stop()
        self._poll_note_fade.stop()
        self.poll_interval_note.clear()
        self.poll_interval_note.hide()
        self._poll_note_effect.setOpacity(0.0)

    def _on_refresh_token_clicked(self) -> None:
        if self._on_refresh_token:
            self.set_token_status("Refreshing…")
            self._on_refresh_token()

    def _on_aot_toggled(self, checked: bool) -> None:
        app_settings.set_always_on_top(checked)
        if self._on_aot_changed:
            self._on_aot_changed(checked)

    def _on_auto_hide_toggled(self, checked: bool) -> None:
        app_settings.set_auto_hide_titlebar(checked)
        if self._on_auto_hide_changed:
            self._on_auto_hide_changed(checked)

    def _on_quit_on_close_toggled(self, checked: bool) -> None:
        app_settings.set_quit_on_close(checked)

    def _on_auto_check_updates_toggled(self, checked: bool) -> None:
        # The running checker reads this each cycle, so no live callback needed.
        app_settings.set_auto_check_updates(checked)

    def _on_check_updates_clicked(self) -> None:
        if self._on_check_updates:
            self._on_check_updates()

    def _on_multi_sessions_toggled(self, checked: bool) -> None:
        app_settings.set_show_multiple_sessions(checked)
        if self._on_sessions_view_changed:
            self._on_sessions_view_changed()

    def _on_subagents_toggled(self, checked: bool) -> None:
        app_settings.set_show_subagents(checked)
        if self._on_sessions_view_changed:
            self._on_sessions_view_changed()

    def _on_token_usage_toggled(self, checked: bool) -> None:
        app_settings.set_show_token_usage(checked)
        if self._on_token_view_changed:
            self._on_token_view_changed()

    def _on_notify_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify(checked)
        self._sync_notify_subtoggles()

    def _on_notify_toast_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_toast(checked)
        self._sync_notify_subtoggles()

    def _on_notify_sound_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_sound(checked)

    def _on_notify_popup_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_popup(checked)

    def _on_notify_push_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_push(checked)
        self._sync_notify_subtoggles()

    def _rebuild_push_channels(self) -> None:
        """Re-render the added-channel rows from settings and refresh the
        Add-a-channel menu (which only offers channels not yet added)."""
        for row in self._push_rows.values():
            self.notify_push_list.removeWidget(row)
            row.deleteLater()
        self._push_rows = {}
        for provider in app_settings.get_reset_notify_push_channels():
            row = _PushChannelRow(provider, PUSH_CHANNEL_NAMES.get(provider, provider))
            row.removed.connect(lambda p=provider: self._remove_push_channel(p))
            self.notify_push_list.addWidget(row)
            self._push_rows[provider] = row
        self._refresh_push_add_menu()

    def _refresh_push_add_menu(self) -> None:
        self.notify_push_add_menu.clear()
        remaining = [p for p in app_settings.PUSH_PROVIDERS if p not in self._push_rows]
        for p in remaining:
            act = self.notify_push_add_menu.addAction(PUSH_CHANNEL_NAMES.get(p, p))
            act.triggered.connect(lambda _checked=False, prov=p: self._add_push_channel(prov))
        self.notify_push_add_btn.setEnabled(bool(remaining))

    def _add_push_channel(self, provider: str) -> None:
        chans = app_settings.get_reset_notify_push_channels()
        if provider not in chans:
            chans.append(provider)
            app_settings.set_reset_notify_push_channels(chans)
        self._rebuild_push_channels()
        row = self._push_rows.get(provider)
        if row is not None:
            row.set_editing(True)  # open the new row so the field is ready to fill

    def _remove_push_channel(self, provider: str) -> None:
        chans = [c for c in app_settings.get_reset_notify_push_channels() if c != provider]
        app_settings.set_reset_notify_push_channels(chans)
        self._rebuild_push_channels()

    def _on_test_push_clicked(self) -> None:
        """Send a one-off push with the current settings so the user can verify
        their setup. Runs off the UI thread; the result returns via signal."""
        self.notify_push_test_btn.setEnabled(False)
        self.notify_push_test_status.setText("Sending…")

        def worker() -> None:
            ok, msg = _dispatch_push(
                "Clawdmeter test",
                "If you can see this, your phone notifications are set up.",
            )
            self._push_test_result.emit(ok, msg)

        threading.Thread(target=worker, name="push-test", daemon=True).start()

    def _on_test_push_result(self, ok: bool, msg: str) -> None:
        self.notify_push_test_btn.setEnabled(True)  # re-enable after the send
        # msg = "sent to ntfy, Discord" on success; capitalise + add a nudge.
        nice = (msg[:1].upper() + msg[1:]) if msg else "Sent"
        self.notify_push_test_status.setText(
            f"{nice} — check your notifications." if ok else f"Failed: {msg}"
        )

    def _sync_notify_subtoggles(self) -> None:
        """Show/hide the notification sub-options to match the hierarchy: the
        master off hides both channels; each channel's sub-box (Windows
        sound/pop, or the push channel list) hides when that channel is off."""
        on = self.notify_check.isChecked()
        # Both channel toggles hide entirely when the master is off.
        self.notify_toast_check.setVisible(on)
        self.notify_push_check.setVisible(on)

        # Windows channel sub-box (sound + pop): only when master + Windows on.
        toast_on = on and self.notify_toast_check.isChecked()
        self.notify_toast_box.setVisible(toast_on)

        # Push channel sub-box (the added-channel list): only when master + push on.
        push_on = on and self.notify_push_check.isChecked()
        self.notify_push_box.setVisible(push_on)
        if not push_on:
            self.notify_push_test_status.clear()

    def _refresh_start_menu_btn(self) -> None:
        if start_menu.has_shortcut():
            self.start_btn.setText("Remove from Start menu")
        else:
            self.start_btn.setText("Add to Start menu")

    def _on_start_menu_toggled(self) -> None:
        if start_menu.has_shortcut():
            ok, msg = start_menu.remove_shortcut()
            if not ok:
                QMessageBox.warning(self, "Clawdmeter", f"Failed to remove shortcut:\n{msg}")
        else:
            ok, msg = start_menu.create_shortcut()
            if ok:
                QMessageBox.information(
                    self, "Clawdmeter",
                    "Added Clawdmeter to your Start menu.\n\n"
                    "Open Start, find Clawdmeter, right-click it, "
                    "and choose Pin to Start.",
                )
            else:
                QMessageBox.warning(self, "Clawdmeter", f"Failed to create shortcut:\n{msg}")
        self._refresh_start_menu_btn()

    def _choose_credentials(self) -> None:
        start = str(credentials_path())
        path, _ = QFileDialog.getOpenFileName(self, "Locate .credentials.json", start, "JSON (*.json)")
        if path:
            os.environ["CLAUDE_CREDENTIALS_PATH"] = path
            app_settings.set_credentials_override(path)
            self._refresh_cred_status()
            QMessageBox.information(self, "Clawdmeter", f"Using:\n{path}\n(next poll within 60s)")

    def _reset_credentials(self) -> None:
        os.environ.pop("CLAUDE_CREDENTIALS_PATH", None)
        app_settings.set_credentials_override("")
        self._refresh_cred_status()


class NavRail(QWidget):
    """Slim vertical icon rail down the content area's left edge — icon-only, with
    names shown via tooltips. The mascot sits at the top; the active page is
    accent-highlighted. Full-height (parented to root) so it survives the
    auto-hide title bar. Lives only on the full window."""

    COLLAPSED = 46   # the rail's fixed width

    def __init__(self, parent: QWidget, on_select) -> None:
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._on_select = on_select

        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 10)
        col.setSpacing(4)

        # Mascot at the rail's top-left — where the title-bar icon used to sit,
        # but on the rail so it survives the auto-hide title bar.
        icon_lbl = QLabel()
        ip = assets_root() / "icon.png"
        if ip.exists():
            icon_lbl.setPixmap(QPixmap(str(ip)).scaled(
                34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        icon_lbl.setFixedSize(34, 34)
        col.addWidget(icon_lbl, 0, Qt.AlignLeft)
        col.addSpacing(12)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self.dash_btn = self._item(chr(0xF015), "Dashboard", page=0)   # house
        self.stats_btn = self._item(chr(0xF201), "Stats", page=1)      # chart-line
        col.addWidget(self.dash_btn)
        col.addWidget(self.stats_btn)
        col.addStretch(1)
        self.settings_btn = self._item(chr(0xF013), "Settings", page=2)  # gear
        col.addWidget(self.settings_btn)

        self.dash_btn.setChecked(True)
        self._group.idClicked.connect(self._on_select)

    def _item(self, glyph: str, label: str, page) -> QPushButton:
        # Icon only; the destination name lives on the tooltip.
        b = QPushButton(glyph, objectName="railBtn")
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolTip(label)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if page is not None:
            b.setCheckable(True)
            self._group.addButton(b, page)
        return b

    def reposition(self) -> None:
        """Anchor to the parent's left edge, full height."""
        p = self.parentWidget()
        if not p:
            return
        self.setGeometry(0, 0, self.COLLAPSED, p.height())


_PLAN_LABELS = {
    "default_claude_max_20x": "Max 20×",
    "default_claude_max_5x": "Max 5×",
    "default_claude_pro": "Pro",
    "default_claude_free": "Free",
}


def plan_label(tier: str | None) -> str:
    """Human label for an organization.rate_limit_tier (e.g. 'Max 5×')."""
    if not tier:
        return "Claude"
    return _PLAN_LABELS.get(
        tier, tier.replace("default_claude_", "").replace("_", " ").title())


class _StatsWorker(QThread):
    """Computes the monthly Stats aggregate off the UI thread (a month-long
    transcript scan + valuation). Emits the aggregate dict when done."""

    ready = Signal(object)

    def run(self) -> None:
        try:
            agg = stats.monthly_aggregate(time.time())
        except Exception:
            return
        self.ready.emit(agg)


class Dashboard(QMainWindow):
    def __init__(self, mock: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Clawdmeter")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        # The window height tracks its content (see _fit_window_height): it grows
        # and shrinks with the mascot shelf so there's no dead space below the
        # bars. This low floor only stops a manual drag from clipping badly; the
        # actual height is driven by the fit.
        self._min_window_h = 430
        # Width: ~3 shelf tiles (130px sprites + margins/spacing) fit without
        # scrolling; overflow scrolls horizontally inside the shelf's QScrollArea,
        # so the window never balloons sideways.
        # +NavRail.COLLAPSED so the shelf keeps room for ~3 tiles now that the
        # rail reserves the content's left edge (otherwise 3 sessions scroll).
        self.setMinimumSize(520 + NavRail.COLLAPSED, self._min_window_h)
        self.resize(520 + NavRail.COLLAPSED, 520)
        self.setStyleSheet(STYLESHEET)

        icon_path = assets_root() / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        self._root = root
        # Full-height nav rail down the left (overlay, created after content); the
        # rest of the UI sits in a right column whose left edge is reserved for the
        # collapsed rail. Keeping the rail outside the title bar means it — and the
        # mascot pinned at its top — survive the auto-hide title bar.
        self._outer = QHBoxLayout(root)
        self._outer.setContentsMargins(NavRail.COLLAPSED, 0, 0, 0)
        self._outer.setSpacing(0)

        right_col = QWidget()
        self._outer.addWidget(right_col, 1)
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.title_bar = TitleBar(self, on_toggle=self._toggle_full_compact,
                                  on_mini=lambda: self._set_view_mode("mini"))
        right_layout.addWidget(self.title_bar)

        content = QWidget()
        right_layout.addWidget(content, 1)

        # The mascot + bars now live on a "Dashboard page"; it and the Stats page
        # sit in a stack the nav rail switches between. `layout` still refers to
        # the dashboard page below, so the existing content code is unchanged.
        self.dashboard_page = QWidget()
        layout = QVBoxLayout(self.dashboard_page)
        layout.setContentsMargins(24, 14, 24, 11)
        layout.setSpacing(12)

        # Hero mascot for the EMPTY (0-session) state — the single rate-driven
        # mascot that's been the app's face from day one. Wrapped in its own
        # widget so the whole block can hide as a unit when the shelf is shown.
        self.sprite = SpritePlayer(size=240)
        self.hero = QWidget()
        sprite_row = QHBoxLayout(self.hero)
        sprite_row.setContentsMargins(0, 0, 0, 0)
        sprite_row.addStretch(1)
        sprite_row.addWidget(self.sprite)
        sprite_row.addStretch(1)
        layout.addWidget(self.hero)

        # Shelf of per-session mascots, shown whenever >=1 session is live. It
        # lives in the same slot as the hero and the two toggle visibility so
        # only one occupies the space at a time. Hidden until a session appears.
        self.shelf = SessionShelf()
        self.shelf.hide()
        layout.addWidget(self.shelf, 1)

        # Group label sits 6px above the session row (half of the main
        # layout's 12px) by nesting both into a sub-layout. The sub-layout
        # is then spaced 12px against the rest of the main layout.
        self.group_label = QLabel("IDLE", objectName="group", alignment=Qt.AlignCenter)
        group_session = QVBoxLayout()
        group_session.setContentsMargins(0, 0, 0, 0)
        group_session.setSpacing(6)
        group_session.addWidget(self.group_label)
        self.session_row, self.session_title, self.session_pct, self.session_bar, self.session_reset = self._build_row("SESSION (5h)")
        group_session.addLayout(self.session_row)
        layout.addLayout(group_session)
        self.weekly_row, self.weekly_title, self.weekly_pct, self.weekly_bar, self.weekly_reset = self._build_row("WEEKLY (7d)")
        layout.addLayout(self.weekly_row)

        # Status badge: only visible when nearing/at the rate limit. When
        # hidden it takes zero vertical space so the WEEKLY bar hugs the
        # bottom; when shown the layout grows to fit it.
        self.status_container = QWidget()
        status_row = QHBoxLayout(self.status_container)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        self.status_text = QLabel("", objectName="statusText")
        self.status_icon = QLabel("", objectName="statusIcon")
        status_row.addWidget(self.status_text)
        status_row.addWidget(self.status_icon)
        status_row.addStretch(1)
        self.status_container.setVisible(False)
        layout.addWidget(self.status_container)
        layout.addStretch(1)

        # Page stack (Dashboard + Stats) inside the content area. content_box
        # reserves the collapsed rail's width on the left so content never sits
        # under it; the rail itself is an overlay added after the settings panel.
        self.stats_page = self._build_stats_page()
        self._pages = QStackedWidget()
        self._pages.addWidget(self.dashboard_page)   # index 0
        self._pages.addWidget(self.stats_page)       # index 1
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 0, 0, 0)  # rail width reserved at root
        content_box.setSpacing(0)
        content_box.addWidget(self._pages)

        # Settings is the third page in the content stack — a nav-rail
        # destination, not an overlay. You leave it by clicking another rail item.
        self._content = content
        self.settings_panel = SettingsPanel(
            content,
            on_always_on_top_changed=self._set_always_on_top,
            on_auto_hide_changed=self._apply_auto_hide,
            on_refresh_token=self._request_token_refresh,
            on_auto_refresh_changed=self._set_auto_refresh,
            on_poll_interval_changed=self._set_poll_interval,
            on_sessions_view_changed=self._apply_session_view,
            on_token_view_changed=self._apply_token_view,
            on_check_updates=self._check_for_updates_now,
        )
        self._pages.addWidget(self.settings_panel)   # index 2 (Settings)

        # Full-height slim icon nav rail down root's left edge. Parented to root
        # (not content) so it spans the whole left side and the mascot at its top
        # stays put when the title bar auto-hides.
        self.nav_rail = NavRail(root, on_select=self._show_page)
        self.nav_rail.reposition()
        self.nav_rail.raise_()
        root.installEventFilter(self)

        # Apply persisted always-on-top before the first show().
        if app_settings.get_always_on_top():
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        # Auto-hide title bar: the title bar stays in the outer layout. When
        # mouse-revealed it animates its height 0 -> 48 AND the window's
        # height grows by the same delta in lockstep, so the content area
        # never gets squeezed and there's no empty band at the bottom.
        self._tb_max_anim = QPropertyAnimation(self.title_bar, b"maximumHeight", self)
        self._tb_min_anim = QPropertyAnimation(self.title_bar, b"minimumHeight", self)
        self._win_size_anim = QPropertyAnimation(self, b"size", self)
        for _a in (self._tb_max_anim, self._tb_min_anim, self._win_size_anim):
            _a.setDuration(180)
            _a.setEasingCurve(QEasingCurve.OutCubic)
        self._titlebar_anim_group = QParallelAnimationGroup(self)
        for _a in (self._tb_max_anim, self._tb_min_anim, self._win_size_anim):
            self._titlebar_anim_group.addAnimation(_a)

        self._mouse_poll = QTimer(self)
        self._mouse_poll.setInterval(80)
        self._mouse_poll.timeout.connect(self._check_titlebar_hover)
        self._auto_hide_enabled = False
        # Window height when the title bar is fully collapsed. Used as an
        # absolute reference so interrupted animations don't accumulate.
        self._collapsed_window_height: int | None = None
        self._apply_auto_hide(app_settings.get_auto_hide_titlebar())

        # Smoothly resizes the window height to fit content as the shelf changes.
        self._fit_anim = QPropertyAnimation(self, b"size", self)
        self._fit_anim.setDuration(240)  # matches the shelf enter/resize animation
        self._fit_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fit_anim.finished.connect(self._on_fit_anim_finished)
        # Auto-fit height state: we stop auto-fitting once the user drags the
        # window height themselves, so a manual size sticks (width is always free).
        self._auto_fit_height = True
        self._fitting = False        # True during our own height resize/animation
        self._fit_armed = False      # don't treat the first show as a user resize
        self._was_maximized = False

        self._rate = RateGroupTracker()
        self._reset_notifier = ResetNotifier()
        self._last_sample: UsageSample | None = None
        self._last_tooltip = ""
        self._transcript_state: TranscriptState | None = None
        # Last sessions from the watcher, re-rendered through the Settings toggles.
        self._last_raw_states: list[TranscriptState] = []
        # True while the shelf is showing >=1 live session. Tracked explicitly
        # (not via shelf.isVisible(), which is False until the window is shown)
        # so the usage-poll handler knows to leave the mascots to the shelf.
        self._shelf_active = False

        self._transcript = TranscriptWatcher(self)
        # sessions_changed drives the whole multi-mascot path (shelf + the
        # focused-session mini mascot + empty-state mood). state_changed is
        # the back-compat single-session signal; sessions_changed[0] carries the
        # same focused state, so we listen to the richer one only.
        self._transcript.sessions_changed.connect(self._on_sessions)
        # NOTE: started at the END of __init__, not here. start() does a
        # synchronous first poll that emits sessions_changed, and _on_sessions
        # touches widgets (self.mini, self.sprite, self.shelf) that aren't
        # built until later in __init__ — starting here AttributeErrors on
        # real-mode launch.

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon(str(icon_path)) if icon_path.exists() else QIcon(_tray_pixmap(0)))
        tray_menu = QMenu(self)
        self._tray_menu = tray_menu   # keep a reference so it isn't GC'd
        show_action = QAction("Show", self)
        show_action.triggered.connect(self._restore_view)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        # Explicit per-mode entries.
        for label, mode in (("Full view", "full"), ("Compact view", "compact"),
                            ("Mini view", "mini")):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, m=mode: self._set_view_mode(m))
            tray_menu.addAction(act)
        tray_menu.addSeparator()
        # Hidden until a newer release is found; clicking opens the download page.
        self._update_action = QAction("Update available", self)
        self._update_action.setVisible(False)
        self._update_action.triggered.connect(self._open_update_page)
        tray_menu.addAction(self._update_action)
        self._check_updates_action = QAction("Check for updates", self)
        self._check_updates_action.triggered.connect(self._check_for_updates_now)
        tray_menu.addAction(self._check_updates_action)
        tray_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._real_quit)
        tray_menu.addAction(quit_action)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        # Clicking the "update available" balloon should open the release page;
        # messageClicked is global, so the handler checks for a pending update.
        self._tray.messageClicked.connect(self._on_tray_message_clicked)
        self._update_info = None
        self._tray.setToolTip("Clawdmeter - starting…")
        self._tray.show()

        # Tray-flash state for the limit-reset notification.
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(400)
        self._flash_timer.timeout.connect(self._flash_tick)
        self._flash_alert_icon = QIcon(_tray_alert_pixmap())
        self._flash_saved_icon: QIcon | None = None
        self._flash_remaining = 0
        self._flash_on = False

        # Mini mode: a tiny always-on-top floating widget mirroring usage.
        self._view_mode = "full"
        self._mini_return_mode = "full"   # the view mini was entered from
        self.mini = MiniWidget()
        # Double-click / Expand on mini returns to whichever view it came from.
        self.mini.expand_requested.connect(
            lambda: self._set_view_mode(self._mini_return_mode))
        self.mini.quit_requested.connect(self._real_quit)

        # Compact mode: a denser list view (one row per session).
        self.compact_view = CompactView()
        self.compact_view.set_mode_requested.connect(self._set_view_mode)
        # Double-click the compact title bar also expands to full.
        self.compact_view.grow_requested.connect(lambda: self._set_view_mode("full"))
        self.compact_view.hide_requested.connect(self._stash_compact)  # to tray
        self.compact_view.quit_requested.connect(self._real_quit)

        # Custom limit-reset toast (replaces the native OS notification);
        # clicking it brings the dashboard forward.
        self._toast = ResetToast()
        self._toast.clicked.connect(self._show_window)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick_countdown)
        self._countdown.start()

        # Persisted usage history for Stats trends. Disk off in mock so synthetic
        # samples never land in the real on-disk history.
        self.usage_history = UsageHistory(persist=not mock)

        if mock:
            self._start_mock()
        else:
            self._start_poller()
            self._start_update_checker()
            # Now that self.mini / self.sprite / self.shelf exist, the
            # watcher's initial synchronous poll can safely drive the shelf.
            self._transcript.start()


    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.Resize and obj is self._root:
            self.nav_rail.reposition()   # rail spans full root height
        return super().eventFilter(obj, ev)

    def _show_page(self, idx: int) -> None:
        """Switch the content stack to a nav-rail destination (0=Dashboard,
        1=Stats, 2=Settings)."""
        self._pages.setCurrentIndex(idx)

    def _build_stats_page(self) -> QWidget:
        """Stats page: ROI + extra-usage spend, a per-day value strip, a 7x24
        activity heatmap, and a 'this month' recap. Scrolls when tall."""
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        body = QWidget(objectName="settingsBody")
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(24, 18, 24, 18)
        v.setSpacing(14)
        v.addWidget(QLabel("STATS", objectName="settingsTitle"))

        def num_card(label: str):
            f = QFrame(objectName="statCard")
            cl = QVBoxLayout(f)
            cl.setContentsMargins(16, 14, 16, 16)
            cl.setSpacing(3)
            cl.addWidget(QLabel(label, objectName="statLabel"))
            big = QLabel("—", objectName="statBig")
            cl.addWidget(big)
            sub = QLabel("", objectName="sectionHint")
            sub.setWordWrap(True)
            cl.addWidget(sub)
            v.addWidget(f)
            return big, sub

        def viz_card(label: str, widget: QWidget) -> None:
            f = QFrame(objectName="statCard")
            cl = QVBoxLayout(f)
            cl.setContentsMargins(16, 14, 16, 14)
            cl.setSpacing(8)
            cl.addWidget(QLabel(label, objectName="statLabel"))
            cl.addWidget(widget)
            v.addWidget(f)

        self.stat_value, self.stat_value_sub = num_card("API VALUE THIS MONTH")
        self.stat_spend, self.stat_spend_sub = num_card("EXTRA USAGE THIS MONTH")
        self.stat_cache, self.stat_cache_sub = num_card("CACHE SAVINGS THIS MONTH")
        self.stat_burn, self.stat_burn_sub = num_card("TIME TO 7-DAY CAP")

        self.stat_windows = PercentBars(empty_text="No per-model limits on your plan")
        viz_card("WEEKLY WINDOWS BY MODEL", self.stat_windows)

        self.stat_models = ModelBreakdown()
        viz_card("VALUE BY MODEL", self.stat_models)

        self.stat_bars = DailyBars()
        viz_card("VALUE PER DAY", self.stat_bars)

        self.stat_heat = Heatmap()
        viz_card("WHEN YOU WORK  ·  LOCAL TIME", self.stat_heat)

        wrap = QFrame(objectName="statCard")
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(16, 14, 16, 16)
        wl.setSpacing(4)
        wl.addWidget(QLabel("THIS MONTH", objectName="statLabel"))
        self.stat_wrap = QLabel("—", objectName="sectionHint")
        self.stat_wrap.setWordWrap(True)
        wl.addWidget(self.stat_wrap)
        v.addWidget(wrap)

        v.addStretch(1)
        return scroll

    def _on_aggregate(self, agg: dict) -> None:
        """Update the Stats trend visuals + recap from a computed aggregate, and
        feed the ROI card's dollar value."""
        self._agg = agg
        self._render_roi()
        cr, inp = agg.get("cache_read_tokens", 0), agg.get("input_tokens", 0)
        self.stat_cache.setText(f"${agg.get('cache_savings_usd', 0):,.2f}")
        pct = 100 * cr / (cr + inp) if (cr + inp) else 0
        self.stat_cache_sub.setText(f"{pct:.0f}% of input served from cache")
        self.stat_models.set_data(
            [(stats.model_display(m).replace("Claude ", ""), v)
             for m, v in agg.get("by_model_value", {}).items()])
        self.stat_bars.set_data(agg.get("value_by_day", []))
        self.stat_heat.set_data(agg.get("heatmap"))
        parts = []
        tm = agg.get("top_model")
        if tm and tm[1]:
            parts.append(f"Top model — {stats.model_display(tm[0])} (${tm[1]:,.0f})")
        bd = agg.get("busiest_day")
        if bd and bd[1]:
            parts.append(f"Busiest day — {bd[0]:%a %b %d} (${bd[1]:,.0f})")
        parts.append(f"{agg.get('turns', 0):,} turns over {agg.get('active_days', 0)} active days")
        self.stat_wrap.setText("\n".join(parts))

    def _refresh_stats(self) -> None:
        """Recompute the Stats aggregate off the UI thread (held ref so it lives
        until it finishes)."""
        self._stats_worker = _StatsWorker()
        self._stats_worker.ready.connect(self._on_aggregate)
        self._stats_worker.start()

    def _mock_aggregate(self) -> dict:
        """Synthetic Stats aggregate for --mock (no transcript scan)."""
        from datetime import date, timedelta
        today = date.today()
        series = [(today - timedelta(days=n), round(80 + abs(8 - (n % 17)) * 35.0, 2))
                  for n in range(16, -1, -1)]
        heat = [[(2 if 9 <= h <= 19 else 0) * (r + 1) + ((h * (r + 2)) % 7)
                 for h in range(24)] for r in range(7)]
        return {
            "value_total": 3380.0, "value_by_day": series, "heatmap": heat,
            "by_model_value": {"claude-opus-4-8": 2500.0, "claude-sonnet-4-6": 880.0},
            "top_model": ("claude-opus-4-8", 2500.0),
            "busiest_day": (today - timedelta(days=3), 412.0),
            "turns": 16704, "active_days": 12,
            "cache_savings_usd": 16999.36,
            "cache_read_tokens": 3_470_000_000, "input_tokens": 12_400_000,
        }

    def _update_stats(self, s: UsageSample) -> None:
        """Per-poll Stats update: the extra-usage spend card (from K1) and the
        ROI card's plan side. The ROI dollar value comes from the aggregate."""
        if s.extra_usage_enabled or s.extra_usage_used_usd:
            self.stat_spend.setText(f"${s.extra_usage_used_usd:,.2f}")
            cap = (f"of ${s.extra_usage_limit_usd:,.2f} cap"
                   if s.extra_usage_limit_usd else "no monthly cap")
            self.stat_spend_sub.setText(f"{plan_label(s.plan_tier)} · {cap}")
        else:
            self.stat_spend.setText("$0.00")
            self.stat_spend_sub.setText("Pay-as-you-go off")
        self._render_roi()
        self.stat_windows.set_data(list(s.model_windows.items()))   # per-model headroom
        self._update_burn(s)

    def _update_burn(self, s: UsageSample) -> None:
        """Time-to-cap card from the recent 7-day utilisation slope (K2 ring)."""
        if s.weekly_pct >= 100:
            self.stat_burn.setText("over")
            self.stat_burn_sub.setText("7-day window already in overage")
            return
        ring = getattr(self, "usage_history", None)
        points = [(p["ts"], p["w"]) for p in ring.ring] if ring else []
        now = time.time()
        eta = stats.cap_eta(points, s.weekly_pct, now)
        if eta is None:
            self.stat_burn.setText("—")
            self.stat_burn_sub.setText("not on pace to cap (gathering data)")
            return
        secs = eta - now
        reset_secs = (s.weekly_reset_minutes or 0) * 60
        if reset_secs and secs > reset_secs:
            self.stat_burn.setText("—")
            self.stat_burn_sub.setText("on pace to reset before capping")
            return
        if secs >= 86400:
            amt = f"~{secs / 86400:.1f} days"
        elif secs >= 3600:
            amt = f"~{secs / 3600:.0f} hours"
        else:
            amt = f"~{max(1, int(secs / 60))} min"
        self.stat_burn.setText(amt)
        self.stat_burn_sub.setText("until the 7-day cap · at current pace")

    def _render_roi(self) -> None:
        """ROI card from the latest aggregate (the dollar value) + the latest
        sample (the plan tier). Either source updating refreshes the card."""
        agg = getattr(self, "_agg", None)
        if agg is None:                       # aggregate not computed yet
            self.stat_value.setText("—")
            self.stat_value_sub.setText("computing…")
            return
        val = agg["value_total"]
        self.stat_value.setText(f"${val:,.2f}")
        s = getattr(self, "_last_sample", None)
        tier = s.plan_tier if s else None
        price = stats.plan_monthly_usd(tier)
        if price and val:
            mult = val / price
            mult_s = f"{mult:.0f}×" if mult >= 10 else f"{mult:.1f}×"
            self.stat_value_sub.setText(
                f"{plan_label(tier)} plan · ${price:,.0f}/mo · {mult_s} the subscription")
        else:
            self.stat_value_sub.setText("of pay-as-you-go API value this month")

    def _set_always_on_top(self, on: bool) -> None:
        """Zero-flicker topmost via SetWindowPos. Qt's setWindowFlag forces a
        window re-creation, which flickers; SetWindowPos changes the OS-level
        WS_EX_TOPMOST bit on the existing HWND."""
        winutil.set_topmost(int(self.winId()), on)

    def _apply_auto_hide(self, on: bool) -> None:
        """Toggle the auto-hide title bar feature.

        Title bar stays in the outer layout in both modes. When enabled it
        starts collapsed (min/max height 0) and grows on hover. Window
        height is animated in lockstep so the content area never changes
        size — title bar growth pushes the bottom edge down, not into
        content.
        """
        if self._auto_hide_enabled == on:
            return
        self._auto_hide_enabled = on
        self._titlebar_anim_group.stop()
        h = TitleBar.HEIGHT

        if on:
            self.title_bar.setMinimumHeight(0)
            self.title_bar.setMaximumHeight(0)
            self.setMinimumHeight(self.minimumHeight() - h)
            new_h = max(self.minimumHeight(), self.height() - h)
            self.resize(self.width(), new_h)
            self._collapsed_window_height = new_h
            self._mouse_poll.start()
        else:
            self._mouse_poll.stop()
            self.title_bar.setMinimumHeight(h)
            self.title_bar.setMaximumHeight(h)
            self.setMinimumHeight(self.minimumHeight() + h)
            self.resize(self.width(), self.height() + h)
            self._collapsed_window_height = None

    def _check_titlebar_hover(self) -> None:
        """Reveal title bar when mouse is near the top edge; hide otherwise.

        Hysteresis: when hidden, only the top 8 px reveals; when visible, the
        cursor has to leave the full title-bar region (48 px) to hide.
        """
        local = self.mapFromGlobal(QCursor.pos())
        in_window = self.rect().contains(local)
        visible = self.title_bar.maximumHeight() >= TitleBar.HEIGHT // 2
        threshold = TitleBar.HEIGHT if visible else 8

        if in_window and local.y() < threshold:
            self._reveal_titlebar()
        else:
            self._hide_titlebar()

    def _animate_titlebar_to(self, target_height: int) -> None:
        """Animate title bar height + window height in parallel to absolute
        targets so that restarting the animation mid-flight never accumulates."""
        if self._collapsed_window_height is None:
            return
        current = self.title_bar.maximumHeight()
        if current == target_height:
            return
        target_win_h = self._collapsed_window_height + target_height
        self._titlebar_anim_group.stop()
        self._tb_max_anim.setStartValue(current)
        self._tb_max_anim.setEndValue(target_height)
        self._tb_min_anim.setStartValue(self.title_bar.minimumHeight())
        self._tb_min_anim.setEndValue(target_height)
        self._win_size_anim.setStartValue(self.size())
        self._win_size_anim.setEndValue(QSize(self.width(), target_win_h))
        self._titlebar_anim_group.start()

    def resizeEvent(self, event) -> None:
        """Update the collapsed-height baseline when the user resizes manually.
        We only do this when no animation is in flight so that animation ticks
        don't poison the baseline."""
        super().resizeEvent(event)
        if (
            self._auto_hide_enabled
            and self._titlebar_anim_group.state() == QAbstractAnimation.Stopped
        ):
            self._collapsed_window_height = self.height() - self.title_bar.maximumHeight()

    def _reveal_titlebar(self) -> None:
        self._animate_titlebar_to(TitleBar.HEIGHT)

    def _hide_titlebar(self) -> None:
        self._animate_titlebar_to(0)

    def nativeEvent(self, eventType, message):
        """Handle WM_NCHITTEST so Windows itself does edge-resize: cursor
        changes, edge snap, etc. all come for free."""
        if eventType == b"windows_generic_MSG":
            msg = winutil.parse_msg(message)
            if msg.message == winutil.WM_NCHITTEST and not self.isMaximized():
                # Use Qt's own logical cursor position so the coordinate space
                # matches mapFromGlobal(). The native lParam is in physical
                # pixels, which mismatches Qt's device-independent geometry
                # under high-DPI scaling (e.g. 200%) and makes the whole client
                # area read as a resize border. See issue #7.
                local = self.mapFromGlobal(QCursor.pos())
                hit = winutil.hit_test(local.x(), local.y(), self.width(), self.height())
                if hit != winutil.HTCLIENT:
                    return True, hit
        return super().nativeEvent(eventType, message)

    def _build_row(self, label_text: str):
        outer = QVBoxLayout()
        outer.setSpacing(4)
        header = QHBoxLayout()
        label = QLabel(label_text, objectName="rowLabel")
        pct = QLabel("-", objectName="pct")
        pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(label)
        header.addStretch(1)
        header.addWidget(pct)
        bar = UsageBar(height=14)
        reset = QLabel("resets in -", objectName="reset")
        outer.addLayout(header)
        outer.addWidget(bar)
        outer.addWidget(reset)
        return outer, label, pct, bar, reset

    def _start_poller(self) -> None:
        self._poller = UsagePoller(interval_seconds=app_settings.get_poll_interval())
        self._poller.sample.connect(self._on_sample)
        self._poller.refresh_status.connect(self._on_refresh_status)
        self._poller.start()
        # Stats trend aggregate: compute now + every 10 min, off the UI thread.
        self._refresh_stats()
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(600_000)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start()

    def _start_update_checker(self) -> None:
        self._update_checker = UpdateChecker()
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.check_finished.connect(self._on_update_check_finished)
        self._update_checker.start()

    def _on_update_available(self, info) -> None:
        """A newer release was found (background or manual check)."""
        self._update_info = info
        self._update_action.setText(f"Update available ({info.version}) — get it")
        self._update_action.setVisible(True)
        self._tray.setToolTip(f"Clawdmeter — update {info.version} available")
        self._tray.showMessage(
            "Clawdmeter update available",
            f"Version {info.version} is out. Click here, or use the tray menu, "
            "to open the download page.",
            QSystemTrayIcon.MessageIcon.Information, 10000,
        )
        self._start_tray_flash()

    def _on_update_check_finished(self, info) -> None:
        """Feedback for a manual 'Check for updates' (info is None when current)."""
        if info is None:
            self._tray.showMessage(
                "Clawdmeter",
                f"You're on the latest version ({app_settings.APP_VERSION}).",
                QSystemTrayIcon.MessageIcon.Information, 5000,
            )

    def _check_for_updates_now(self) -> None:
        checker = getattr(self, "_update_checker", None)
        if checker is None:
            self._tray.showMessage(
                "Clawdmeter", "Update check isn't available in mock mode.",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )
            return
        self._tray.showMessage(
            "Clawdmeter", "Checking for updates…",
            QSystemTrayIcon.MessageIcon.Information, 3000,
        )
        checker.request_check()

    def _open_update_page(self) -> None:
        info = getattr(self, "_update_info", None)
        QDesktopServices.openUrl(QUrl(info.url if info else update_check.RELEASES_PAGE))

    def _on_tray_message_clicked(self) -> None:
        # Only act if there's a pending update — other balloons are informational.
        if getattr(self, "_update_info", None) is not None:
            self._open_update_page()

    def _request_token_refresh(self) -> None:
        poller = getattr(self, "_poller", None)
        if poller is None:
            self.settings_panel.set_token_status("Not available in mock mode.")
            return
        poller.request_manual_refresh()

    def _set_auto_refresh(self, on: bool) -> None:
        poller = getattr(self, "_poller", None)
        if poller is not None:
            poller.set_auto_refresh(on)

    def _set_poll_interval(self, seconds: int) -> None:
        poller = getattr(self, "_poller", None)
        if poller is not None:
            poller.set_interval(seconds)

    def _on_refresh_status(self, result) -> None:
        """token_refresh.RefreshResult from the poll thread (auto or manual)."""
        if result.ok:
            self.settings_panel.refresh_token_status()
        else:
            self.settings_panel.set_token_status("⚠ " + result.status)

    def _start_mock(self) -> None:
        self._mock_pct = 12
        self._mock_sample_timer = QTimer(self)

        def sample_tick():
            # Cycle 0..129 so both windows cross 100% — the per-window red
            # overage state on the SESSION and WEEKLY bars is visible in mock
            # (weekly is offset so the two cross at different times).
            self._mock_pct = (self._mock_pct + 1) % 130
            self._on_sample(UsageSample(
                session_pct=self._mock_pct,
                session_reset_minutes=140,
                weekly_pct=(self._mock_pct + 65) % 130,
                weekly_reset_minutes=4 * 24 * 60 + 6 * 60,
                status="ok (mock)",
                ok=True,
                error=None,
                timestamp=time.time(),
                tokens_5h=914_000,
                tokens_7d=19_400_000,
                plan_tier="default_claude_max_5x",
                extra_usage_enabled=True,
                extra_usage_used_usd=round(self._mock_pct * 0.3, 2),
                model_windows={"Opus": 62, "Sonnet": 18},
            ))
        self._mock_sample_timer.timeout.connect(sample_tick)
        self._mock_sample_timer.start(800)
        sample_tick()
        self._on_aggregate(self._mock_aggregate())  # synthetic trends for --mock



        # Drive the shelf through a scripted roster that grows 1->4 sessions and
        # shrinks back, reordering and cycling activities along the way, so every
        # transition (tile enter/leave, resize, reorder, live<->idle) is on show
        # without launching concurrent Claude Code windows. Steps every 2s.
        self._mock_phase = 0
        self._mock_shelf_timer = QTimer(self)
        self._mock_shelf_timer.timeout.connect(self._emit_mock_sessions)
        self._mock_shelf_timer.start(2000)
        self._emit_mock_sessions()

    # Pool of fake sessions (session_id, label) the mock roster draws on. Labels
    # mirror the real precedence (custom/ai title, else cwd leaf): a mix of long
    # session titles (elided + tooltip) and short cwd fallbacks.
    _MOCK_POOL = [
        ("mock-clawdmeter", "Review clawdmeter UI design and implementation"),
        ("mock-api-gateway", "api-gateway"),
        ("mock-notes-cli", "Fix login redirect bug"),
        ("mock-data-pipeline", "data-pipeline"),
    ]

    # Each step lists the active pool indices, newest-first. The shelf is driven
    # through add (1->4), reorder (data-pipeline jumps to front) and remove
    # (4->1), exercising the tile enter/leave + count-based resize each step.
    _MOCK_SCHEDULE = [
        [0],
        [0, 1],
        [0, 1, 2],
        [0, 1, 2, 3],
        [3, 0, 1, 2],
        [3, 0, 1],
        [3, 0],
        [0],
    ]

    # Activities the live tiles rotate through, so the per-activity glow colors
    # and animations change over time.
    _MOCK_ACTIVITY_CYCLE = [
        TranscriptActivity.CODING,
        TranscriptActivity.THINKING,
        TranscriptActivity.READING,
        TranscriptActivity.SEARCHING,
        TranscriptActivity.PLANNING,
        TranscriptActivity.INTEGRATING,
    ]

    # The FOCUSED session (the one single mode shows) steps through every
    # expression so all moods are on display for screenshots:
    # (activity, tool label, subagent count, stale).
    _FOCUS_CYCLE = [
        (TranscriptActivity.CODING, "Edit", 0, False),
        (TranscriptActivity.READING, "Read", 0, False),
        (TranscriptActivity.SEARCHING, "WebSearch", 0, False),
        (TranscriptActivity.THINKING, None, 0, False),
        (TranscriptActivity.INTEGRATING, "github/list_issues", 0, False),
        (TranscriptActivity.PLANNING, "TodoWrite", 0, False),
        (TranscriptActivity.PLANNING, None, 3, False),  # supervising subagents
        (TranscriptActivity.IDLE, None, 0, True),       # idle / "last active …"
    ]

    def _mock_agent_list(self, phase: int, n: int) -> list:
        cyc = self._MOCK_ACTIVITY_CYCLE
        return [
            TranscriptAgentState(
                agent_id=f"mock-agent-{k}",
                activity=cyc[(phase + k) % len(cyc)],
                tool_name=None,
                is_stale=False,
            )
            for k in range(n)
        ]

    def _focus_state(self, phase: int, sid: str, project: str, now: float) -> TranscriptState:
        """Build the focused session for this phase, cycling all expressions."""
        act, tool, n_agents, stale = self._FOCUS_CYCLE[phase % len(self._FOCUS_CYCLE)]
        if n_agents:
            return TranscriptState(
                activity=TranscriptActivity.PLANNING, tool_name=None,
                transcript_path=None, last_event_ts=now, session_id=sid, cwd=None,
                project_name=project, is_stale=False,
                agents=self._mock_agent_list(phase, n_agents),
            )
        if stale:
            return TranscriptState(
                activity=TranscriptActivity.IDLE, tool_name=None,
                transcript_path=None, last_event_ts=now - 4 * 60, session_id=sid,
                cwd=None, project_name=project, is_stale=True,
            )
        return TranscriptState(
            activity=act, tool_name=tool, transcript_path=None, last_event_ts=now,
            session_id=sid, cwd=None, project_name=project, is_stale=False,
        )

    def _emit_mock_sessions(self) -> None:
        phase = self._mock_phase
        self._mock_phase += 1
        now = time.time()
        active = self._MOCK_SCHEDULE[phase % len(self._MOCK_SCHEDULE)]
        states = []
        for slot, pool_idx in enumerate(active):
            sid, project = self._MOCK_POOL[pool_idx]
            if slot == 0:
                # Focused session — single mode shows only this, so cycle it
                # through the full expression set.
                states.append(self._focus_state(phase, sid, project, now))
                continue
            # Other tiles (multi mode only): cycle activities; oldest reads idle.
            if slot == len(active) - 1:
                states.append(TranscriptState(
                    activity=TranscriptActivity.IDLE, tool_name=None,
                    transcript_path=None, last_event_ts=now - 4 * 60, session_id=sid,
                    cwd=None, project_name=project, is_stale=True,
                ))
            else:
                act = self._MOCK_ACTIVITY_CYCLE[
                    (phase + pool_idx) % len(self._MOCK_ACTIVITY_CYCLE)
                ]
                states.append(TranscriptState(
                    activity=act,
                    tool_name="Edit" if act == TranscriptActivity.CODING else None,
                    transcript_path=None, last_event_ts=now, session_id=sid, cwd=None,
                    project_name=project, is_stale=False,
                ))
        # Give each mock session a distinct token tally (hover breakdown) and a
        # demo "target" so the sub-label shows what's being acted on.
        mock_targets = {
            TranscriptActivity.CODING: "dashboard.py",
            TranscriptActivity.READING: "transcript.py",
            TranscriptActivity.SEARCHING: "qt elided label",
            TranscriptActivity.INTEGRATING: "list_issues",
        }
        for i, st in enumerate(states):
            base = i + 1
            st.tokens = TokenUsage(
                input=base * 220_000, output=base * 410_000,
                cache_read=base * 9_800_000, cache_write=base * 180_000,
            )
            st.target = mock_targets.get(st.activity)
        self._on_sessions(states)

    def _on_sample(self, s: UsageSample) -> None:
        # Feed every sample (incl. errors) so the notifier can ignore them
        # without disturbing its baseline.
        decision = self._reset_notifier.observe(s)
        self._last_sample = s
        self.usage_history.record(s)   # ring + throttled disk log (skips errors)
        if not s.ok:
            self._apply_status_badge(s.status)
            self._tray.setToolTip(f"Clawdmeter - {s.status}")
            self._last_tooltip = ""  # force a fresh stats tooltip on recovery
            return

        # Each window handles its own overage: once 5h / 7d crosses 100% the bar
        # restarts red and a red OVERAGE tag joins its title.
        apply_overage_bar(self.session_title, self.session_pct, self.session_bar,
                          "SESSION (5h)", s.session_pct)
        apply_overage_bar(self.weekly_title, self.weekly_pct, self.weekly_bar,
                          "WEEKLY (7d)", s.weekly_pct)

        self._refresh_reset_lines(
            s, s.session_reset_minutes, s.weekly_reset_minutes)

        self._sync_mini(s)
        self._update_compact_usage(
            s, s.session_reset_minutes, s.weekly_reset_minutes)
        self._update_stats(s)

        self._rate.observe(s.session_pct)
        # While the shelf is up it owns the mascots (per-session tiles + the
        # mini widget via _drive_mini_from), so the rate-based mood must
        # NOT also drive them here — two paths with different set_anims keys for
        # the same activity would restart and flicker the mini animation on
        # every usage poll. Only refresh the rate mood in the empty state.
        if not self._shelf_active:
            self._update_sprite_selection()

        self._apply_status_badge(s.status)
        self._set_tray_tooltip(s.session_pct, s.session_reset_minutes,
                               s.weekly_pct, s.weekly_reset_minutes)

        # Fire last, so the UI already reflects the post-reset state before we
        # (optionally) pop the window to the foreground.
        if decision.notify and app_settings.get_reset_notify():
            self._fire_reset_notification(decision)

    def _fire_reset_notification(self, decision: ResetDecision) -> None:
        """Surface a gated limit reset via the user's chosen methods."""
        which = " & ".join(r.capitalize() for r in decision.reasons) or "Usage"
        title = "Claude limit reset"
        body = f"{which} limit has reset — you can resume."

        # Windows channel: the themed toast + tray flash, with sound and
        # window-pop as its sub-options. Off -> the push channel can still fire,
        # so the user can choose to be notified only on their phone/Discord.
        if app_settings.get_reset_notify_toast():
            self._toast.show_message(title, body)
            self._start_tray_flash()
            if app_settings.get_reset_notify_sound():
                QApplication.beep()
            if app_settings.get_reset_notify_popup():
                self._show_window()
        if app_settings.get_reset_notify_push():
            self._send_push(title, body)

    def _send_push(self, title: str, body: str) -> None:
        """Fire the phone push off the UI thread; a failure is logged, not raised."""
        if not _push_configured():  # nothing to send to — stay a clean no-op
            return

        def worker() -> None:
            ok, msg = _dispatch_push(title, body)
            # The frozen app runs windowed (console=False), where stderr is None
            # and print() would raise — guard so the failure stays silent-but-safe.
            if not ok and sys.stderr is not None:
                sys.stderr.write(f"[clawdmeter] {msg}\n")

        threading.Thread(target=worker, name="push-notify", daemon=True).start()

    def _start_tray_flash(self, cycles: int = 6) -> None:
        if self._flash_timer.isActive():  # already flashing — just extend it
            self._flash_remaining = max(self._flash_remaining, cycles * 2)
            return
        self._flash_saved_icon = self._tray.icon()  # snapshot the real icon
        self._flash_remaining = cycles * 2
        self._flash_on = False
        self._flash_timer.start()

    def _flash_tick(self) -> None:
        if self._flash_remaining <= 0:
            self._flash_timer.stop()
            if self._flash_saved_icon is not None:
                self._tray.setIcon(self._flash_saved_icon)  # restore exactly
                self._flash_saved_icon = None
            return
        self._flash_on = not self._flash_on
        self._tray.setIcon(
            self._flash_alert_icon if self._flash_on
            else (self._flash_saved_icon or self._tray.icon())
        )
        self._flash_remaining -= 1

    def _set_tray_tooltip(self, session_pct: int, session_reset: int,
                          weekly_pct: int, weekly_reset: int) -> None:
        """Tray hover tooltip with live session/weekly usage. Shared by the
        60s poll and the 1s countdown tick so the reset times stay current."""
        text = (
            "Clawdmeter\n"
            f"Session {session_pct}% (resets {_format_minutes(session_reset)})\n"
            f"Weekly {weekly_pct}% (resets {_format_minutes(weekly_reset)})"
        )
        s = self._last_sample
        if s is not None and app_settings.get_show_token_usage():
            text += (
                f"\nTokens {fmt_tokens(s.tokens_5h)} (5h) · "
                f"{fmt_tokens(s.tokens_7d)} (7d)"
            )
        if text != self._last_tooltip:
            self._last_tooltip = text
            self._tray.setToolTip(text)

    def _target_window_height(self) -> int:
        """Snug window height for the current content: the title bar plus the
        content area, counting the shelf at its settled (target) height rather
        than a value still mid-animation, so the fit aims at the final size."""
        tb_h = self.title_bar.height() or TitleBar.HEIGHT  # may be 0 before show
        content_min = self._content.minimumSizeHint().height()
        if self._shelf_active:
            content_min += self.shelf.reserved_target() - self.shelf.reserved_current()
        return tb_h + content_min

    def _fit_window_height(self) -> None:
        """Resize the window's height to hug its content so there's no dead space
        below the bars; the height follows the shelf as it grows/shrinks. Does
        nothing once the user has set their own height (auto-fit released)."""
        if not self._auto_fit_height:
            return
        if self.isMaximized() or self.isFullScreen():
            return
        if self._titlebar_anim_group.state() == QAbstractAnimation.Running:
            return  # don't fight the auto-hide title-bar resize
        target = max(self.minimumHeight(), self._target_window_height())
        if abs(target - self.height()) <= 1:
            return
        if not self.isVisible():
            self._fitting = True
            self.resize(self.width(), target)  # snap before the first show
            self._fitting = False
            return
        self._fit_anim.stop()
        self._fitting = True  # set AFTER stop() so a restart stays guarded
        self._fit_anim.setStartValue(self.size())
        self._fit_anim.setEndValue(QSize(self.width(), target))
        self._fit_anim.start()

    def _on_fit_anim_finished(self) -> None:
        self._fitting = False

    def reset_to_fit(self) -> None:
        """Re-enable auto-fit and snap back to the snug content height — the
        title-bar double-click 'reset' after a manual height resize."""
        if self.isMaximized():
            self.showNormal()
        self._auto_fit_height = True
        self._fit_window_height()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Arm user-resize detection only after the show settles, so the initial
        # show geometry isn't mistaken for a manual height drag.
        QTimer.singleShot(0, lambda: setattr(self, "_fit_armed", True))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        old = event.oldSize()
        max_involved = self.isMaximized() or self._was_maximized
        self._was_maximized = self.isMaximized()
        if not self._auto_fit_height:
            return
        height_changed = old.height() > 0 and event.size().height() != old.height()
        if _should_release_autofit(
            height_changed,
            self._fitting,
            self._fit_armed,
            max_involved,
            self._titlebar_anim_group.state() == QAbstractAnimation.Running,
        ):
            self._auto_fit_height = False  # respect the user's height from now on

    def _apply_status_badge(self, status: str) -> None:
        """Show/hide the bottom-left rate-limit badge and reflow the window.

        `anthropic-ratelimit-unified-5h-status` is `allowed` / `allowed_warning`
        / `rejecting` / etc. Only warn and blocked are surfaced. The status
        container is hidden when there's nothing to say so the WEEKLY bar
        sits tight against the bottom; minimum window height grows by the
        badge row's footprint when it appears.
        """
        s = (status or "").lower()
        if "reject" in s or "block" in s:
            self.status_text.setText("Limit reached")
            self.status_icon.setText("❌")
            self.status_text.setProperty("level", "block")
            has_badge = True
        elif "warn" in s:
            self.status_text.setText("Nearing limit")
            self.status_icon.setText("⚠️")
            self.status_text.setProperty("level", "warn")
            has_badge = True
        else:
            self.status_text.clear()
            self.status_icon.clear()
            self.status_text.setProperty("level", "")
            has_badge = False

        self.status_container.setVisible(has_badge)
        # Property selectors don't re-evaluate until restyled.
        self.status_text.style().unpolish(self.status_text)
        self.status_text.style().polish(self.status_text)

        # The badge row changes the content height; refit so the window grows to
        # show it (and shrinks back when it clears) with no dead space.
        self._fit_window_height()

    def _reset_line(self, minutes: int, tokens: int) -> str:
        line = f"resets in {_format_minutes(minutes)}"
        if app_settings.get_show_token_usage():
            line += f" · {fmt_tokens(tokens)}"
        return line

    def _refresh_reset_lines(self, s: UsageSample, sr: int, wr: int) -> None:
        """Render the session/weekly reset lines with the per-window token total
        appended when the token display is on. Shared by the poll handler and the
        1s countdown so they never disagree. Overage is part of the same window
        now (the bar/title go red past 100%), so the reset is just the 5h / 7d
        reset — no separate overage clock."""
        self.session_reset.setText(self._reset_line(sr, s.tokens_5h))
        self.weekly_reset.setText(self._reset_line(wr, s.tokens_7d))

    def _tick_countdown(self) -> None:
        s = self._last_sample
        if not s or not s.ok:
            return
        elapsed_min = int((time.time() - s.timestamp) // 60)
        sr = max(0, s.session_reset_minutes - elapsed_min)
        wr = max(0, s.weekly_reset_minutes - elapsed_min)
        self._refresh_reset_lines(s, sr, wr)
        self.mini.set_resets(sr, wr)
        self._update_compact_usage(s, sr, wr)
        self._set_tray_tooltip(s.session_pct, sr, s.weekly_pct, wr)

    def _on_sessions(self, states: list[TranscriptState]) -> None:
        """Receive the watcher's per-session states, remember them, and render
        through the current Settings view (multiple-sessions / subagents toggles)."""
        self._last_raw_states = list(states)
        self._apply_session_view()

    def _apply_session_view(self) -> None:
        """Render the last-seen sessions honouring the Settings toggles: collapse
        to the focused session when 'show multiple sessions' is off, and strip
        child agents when 'show subagents' is off. Called on each watcher update
        and whenever a toggle changes, so a flip takes effect immediately.

        With >=1 live session the shelf takes over the mascot slot and the mini
        widget mirrors the focused (newest) session; with 0 the hero returns and
        the rate-based mood drives hero + mini + group_label."""
        states = _view_states(
            self._last_raw_states,
            app_settings.get_show_multiple_sessions(),
            app_settings.get_show_subagents(),
        )

        cv = getattr(self, "compact_view", None)
        if cv is not None and cv.isVisible():
            cv.set_show_tokens(app_settings.get_show_token_usage())
            cv.set_sessions(states)

        if states:
            self._shelf_active = True
            self.hero.hide()
            # Pause the hidden hero so its 240px mascot isn't animating offscreen
            # the whole time the shelf is up.
            self.sprite.stop()
            # The shelf owns its own header, so hide the rate-mood group label
            # rather than leave it showing stale text beside live tiles.
            self.group_label.hide()
            self.shelf.show()
            # The "ACTIVE SESSIONS — N" count is only meaningful in multi mode.
            self.shelf.set_header_visible(app_settings.get_show_multiple_sessions())
            self.shelf.set_show_tokens(app_settings.get_show_token_usage())
            self.shelf.set_sessions(states)
            # The mini widget stays single-mascot, so it follows the focused
            # (newest) session.
            self._transcript_state = states[0]
            self._drive_mini_from(states[0])
        else:
            self._shelf_active = False
            self.shelf.hide()
            self.shelf.set_sessions([])  # drop any leftover tiles + reset header
            self.hero.show()
            self.group_label.show()
            # No session: fall back to today's rate-driven mood for hero/mini.
            self._transcript_state = None
            self._update_sprite_selection()
            # set_anims no-ops on an unchanged key, so re-show the paused hero —
            # but only when the full window is actually the visible view (in
            # compact/mini the hero is hidden and must stay paused).
            if self._view_mode == "full":
                self.sprite.resume()

        # Resize the window to hug the new content (no dead space below the bars).
        self._fit_window_height()

    def _apply_token_view(self) -> None:
        """Token-usage display toggled in Settings: re-render the per-bar token
        figures + tray and refresh the shelf's hover breakdown immediately."""
        s = self._last_sample
        if s is not None and s.ok:
            # Toggled ON: the last sample was polled with tokens off (windows 0),
            # so fill them in now rather than showing "· 0" until the next poll.
            if (app_settings.get_show_token_usage()
                    and not s.tokens_5h and not s.tokens_7d):
                try:
                    s.tokens_5h, s.tokens_7d = account_window_tokens(time.time())
                except OSError:
                    pass
            elapsed_min = int((time.time() - s.timestamp) // 60)
            sr = max(0, s.session_reset_minutes - elapsed_min)
            wr = max(0, s.weekly_reset_minutes - elapsed_min)
            self._refresh_reset_lines(s, sr, wr)
            self._update_compact_usage(s, sr, wr)
            self._set_tray_tooltip(s.session_pct, sr, s.weekly_pct, wr)
        # Re-render the shelf so mascot-hover tooltips appear/disappear at once.
        self._apply_session_view()

    def _drive_mini_from(self, state: TranscriptState) -> None:
        """Mirror one session's activity on the mini mascot only — the hero
        is hidden while the shelf is up, so it isn't driven here. Idle/stale
        sessions fall back to the calm group-0 loop."""
        idle = state.is_stale or state.activity == TranscriptActivity.IDLE
        anims = None if idle else ACTIVITY_ANIMS.get(state.activity)
        if anims:
            self.mini.sprite.set_anims(f"mini:{state.activity.value}", anims)
        else:
            self.mini.sprite.set_anims("mini:idle", GROUP_ANIMS[0])

    def _set_sprite_anims(self, key: str, names) -> None:
        """Drive both the full-window and mini mascots in lockstep."""
        self.sprite.set_anims(key, names)
        self.mini.sprite.set_anims(key, names)

    def _update_sprite_selection(self) -> None:
        """Transcript-driven activity takes precedence; rate-based when idle."""
        ts = self._transcript_state
        if ts and ts.activity != TranscriptActivity.IDLE:
            anims = ACTIVITY_ANIMS.get(ts.activity)
            if anims:
                self._set_sprite_anims(f"transcript:{ts.activity.value}", anims)
                label = ACTIVITY_LABELS[ts.activity]
                if ts.tool_name:
                    label = f"{label} — {ts.tool_name}"
                self.group_label.setText(label)
                return

        group_id = self._rate.group()
        self._set_sprite_anims(f"group:{group_id}", GROUP_ANIMS[group_id])
        self.group_label.setText(GROUP_NAMES[group_id].upper())

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self._restore_view()

    def _sync_mini(self, s: UsageSample) -> None:
        """Push a usage sample into the mini widget. Reset times use the same
        relative 'resets in 4h 56m' form as the main window."""
        self.mini.update_usage(
            s.session_pct, s.weekly_pct,
            s.session_reset_minutes, s.weekly_reset_minutes,
        )

    # The title-bar button cycles forward (full -> compact -> mini -> full);
    # double-click / "grow" steps back toward full; the tray restores the last
    # mode. Order math lives in module-level next_view_mode/grow_view_mode.
    def _set_view_mode(self, mode: str, persist: bool = True) -> None:
        """Switch to full / compact / mini. Persists the choice unless persist
        is False (transient foregrounding for a notification must not overwrite
        the user's chosen mode)."""
        if mode not in VIEW_ORDER:
            mode = "full"
        # Remember which view we drop into mini FROM, so expanding mini returns
        # there (full->mini->full, compact->mini->compact).
        if mode == "mini" and self._view_mode in ("full", "compact"):
            self._mini_return_mode = self._view_mode
        self._view_mode = mode
        if persist:
            app_settings.set_view_mode(mode)
        # Keep both switchers' active segment in sync with the real mode.
        self.title_bar.set_active_mode(mode)
        self.compact_view.set_active_mode(mode)
        self._stash_mini()
        self._stash_compact()
        if mode == "full":
            self.showNormal()
            self.raise_()
            self.activateWindow()
            # The hero only animates in the empty (0-session) full view; resume
            # it here in case it was paused while hidden in compact/mini.
            if not self._shelf_active:
                self.sprite.resume()
        else:
            # Hidden in compact/mini — pause the full window's hero so its
            # 240px mascot isn't animating offscreen.
            self.sprite.stop()
            self.hide()
            self._show_compact() if mode == "compact" else self._show_mini()

    def _toggle_full_compact(self) -> None:
        """The square-caret toggle: full <-> compact."""
        self._set_view_mode("full" if self._view_mode == "compact" else "compact")

    def _restore_view(self) -> None:
        """Tray click / 'Show' / pop-to-front: re-show the last-used mode without
        re-persisting it (it's already the saved value)."""
        self._set_view_mode(getattr(self, "_view_mode", "full"), persist=False)

    def show_initial(self) -> None:
        """Launch into the last-used view mode directly (no full-window flash)."""
        mode = app_settings.get_view_mode()
        if mode == "full":
            self.show()
        else:
            self._set_view_mode(mode, persist=False)

    def _stash_mini(self) -> None:
        if self.mini.isVisible():
            app_settings.set_mini_pos(self.mini.x(), self.mini.y())
            self.mini.hide()

    def _stash_compact(self) -> None:
        if self.compact_view.isVisible():
            app_settings.set_compact_pos(
                self.compact_view.x(), self.compact_view.y())
            self.compact_view.hide()

    def _default_corner(self, w: int, h: int, top: bool) -> tuple[int, int]:
        scr = self.screen() or QGuiApplication.primaryScreen()
        geo = scr.availableGeometry()
        x = geo.right() - w - 24
        y = (geo.top() + 24) if top else (geo.bottom() - h - 24)
        return x, y

    def _onscreen(self, x: int, y: int, w: int) -> bool:
        """True if a chunk of the window's top edge lands on some screen — so a
        position saved on a now-disconnected monitor doesn't open off-screen
        and ungrabbable."""
        return QGuiApplication.screenAt(QPoint(int(x + w / 2), int(y + 10))) is not None

    def _show_mini(self) -> None:
        self.mini.lock_size()
        pos = app_settings.get_mini_pos()
        if pos is None or not self._onscreen(pos[0], pos[1], self.mini.width()):
            pos = self._default_corner(self.mini.width(), self.mini.height(), top=False)
        self.mini.move(pos[0], pos[1])
        s = self._last_sample
        if s and s.ok:
            self._sync_mini(s)
        self.mini.show()
        self.mini.raise_()
        self.mini.activateWindow()

    def _show_compact(self) -> None:
        self._sync_compact_view()
        pos = app_settings.get_compact_pos()
        if pos is None or not self._onscreen(pos[0], pos[1], self.compact_view.width()):
            pos = self._default_corner(
                self.compact_view.width(), self.compact_view.height(), top=True)
        self.compact_view.move(pos[0], pos[1])
        self.compact_view.show()
        self.compact_view.raise_()
        self.compact_view.activateWindow()

    def _sync_compact_view(self) -> None:
        """Push the current sessions + usage into the compact view."""
        cv = getattr(self, "compact_view", None)
        if cv is None:
            return
        cv.set_show_tokens(app_settings.get_show_token_usage())
        cv.set_sessions(_view_states(
            self._last_raw_states,
            app_settings.get_show_multiple_sessions(),
            app_settings.get_show_subagents(),
        ))
        s = self._last_sample
        if s and s.ok:
            e = int((time.time() - s.timestamp) // 60)
            cv.update_usage(s,
                            max(0, s.session_reset_minutes - e),
                            max(0, s.weekly_reset_minutes - e),
                            app_settings.get_show_token_usage())

    def _update_compact_usage(self, s, sr: int, wr: int) -> None:
        cv = getattr(self, "compact_view", None)
        if cv is not None and cv.isVisible():
            cv.update_usage(s, sr, wr, app_settings.get_show_token_usage())

    def _show_window(self) -> None:
        """Bring the app to the front for a notification / toast click / second
        launch. Restores whatever mode the user last chose (NOT forced to full)
        so 'pop to front' never silently overwrites their compact/mini choice."""
        self._restore_view()

    def closeEvent(self, event) -> None:
        # Minimize to tray unless the user opted into quit-on-close (or the tray
        # isn't available, in which case closing must actually exit).
        if app_settings.get_quit_on_close() or not self._tray.isVisible():
            event.accept()
            self._real_quit()
        else:
            self.hide()
            event.ignore()

    def _real_quit(self) -> None:
        if hasattr(self, "_poller"):
            self._poller.stop()
            self._poller.wait(2000)
        if hasattr(self, "_update_checker"):
            self._update_checker.stop()
            self._update_checker.wait(2000)
        self._transcript.stop()
        self.sprite.stop()
        self.shelf.stop_all()
        if self.mini.isVisible():
            app_settings.set_mini_pos(self.mini.x(), self.mini.y())
        self.mini.sprite.stop()
        self.mini.close()
        if self.compact_view.isVisible():
            app_settings.set_compact_pos(
                self.compact_view.x(), self.compact_view.y())
        self.compact_view.stop_all()
        self.compact_view.close()
        self._toast.sprite.stop()
        self._toast.close()
        self._tray.hide()
        QGuiApplication.quit()
