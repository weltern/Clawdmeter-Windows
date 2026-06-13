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
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
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
from reset_notify import ResetDecision, ResetNotifier
from session_shelf import SessionShelf
from sprite_player import SpritePlayer, assets_root
from transcript import (
    ACTIVITY_ANIMS,
    ACTIVITY_LABELS,
    Activity as TranscriptActivity,
    TranscriptState,
    TranscriptWatcher,
)


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
    font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets";
}
QToolButton#titleBtn, QToolButton#closeBtn { font-size: 11px; }
QToolButton#settingsBtn { font-size: 16px; }
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

QProgressBar {
    background-color: #1f2937; border: 1px solid #374151; border-radius: 0px;
    height: 14px; text-align: center; color: transparent;
}
QProgressBar::chunk { background-color: #CE7D6B; border-radius: 0px; }
QProgressBar[heat="warm"]::chunk { background-color: #B85C42; }
QProgressBar[heat="hot"]::chunk  { background-color: #8B2E1A; }

QWidget#settingsPanel {
    background-color: #0a0d12;
    border-left: 1px solid #1f2937;
}
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
QWidget#scrim { background-color: rgba(0, 0, 0, 60); }

QWidget#compactRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#compactPct { font-size: 17px; font-weight: 700; color: #e6edf3; }
QLabel#compactPctSub { font-size: 13px; font-weight: 700; color: #9ca3af; }
QLabel#compactReset { font-size: 12px; color: #9ca3af; }

QWidget#toastRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#toastTitle {
    font-size: 14px; font-weight: 700; color: #e6edf3; letter-spacing: 0.5px;
}
QLabel#toastBody { font-size: 12px; color: #9ca3af; }
"""


def _format_minutes(mins: int) -> str:
    if mins <= 0:
        return "-"
    if mins < 60:
        return f"{mins}m"
    hours, m = divmod(mins, 60)
    if hours < 24:
        return f"{hours}h {m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h:02d}h"


def _heat(pct: int) -> str:
    if pct >= 80:
        return "hot"
    if pct >= 50:
        return "warm"
    return "cool"


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


def _push_configured() -> bool:
    """True if the selected push provider has the credentials it needs."""
    if app_settings.get_reset_notify_push_provider() == "telegram":
        return bool(
            app_settings.get_reset_notify_push_tg_token()
            and app_settings.get_reset_notify_push_tg_chat()
        )
    return bool(app_settings.get_reset_notify_push_topic())


def _dispatch_push(title: str, body: str) -> tuple[bool, str]:
    """Send a push via the configured provider. Shared by the reset
    notification and the Settings test button so both exercise one code path."""
    if app_settings.get_reset_notify_push_provider() == "telegram":
        return remote_notify.send_telegram(
            app_settings.get_reset_notify_push_tg_token(),
            app_settings.get_reset_notify_push_tg_chat(),
            title,
            body,
        )
    return remote_notify.send_ntfy(
        app_settings.get_reset_notify_push_topic(), title, body
    )


class CompactWidget(QWidget):
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
        self.setObjectName("compactRoot")
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
        stack.setSpacing(2)
        self.session_pct, self.session_reset = self._row(stack, "compactPct")
        self.weekly_pct, self.weekly_reset = self._row(stack, "compactPctSub")
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
        reset = QLabel("", objectName="compactReset")
        reset.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        line.addWidget(pct)
        line.addWidget(reset)
        line.addStretch(1)
        parent_layout.addLayout(line)
        return pct, reset

    def update_usage(self, session_pct: int, weekly_pct: int,
                     session_reset_minutes: int, weekly_reset_minutes: int) -> None:
        self.session_pct.setText(f"{session_pct}%")
        self.weekly_pct.setText(f"{weekly_pct}%")
        self.set_resets(session_reset_minutes, weekly_reset_minutes)

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
        # Opaque, like the main/compact windows. NOT WA_TranslucentBackground:
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


class Scrim(QWidget):
    """Click-to-dismiss overlay shown behind the settings panel."""

    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("scrim")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.hide()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
        else:
            super().mousePressEvent(e)


class TitleBar(QWidget):
    """Custom frameless title bar: icon, drag area, settings + window buttons."""

    HEIGHT = 48
    ICON_SIZE = 36

    def __init__(self, window: QMainWindow, on_settings, on_compact) -> None:
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

        icon_label = QLabel()
        icon_path = assets_root() / "icon.png"
        if icon_path.exists():
            pm = QPixmap(str(icon_path)).scaled(
                self.ICON_SIZE, self.ICON_SIZE,
                Qt.KeepAspectRatio, Qt.FastTransformation,
            )
            icon_label.setPixmap(pm)
        icon_label.setFixedSize(self.ICON_SIZE + 2, self.ICON_SIZE + 2)
        icon_label.setAlignment(Qt.AlignCenter)
        row.addWidget(icon_label)

        name = QLabel("CLAWDMETER", objectName="titleAppName")
        row.addWidget(name)
        row.addStretch(1)

        # Glyphs from Segoe Fluent Icons / Segoe MDL2 Assets — the same
        # codepoints Windows itself uses for window controls.
        self.settings_btn = self._tool_btn("", "Settings")   # gear
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.clicked.connect(on_settings)
        row.addWidget(self.settings_btn)

        self.compact_btn = self._tool_btn("", "Compact mode")  # BackToWindow
        self.compact_btn.clicked.connect(on_compact)
        row.addWidget(self.compact_btn)

        self.min_btn = self._tool_btn("", "Minimize")        # ChromeMinimize
        self.min_btn.clicked.connect(self._win.showMinimized)
        row.addWidget(self.min_btn)

        self.max_btn = self._tool_btn("", "Maximize")        # ChromeMaximize
        self.max_btn.clicked.connect(self._toggle_max)
        row.addWidget(self.max_btn)

        self.close_btn = self._tool_btn("", "Close")         # ChromeClose
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

    def _toggle_max(self) -> None:
        if self._win.isMaximized():
            self._win.showNormal()
            self.max_btn.setText("")  # ChromeMaximize
        else:
            self._win.showMaximized()
            self.max_btn.setText("")  # ChromeRestore

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
        # Under the threshold we do nothing so double-click-to-maximize still
        # registers.
        if not (e.buttons() & Qt.LeftButton) or self._press_pos is None:
            return
        moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._press_pos = None
        if self._win.isMaximized():
            # Restore before the handoff so the window follows the cursor at
            # its normal size, like Windows' own maximized title-bar drag.
            self._win.showNormal()
            self.max_btn.setText("")  # ChromeMaximize
        winutil.start_native_move(int(self._win.winId()))
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._press_pos = None

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._toggle_max()


class SettingsPanel(QWidget):
    """Right-side slide-in panel. Lives as a child of `parent` (the content
    area). Call open()/close() to animate. Position is set externally on
    parent resize."""

    WIDTH = 280
    ANIM_MS = 220

    # Emitted from the push-test worker thread; delivered on the UI thread.
    _push_test_result = Signal(bool, str)

    def __init__(self, parent: QWidget, on_always_on_top_changed, on_auto_hide_changed, on_close_requested,
                 on_refresh_token=None, on_auto_refresh_changed=None,
                 on_poll_interval_changed=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._open = False
        self._on_aot_changed = on_always_on_top_changed
        self._on_auto_hide_changed = on_auto_hide_changed
        self._on_close_requested = on_close_requested
        self._on_refresh_token = on_refresh_token
        self._on_auto_refresh_changed = on_auto_refresh_changed
        self._on_poll_interval_changed = on_poll_interval_changed
        self.hide()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Fixed header (stays put while the body scrolls).
        header_w = QWidget()
        header = QHBoxLayout(header_w)
        header.setContentsMargins(20, 18, 20, 8)
        title = QLabel("SETTINGS", objectName="settingsTitle")
        close = QToolButton()
        close.setObjectName("titleBtn")
        close.setText("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setFocusPolicy(Qt.NoFocus)
        close.clicked.connect(self._on_close_requested)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close)
        outer.addWidget(header_w)

        # Scrollable body — settings grow without clipping or cramping.
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll, 1)

        body = QWidget()
        body.setObjectName("settingsBody")
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 6, 20, 18)
        layout.setSpacing(12)

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

        layout.addSpacing(10)
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

        layout.addSpacing(10)
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

        self.notify_sound_check = QCheckBox("    Play a sound")
        self.notify_sound_check.setChecked(app_settings.get_reset_notify_sound())
        self.notify_sound_check.toggled.connect(self._on_notify_sound_toggled)
        layout.addWidget(self.notify_sound_check)

        self.notify_popup_check = QCheckBox("    Pop the window to front")
        self.notify_popup_check.setChecked(app_settings.get_reset_notify_popup())
        self.notify_popup_check.toggled.connect(self._on_notify_popup_toggled)
        layout.addWidget(self.notify_popup_check)

        self.notify_push_check = QCheckBox("    Send a push to my phone")
        self.notify_push_check.setChecked(app_settings.get_reset_notify_push())
        self.notify_push_check.toggled.connect(self._on_notify_push_toggled)
        layout.addWidget(self.notify_push_check)

        push_provider_row = QHBoxLayout()
        push_provider_row.addWidget(QLabel("    via"))
        self.notify_push_provider = QComboBox()
        self.notify_push_provider.addItem("ntfy", "ntfy")
        self.notify_push_provider.addItem("Telegram", "telegram")
        idx = self.notify_push_provider.findData(app_settings.get_reset_notify_push_provider())
        self.notify_push_provider.setCurrentIndex(max(0, idx))
        self.notify_push_provider.currentIndexChanged.connect(
            self._on_notify_push_provider_changed
        )
        push_provider_row.addWidget(self.notify_push_provider, 1)
        layout.addLayout(push_provider_row)

        # ntfy fields
        self.notify_push_topic = QLineEdit()
        self.notify_push_topic.setPlaceholderText(
            "ntfy topic (e.g. clawd-nick-7f3a) or full URL"
        )
        self.notify_push_topic.setText(app_settings.get_reset_notify_push_topic())
        self.notify_push_topic.editingFinished.connect(self._on_notify_push_topic_changed)
        layout.addWidget(self.notify_push_topic)
        self.notify_push_ntfy_hint = QLabel(
            "Subscribe to the same topic in the ntfy app (Android/iOS). Pick a "
            "long, hard-to-guess topic — anyone who knows it can read your alerts.",
            objectName="sectionHint",
        )
        self.notify_push_ntfy_hint.setWordWrap(True)
        layout.addWidget(self.notify_push_ntfy_hint)

        # Telegram fields
        self.notify_push_tg_token = QLineEdit()
        self.notify_push_tg_token.setPlaceholderText("Telegram bot token (from @BotFather)")
        self.notify_push_tg_token.setEchoMode(QLineEdit.Password)
        self.notify_push_tg_token.setText(app_settings.get_reset_notify_push_tg_token())
        self.notify_push_tg_token.editingFinished.connect(
            self._on_notify_push_tg_token_changed
        )
        layout.addWidget(self.notify_push_tg_token)
        self.notify_push_tg_chat = QLineEdit()
        self.notify_push_tg_chat.setPlaceholderText("Telegram chat ID (e.g. 123456789)")
        self.notify_push_tg_chat.setText(app_settings.get_reset_notify_push_tg_chat())
        self.notify_push_tg_chat.editingFinished.connect(
            self._on_notify_push_tg_chat_changed
        )
        layout.addWidget(self.notify_push_tg_chat)
        self.notify_push_tg_hint = QLabel(
            "Message @BotFather to create a bot and copy its token, then DM your "
            "bot and read your chat ID from "
            "api.telegram.org/bot<token>/getUpdates. Keep the token private.",
            objectName="sectionHint",
        )
        self.notify_push_tg_hint.setWordWrap(True)
        layout.addWidget(self.notify_push_tg_hint)

        self.notify_push_test_btn = QPushButton("Send test notification")
        self.notify_push_test_btn.clicked.connect(self._on_test_push_clicked)
        layout.addWidget(self.notify_push_test_btn)
        self.notify_push_test_status = QLabel("", objectName="sectionHint")
        self.notify_push_test_status.setWordWrap(True)
        layout.addWidget(self.notify_push_test_status)
        self._push_test_result.connect(self._on_test_push_result)

        self._sync_notify_subtoggles()

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

        layout.addSpacing(10)
        layout.addWidget(QLabel("ABOUT", objectName="sectionLabel"))
        about = QLabel(
            f"Clawdmeter-Windows  v{app_settings.APP_VERSION}\n"
            "by Nick Welter (@weltern) & Claude\n"
            "github.com/weltern/Clawdmeter-Windows\n\n"
            "MIT licensed · the Clawd mascot is © Anthropic PBC and is "
            "not covered by the MIT license · unofficial, not affiliated "
            "with Anthropic.",
            objectName="sectionHint",
        )
        about.setWordWrap(True)
        about.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(about)

        layout.addStretch(1)

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

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

    def _on_notify_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify(checked)
        self._sync_notify_subtoggles()

    def _on_notify_sound_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_sound(checked)

    def _on_notify_popup_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_popup(checked)

    def _on_notify_push_toggled(self, checked: bool) -> None:
        app_settings.set_reset_notify_push(checked)
        self._sync_notify_subtoggles()

    def _on_notify_push_provider_changed(self) -> None:
        app_settings.set_reset_notify_push_provider(self.notify_push_provider.currentData())
        self._sync_notify_subtoggles()

    def _on_notify_push_topic_changed(self) -> None:
        app_settings.set_reset_notify_push_topic(self.notify_push_topic.text())

    def _on_notify_push_tg_token_changed(self) -> None:
        app_settings.set_reset_notify_push_tg_token(self.notify_push_tg_token.text())

    def _on_notify_push_tg_chat_changed(self) -> None:
        app_settings.set_reset_notify_push_tg_chat(self.notify_push_tg_chat.text())

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
        self._sync_notify_subtoggles()  # re-enables the button (clears status)
        self.notify_push_test_status.setText(
            "Sent — check your phone." if ok else f"Failed: {msg}"
        )

    def _sync_notify_subtoggles(self) -> None:
        """Grey out the per-method sub-toggles when the master switch is off, and
        show only the selected push provider's credential fields."""
        on = self.notify_check.isChecked()
        self.notify_sound_check.setEnabled(on)
        self.notify_popup_check.setEnabled(on)
        self.notify_push_check.setEnabled(on)

        push_on = on and self.notify_push_check.isChecked()
        self.notify_push_provider.setEnabled(push_on)
        is_ntfy = self.notify_push_provider.currentData() == "ntfy"

        # Only the chosen provider's fields are shown; both enable with push.
        self.notify_push_topic.setVisible(is_ntfy)
        self.notify_push_ntfy_hint.setVisible(is_ntfy)
        self.notify_push_tg_token.setVisible(not is_ntfy)
        self.notify_push_tg_chat.setVisible(not is_ntfy)
        self.notify_push_tg_hint.setVisible(not is_ntfy)
        self.notify_push_topic.setEnabled(push_on)
        self.notify_push_tg_token.setEnabled(push_on)
        self.notify_push_tg_chat.setEnabled(push_on)
        self.notify_push_test_btn.setEnabled(push_on)
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

    def is_open(self) -> bool:
        return self._open

    def place_closed(self) -> None:
        """Snap to off-screen-right at current parent size (no animation)."""
        p = self.parentWidget()
        if not p:
            return
        self.setGeometry(p.width(), 0, self.WIDTH, p.height())
        self._open = False

    def reposition(self) -> None:
        """Called on parent resize. Keeps panel anchored correctly."""
        p = self.parentWidget()
        if not p:
            return
        if self._open:
            self.setGeometry(p.width() - self.WIDTH, 0, self.WIDTH, p.height())
        else:
            self.setGeometry(p.width(), 0, self.WIDTH, p.height())

    def open_panel(self) -> None:
        if self._open:
            return
        p = self.parentWidget()
        if not p:
            return
        self.refresh_token_status()
        self.show()
        self.raise_()
        start = QRect(p.width(), 0, self.WIDTH, p.height())
        end = QRect(p.width() - self.WIDTH, 0, self.WIDTH, p.height())
        self.setGeometry(start)
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()
        self._open = True

    def close_panel(self) -> None:
        if not self._open:
            return
        p = self.parentWidget()
        if not p:
            return
        start = self.geometry()
        end = QRect(p.width(), 0, self.WIDTH, p.height())
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()
        self._open = False

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


class Dashboard(QMainWindow):
    def __init__(self, mock: bool = False) -> None:
        super().__init__()
        self.setWindowTitle("Clawdmeter")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self._min_h_no_badge = 550
        self._min_h_with_badge = 580
        # Wider than the old hero-only 440 so ~3 shelf tiles (130px sprites plus
        # their margins/spacing) fit without scrolling; overflow scrolls
        # horizontally inside the shelf's QScrollArea, so the window never balloons.
        self.setMinimumSize(520, self._min_h_no_badge)
        self.resize(520, self._min_h_no_badge)
        self.setStyleSheet(STYLESHEET)

        icon_path = assets_root() / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        self._root = root
        self._outer = QVBoxLayout(root)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self.title_bar = TitleBar(self, on_settings=self._toggle_settings,
                                  on_compact=self._enter_compact)
        self._outer.addWidget(self.title_bar)

        content = QWidget()
        self._outer.addWidget(content, 1)
        layout = QVBoxLayout(content)
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
        self.session_row, self.session_pct, self.session_bar, self.session_reset = self._build_row("SESSION (5h)")
        group_session.addLayout(self.session_row)
        layout.addLayout(group_session)
        self.weekly_row, self.weekly_pct, self.weekly_bar, self.weekly_reset = self._build_row("WEEKLY (7d)")
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

        # Settings panel is parented to the central widget so it overlays
        # everything below the title bar and resizes with the window. The
        # scrim sits between content and panel: clicks on it close the panel.
        self._content = content
        self.scrim = Scrim(content)
        self.scrim.clicked.connect(self._close_settings)
        self.settings_panel = SettingsPanel(
            content,
            on_always_on_top_changed=self._set_always_on_top,
            on_auto_hide_changed=self._apply_auto_hide,
            on_close_requested=self._close_settings,
            on_refresh_token=self._request_token_refresh,
            on_auto_refresh_changed=self._set_auto_refresh,
            on_poll_interval_changed=self._set_poll_interval,
        )
        self.settings_panel.place_closed()
        content.installEventFilter(self)

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

        self._rate = RateGroupTracker()
        self._reset_notifier = ResetNotifier()
        self._last_sample: UsageSample | None = None
        self._last_tooltip = ""
        self._transcript_state: TranscriptState | None = None
        # True while the shelf is showing >=1 live session. Tracked explicitly
        # (not via shelf.isVisible(), which is False until the window is shown)
        # so the usage-poll handler knows to leave the mascots to the shelf.
        self._shelf_active = False

        self._transcript = TranscriptWatcher(self)
        # sessions_changed drives the whole multi-mascot path (shelf + the
        # focused-session compact mascot + empty-state mood). state_changed is
        # the back-compat single-session signal; sessions_changed[0] carries the
        # same focused state, so we listen to the richer one only.
        self._transcript.sessions_changed.connect(self._on_sessions)
        # NOTE: started at the END of __init__, not here. start() does a
        # synchronous first poll that emits sessions_changed, and _on_sessions
        # touches widgets (self.compact, self.sprite, self.shelf) that aren't
        # built until later in __init__ — starting here AttributeErrors on
        # real-mode launch.

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon(str(icon_path)) if icon_path.exists() else QIcon(_tray_pixmap(0)))
        tray_menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self._show_window)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._real_quit)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
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

        # Compact mode: a tiny always-on-top floating widget mirroring usage.
        self.compact = CompactWidget()
        self.compact.expand_requested.connect(self._exit_compact)
        self.compact.quit_requested.connect(self._real_quit)

        # Custom limit-reset toast (replaces the native OS notification);
        # clicking it brings the dashboard forward.
        self._toast = ResetToast()
        self._toast.clicked.connect(self._show_window)

        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick_countdown)
        self._countdown.start()

        if mock:
            self._start_mock()
        else:
            self._start_poller()
            # Now that self.compact / self.sprite / self.shelf exist, the
            # watcher's initial synchronous poll can safely drive the shelf.
            self._transcript.start()

    def eventFilter(self, obj, ev):
        if obj is self._content and ev.type() == ev.Type.Resize:
            self.settings_panel.reposition()
            if self.scrim.isVisible():
                self.scrim.setGeometry(0, 0, self._content.width(), self._content.height())
        return super().eventFilter(obj, ev)

    def _toggle_settings(self) -> None:
        if self.settings_panel.is_open():
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self) -> None:
        self.scrim.setGeometry(0, 0, self._content.width(), self._content.height())
        self.scrim.show()
        self.scrim.raise_()
        self.settings_panel.open_panel()
        self.settings_panel.raise_()

    def _close_settings(self) -> None:
        self.settings_panel.close_panel()
        self.scrim.hide()

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
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        reset = QLabel("resets in -", objectName="reset")
        outer.addLayout(header)
        outer.addWidget(bar)
        outer.addWidget(reset)
        return outer, pct, bar, reset

    def _start_poller(self) -> None:
        self._poller = UsagePoller(interval_seconds=app_settings.get_poll_interval())
        self._poller.sample.connect(self._on_sample)
        self._poller.refresh_status.connect(self._on_refresh_status)
        self._poller.start()

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
            self._mock_pct = (self._mock_pct + 1) % 100
            self._on_sample(UsageSample(
                session_pct=self._mock_pct,
                session_reset_minutes=140,
                weekly_pct=18,
                weekly_reset_minutes=4 * 24 * 60 + 6 * 60,
                status="ok (mock)",
                ok=True,
                error=None,
                timestamp=time.time(),
            ))
        self._mock_sample_timer.timeout.connect(sample_tick)
        self._mock_sample_timer.start(800)
        sample_tick()

        # Synthesize the three-session shelf from variant_c_shelf.png — two live
        # mascots cycling activities plus one stale/idle tile — and feed them
        # through the real _on_sessions path so the shelf is fully exercised
        # without launching concurrent Claude Code windows.
        self._mock_phase = 0
        self._mock_shelf_timer = QTimer(self)
        self._mock_shelf_timer.timeout.connect(self._emit_mock_sessions)
        self._mock_shelf_timer.start(2000)
        self._emit_mock_sessions()

    # Activities the two live mock mascots rotate through, so the shelf shows
    # off the per-activity glow colors and animations over time.
    _MOCK_CODING_CYCLE = [
        TranscriptActivity.CODING,
        TranscriptActivity.READING,
        TranscriptActivity.INTEGRATING,
    ]
    _MOCK_THINKING_CYCLE = [
        TranscriptActivity.THINKING,
        TranscriptActivity.PLANNING,
        TranscriptActivity.SEARCHING,
    ]

    def _emit_mock_sessions(self) -> None:
        i = self._mock_phase
        self._mock_phase += 1
        now = time.time()
        coding = self._MOCK_CODING_CYCLE[i % len(self._MOCK_CODING_CYCLE)]
        thinking = self._MOCK_THINKING_CYCLE[i % len(self._MOCK_THINKING_CYCLE)]
        states = [
            TranscriptState(
                activity=coding,
                tool_name="Edit" if coding == TranscriptActivity.CODING else None,
                transcript_path=None,
                last_event_ts=now,
                session_id="mock-clawdmeter",
                cwd=r"C:\Claude\clawdmeter-windows",
                project_name="clawdmeter-windows",
                is_stale=False,
            ),
            TranscriptState(
                activity=thinking,
                tool_name=None,
                transcript_path=None,
                last_event_ts=now,
                session_id="mock-api-gateway",
                cwd=r"C:\work\api-gateway",
                project_name="api-gateway",
                is_stale=False,
            ),
            # Stale tile — quiet ~4 minutes, so the shelf renders it dim/IDLE
            # with a "last active 4m ago" sub-label.
            TranscriptState(
                activity=TranscriptActivity.IDLE,
                tool_name=None,
                transcript_path=None,
                last_event_ts=now - 4 * 60,
                session_id="mock-notes-cli",
                cwd=r"C:\work\notes-cli",
                project_name="notes-cli",
                is_stale=True,
            ),
        ]
        self._on_sessions(states)

    def _on_sample(self, s: UsageSample) -> None:
        # Feed every sample (incl. errors) so the notifier can ignore them
        # without disturbing its baseline.
        decision = self._reset_notifier.observe(s)
        self._last_sample = s
        if not s.ok:
            self._apply_status_badge(s.status)
            self._tray.setToolTip(f"Clawdmeter - {s.status}")
            self._last_tooltip = ""  # force a fresh stats tooltip on recovery
            return

        self.session_pct.setText(f"{s.session_pct}%")
        self.session_bar.setValue(s.session_pct)
        self.session_bar.setProperty("heat", _heat(s.session_pct))
        self.session_bar.style().unpolish(self.session_bar)
        self.session_bar.style().polish(self.session_bar)
        self.session_reset.setText(f"resets in {_format_minutes(s.session_reset_minutes)}")

        self.weekly_pct.setText(f"{s.weekly_pct}%")
        self.weekly_bar.setValue(s.weekly_pct)
        self.weekly_bar.setProperty("heat", _heat(s.weekly_pct))
        self.weekly_bar.style().unpolish(self.weekly_bar)
        self.weekly_bar.style().polish(self.weekly_bar)
        self.weekly_reset.setText(f"resets in {_format_minutes(s.weekly_reset_minutes)}")

        self._sync_compact(s)

        self._rate.observe(s.session_pct)
        # While the shelf is up it owns the mascots (per-session tiles + the
        # compact widget via _drive_compact_from), so the rate-based mood must
        # NOT also drive them here — two paths with different set_anims keys for
        # the same activity would restart and flicker the compact animation on
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

        # Themed toast + tray flash always accompany the master toggle.
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
        if text != self._last_tooltip:
            self._last_tooltip = text
            self._tray.setToolTip(text)

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

        # Window grows when the badge appears; doesn't shrink the user's
        # manual resize if they've already enlarged it. Auto-hide mode removes
        # TitleBar.HEIGHT from both floors since the title bar is overlayed.
        base = self._min_h_with_badge if has_badge else self._min_h_no_badge
        offset = TitleBar.HEIGHT if self._auto_hide_enabled else 0
        new_min = base - offset
        if self.minimumHeight() != new_min:
            prev_min = self.minimumHeight()
            self.setMinimumHeight(new_min)
            if not has_badge and self.height() == prev_min and prev_min > new_min:
                # Window was sitting at the previous (larger) auto-grown floor;
                # shrink back to the new floor when the badge clears.
                self.resize(self.width(), new_min)

    def _tick_countdown(self) -> None:
        s = self._last_sample
        if not s or not s.ok:
            return
        elapsed_min = int((time.time() - s.timestamp) // 60)
        sr = max(0, s.session_reset_minutes - elapsed_min)
        wr = max(0, s.weekly_reset_minutes - elapsed_min)
        self.session_reset.setText(f"resets in {_format_minutes(sr)}")
        self.weekly_reset.setText(f"resets in {_format_minutes(wr)}")
        self.compact.set_resets(sr, wr)
        self._set_tray_tooltip(s.session_pct, sr, s.weekly_pct, wr)

    def _on_sessions(self, states: list[TranscriptState]) -> None:
        """Route the watcher's per-session states to the shelf (or the empty
        state). With >=1 live session the shelf takes over the mascot slot and
        the compact widget mirrors the focused (newest) session; with 0 the hero
        returns and the rate-based mood drives hero + compact + group_label."""
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
            self.shelf.set_sessions(states)
            # The compact widget stays single-mascot for now (full multi-session
            # compact is Variant D), so it follows the focused session.
            self._transcript_state = states[0]
            self._drive_compact_from(states[0])
        else:
            self._shelf_active = False
            self.shelf.hide()
            self.shelf.set_sessions([])  # drop any leftover tiles + reset header
            self.hero.show()
            self.group_label.show()
            # No session: fall back to today's rate-driven mood for hero/compact.
            self._transcript_state = None
            self._update_sprite_selection()
            # set_anims no-ops on an unchanged key, so re-show the paused hero.
            self.sprite.resume()

    def _drive_compact_from(self, state: TranscriptState) -> None:
        """Mirror one session's activity on the compact mascot only — the hero
        is hidden while the shelf is up, so it isn't driven here. Idle/stale
        sessions fall back to the calm group-0 loop."""
        idle = state.is_stale or state.activity == TranscriptActivity.IDLE
        anims = None if idle else ACTIVITY_ANIMS.get(state.activity)
        if anims:
            self.compact.sprite.set_anims(f"compact:{state.activity.value}", anims)
        else:
            self.compact.sprite.set_anims("compact:idle", GROUP_ANIMS[0])

    def _set_sprite_anims(self, key: str, names) -> None:
        """Drive both the full-window and compact mascots in lockstep."""
        self.sprite.set_anims(key, names)
        self.compact.sprite.set_anims(key, names)

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
            self._show_window()

    def _sync_compact(self, s: UsageSample) -> None:
        """Push a usage sample into the compact widget. Reset times use the same
        relative 'resets in 4h 56m' form as the main window."""
        self.compact.update_usage(
            s.session_pct, s.weekly_pct,
            s.session_reset_minutes, s.weekly_reset_minutes,
        )

    def _enter_compact(self) -> None:
        """Hide the full window and show the tiny floating widget."""
        if self.settings_panel.is_open():
            self._close_settings()

        pos = app_settings.get_compact_pos()
        self.compact.lock_size()
        if pos is None:
            scr = self.screen() or QGuiApplication.primaryScreen()
            geo = scr.availableGeometry()
            x = geo.right() - self.compact.width() - 24
            y = geo.bottom() - self.compact.height() - 24
            self.compact.move(x, y)
        else:
            self.compact.move(pos[0], pos[1])

        s = self._last_sample
        if s and s.ok:
            self._sync_compact(s)

        self.hide()
        self.compact.show()
        self.compact.raise_()
        self.compact.activateWindow()

    def _exit_compact(self) -> None:
        if self.compact.isVisible():
            app_settings.set_compact_pos(self.compact.x(), self.compact.y())
            self.compact.hide()
        self._show_window()

    def _show_window(self) -> None:
        if self.compact.isVisible():
            app_settings.set_compact_pos(self.compact.x(), self.compact.y())
            self.compact.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
        self._transcript.stop()
        self.sprite.stop()
        self.shelf.stop_all()
        if self.compact.isVisible():
            app_settings.set_compact_pos(self.compact.x(), self.compact.y())
        self.compact.sprite.stop()
        self.compact.close()
        self._toast.sprite.stop()
        self._toast.close()
        self._tray.hide()
        QGuiApplication.quit()
