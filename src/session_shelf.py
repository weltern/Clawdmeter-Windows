"""Mascot shelf — a row of per-session tiles (Variant C).

Each live Claude Code session gets a SessionTile: a mascot with a colored
activity glow, a project name, an activity label and a live/idle status dot.
SessionShelf lays them out in a horizontal scroll area so the window width
stays stable as sessions come and go, and DIFFS by session id on each update
so animations keep running instead of restarting every poll.

The quota bars stay account-wide and live in dashboard.py — this module only
owns the multiplied mascot/activity layer.
"""

from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter

import winutil
from mood import GROUP_ANIMS
from sprite_player import SpritePlayer, assets_root
from uiutil import format_minutes, heat
from transcript import (
    ACTIVITY_ANIMS,
    ACTIVITY_COLORS,
    ACTIVITY_LABELS,
    Activity,
    AgentState,
    TranscriptState,
    fmt_tokens,
)


# Calm animation used when a tile is idle/stale — the firmware's group-0 roster
# leads with the sleep expression, matching the dashboard's empty-state mood.
_IDLE_ANIMS = GROUP_ANIMS[0]

# Local copy of the dashboard's visual tokens. Kept here (rather than imported
# from dashboard.py) so the shelf has no back-dependency on its host window.
_BG = "#0e1116"
_TEXT = "#e6edf3"
_MUTED = "#9ca3af"
_IDLE_COLOR = ACTIVITY_COLORS[Activity.IDLE]

SHELF_STYLESHEET = f"""
QWidget#shelfRoot {{ background-color: {_BG}; }}
QScrollArea#shelfScroll {{ background: transparent; border: none; }}
QWidget#shelfRow {{ background: transparent; }}
QLabel#shelfHeader {{
    font-size: 13px; font-weight: 600; color: {_MUTED}; letter-spacing: 2px;
}}
QWidget#sessionTile {{ background: transparent; }}
QLabel#tileActivity {{
    font-size: 11px; font-weight: 600; letter-spacing: 1px;
}}
QLabel#tileDot {{ font-size: 11px; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{
    background: #374151; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #4b5563; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


# Top padding inside a tile so the mascot's glow (drop-shadow blur ~38) isn't
# clipped at the tile's top edge. The mascot sits with internal margin now
# (consistent-crop), so the glow only needs a little extra room above.
_GLOW_PAD = 14


def _ago_text(last_event_ts: float | None) -> str:
    """Human 'last active Nm ago' from an event timestamp, for idle tiles."""
    if not last_event_ts:
        return "idle"
    secs = max(0, int(time.time() - last_event_ts))
    if secs < 60:
        return f"last active {secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"last active {mins}m ago"
    hours, m = divmod(mins, 60)
    return f"last active {hours}h {m:02d}m ago"


def _abs_time_text(last_event_ts: float | None) -> str:
    """Absolute local time of the last activity, for the idle tile's tooltip
    (the relative 'Nm ago' is shown inline; hover gives the exact wall time)."""
    if not last_event_ts:
        return ""
    return "Last active " + datetime.fromtimestamp(last_event_ts).strftime(
        "%a %b %d, %I:%M %p"
    )


def _token_tooltip(t) -> str:
    """Per-session token breakdown shown when hovering the mascot. Headline is
    'work' (input+output); the cache buckets are listed separately so the big
    cache-read figure is visible but doesn't distort the headline."""
    return (
        f"This session — {fmt_tokens(t.work)} tokens (input + output)\n"
        f"  input  {fmt_tokens(t.input)}\n"
        f"  output {fmt_tokens(t.output)}\n"
        f"  cache  {fmt_tokens(t.cache_read + t.cache_write)} "
        f"(read {fmt_tokens(t.cache_read)} + write {fmt_tokens(t.cache_write)})"
    )


# Cap for the project/title label. Session titles (from ai-title/custom-title)
# are far longer than a cwd leaf, so the label is capped at this width (or the
# mascot's width if larger): a long title can't stretch a tile far wider than
# its mascot. At rest it's elided; on hover it scrolls to reveal the full text
# (see ScrollingLabel).
_LABEL_MIN_W = 150

# Child-mascot (subagent) sizing.
AGENT_SPRITE = 38
# Column spacing above the agents row (matches the tile's QVBoxLayout spacing).
AGENTS_ROW_SPACING = 4
# Height reserved for the agents row before a real one can be measured; once a
# tile has agents the true measured height is used instead (see _agent_extra).
AGENT_ROW_FALLBACK = 46


class ScrollingLabel(QWidget):
    """A single-line title that elides at rest and, on hover, smoothly scrolls
    (ping-pong) to reveal the full text — but only when it doesn't fit. The full
    text is also exposed as a tooltip.

    Hand-painted on purpose: elidedText() must measure with the SAME font we
    render with (a QSS-applied font isn't seen by QFontMetrics, so it would
    mis-measure and never truncate), and QLabel can't scroll its text.
    """

    _START_PAUSE = 0.18      # fraction of the cycle spent paused at each end
    _SPEED_PX_S = 42         # scroll speed in px/sec (drives the duration)
    _END_GAP_PX = 12         # trailing gap so the last glyph isn't flush to edge

    def __init__(self, parent=None, *, px: int = 13, bold: bool = True,
                 color: str = _TEXT, letter_spacing: float = 0.5,
                 max_w: int = _LABEL_MIN_W, align=Qt.AlignHCenter) -> None:
        super().__init__(parent)
        self._full = ""
        self._offset = 0
        self._hovering = False
        self._align = align           # how non-scrolling text sits (centre vs left)
        self._explicit_tt: str | None = None   # None = auto (overflow-based)
        self._font = QFont()
        self._font.setPixelSize(px)
        self._font.setBold(bold)
        if letter_spacing:
            self._font.setLetterSpacing(QFont.AbsoluteSpacing, letter_spacing)
        self._fm = QFontMetrics(self._font)
        self._color = QColor(color)
        self.setFixedHeight(self._fm.height())
        self.setMaximumWidth(max_w)
        self._anim = QPropertyAnimation(self, b"scrollOffset", self)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)

    # --- public API (QLabel-like) -------------------------------------------
    def setText(self, text: str, tooltip: str | None = None) -> None:
        """Set the label text. `tooltip=None` → auto (full text shown on hover
        only when it overflows); pass an explicit string to override (e.g. the
        idle line shows the absolute last-active time instead)."""
        text = text or ""
        self._explicit_tt = tooltip
        if text == self._full:
            self._refresh_tooltip()
            return
        self._full = text
        self._offset = 0
        self._refresh_tooltip()
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._full

    # --- geometry -----------------------------------------------------------
    def _avail(self) -> int:
        # Realized width once laid out; the cap as a fallback before show (and in
        # headless tests) so overflow/elision decisions stay deterministic.
        return self.width() or self.maximumWidth()

    def _text_w(self) -> int:
        return self._fm.horizontalAdvance(self._full)

    def _overflows(self) -> bool:
        return self._text_w() > self._avail()

    def sizeHint(self) -> QSize:
        return QSize(min(self._text_w(), self.maximumWidth()), self._fm.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._fm.height())

    def _refresh_tooltip(self) -> None:
        if self._explicit_tt is not None:
            self.setToolTip(self._explicit_tt)
        else:
            self.setToolTip(self._full if self._overflows() else "")

    # --- animatable scroll offset -------------------------------------------
    def _get_offset(self) -> int:
        return self._offset

    def _set_offset(self, v: int) -> None:
        self._offset = int(v)
        self.update()

    scrollOffset = Property(int, _get_offset, _set_offset)

    # --- events -------------------------------------------------------------
    def enterEvent(self, e) -> None:
        self._hovering = True
        self._maybe_scroll()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hovering = False
        self._anim.stop()
        self._offset = 0
        self.update()
        super().leaveEvent(e)

    def resizeEvent(self, e) -> None:
        self._refresh_tooltip()
        if self._hovering:
            self._maybe_scroll()
        else:
            self._offset = 0
        super().resizeEvent(e)

    def _maybe_scroll(self) -> None:
        self._anim.stop()
        span = self._text_w() + self._END_GAP_PX - self._avail()
        if span <= 0:
            self._offset = 0
            self.update()
            return
        # Ping-pong: pause, scroll out, pause, scroll back — loop while hovered.
        travel_s = span / self._SPEED_PX_S
        total = max(1.2, 2 * travel_s / (1 - 2 * self._START_PAUSE))
        self._anim.setDuration(int(total * 1000))
        p = self._START_PAUSE
        self._anim.setKeyValueAt(0.0, 0)
        self._anim.setKeyValueAt(p, 0)
        self._anim.setKeyValueAt(0.5 - p / 2, span)
        self._anim.setKeyValueAt(0.5 + p / 2, span)
        self._anim.setKeyValueAt(1.0 - p, 0)
        self._anim.setKeyValueAt(1.0, 0)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def paintEvent(self, e) -> None:
        if not self._full:
            return
        p = QPainter(self)
        p.setFont(self._font)
        p.setPen(self._color)
        rect = self.rect()
        if not self._overflows():
            p.drawText(rect, self._align | Qt.AlignVCenter, self._full)
        elif self._hovering:
            y = self._fm.ascent() + (self.height() - self._fm.height()) // 2
            p.drawText(-self._offset, y, self._full)
        else:
            elided = self._fm.elidedText(self._full, Qt.ElideRight, self._avail())
            p.drawText(rect, self._align | Qt.AlignVCenter, elided)
        p.end()


# Usage-bar palette (was QProgressBar QSS; now painted so the bar can render
# past 100% with a distinct overage colour).
_BAR_TRACK = "#1f2937"
_BAR_BORDER = "#374151"
_BAR_OVERAGE = "#dc2626"   # bright red — the overage overflow
_BAR_HEAT = {"cool": "#CE7D6B", "warm": "#B85C42", "hot": "#8B2E1A"}


class UsageBar(QWidget):
    """A usage bar that can render PAST 100%. The base fills 0–100% in its heat
    colour; any overage continues in red, with the scale extended so the 100%
    mark sits proportionally (20% overage -> the bar is full, its last 1/6 red).
    Shared by the full window and the compact view."""

    def __init__(self, parent=None, *, height: int = 14) -> None:
        super().__init__(parent)
        self._value = 0      # base utilisation 0..100
        self._overage = 0    # overage beyond 100 (0 = none)
        self._heat = "cool"
        self.setFixedHeight(height)

    def set_values(self, value: int, overage: int = 0, heat: str = "cool") -> None:
        value = max(0, int(value))
        overage = max(0, int(overage))
        heat = heat if heat in _BAR_HEAT else "cool"
        if (value, overage, heat) == (self._value, self._overage, self._heat):
            return
        self._value, self._overage, self._heat = value, overage, heat
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QColor(_BAR_BORDER))
        p.setBrush(QColor(_BAR_TRACK))
        p.drawRect(rect)

        inner = rect.adjusted(1, 1, 0, 0)
        w, h, x, y = inner.width(), inner.height(), inner.x(), inner.y()
        scale = 100 + self._overage if self._overage > 0 else 100
        p.setPen(Qt.NoPen)
        base = min(self._value, 100)
        base_w = int(round(w * base / scale))
        if base_w > 0:
            p.setBrush(QColor(_BAR_HEAT[self._heat]))
            p.drawRect(x, y, base_w, h)
        if self._overage > 0:
            start = int(round(w * 100 / scale))
            over_w = w - start
            p.setBrush(QColor(_BAR_OVERAGE))
            p.drawRect(x + start, y, over_w, h)
        p.end()


class AgentMascot(QWidget):
    """A small child mascot for one subagent: a mini sprite with an activity glow
    (no label — the tooltip names its activity/tool)."""

    def __init__(self, agent_id: str, parent=None) -> None:
        super().__init__(parent)
        self._agent_id = agent_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.sprite = SpritePlayer(size=AGENT_SPRITE)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(16)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(_IDLE_COLOR))
        self.sprite.setGraphicsEffect(self._glow)
        lay.addWidget(self.sprite, 0, Qt.AlignHCenter)

    def update_state(self, agent: AgentState) -> None:
        idle = agent.is_stale or agent.activity == Activity.IDLE
        color = _IDLE_COLOR if idle else ACTIVITY_COLORS.get(agent.activity, _IDLE_COLOR)
        self._glow.setColor(QColor(color))
        anims = _IDLE_ANIMS if idle else (ACTIVITY_ANIMS.get(agent.activity) or _IDLE_ANIMS)
        self.sprite.set_anims(f"{self._agent_id}:{'idle' if idle else agent.activity.value}", anims)
        label = ACTIVITY_LABELS.get(agent.activity, "")
        self.setToolTip(f"agent · {label}" + (f" — {agent.tool_name}" if agent.tool_name else ""))

    def stop(self) -> None:
        self.sprite.stop()


class SessionTile(QWidget):
    """One session: glowing mascot + project name + activity + status dot,
    plus a row of small child mascots when the session has live subagents."""

    # A single status dot reused for both states — the glyph stays, only its
    # color changes (warm/active accent when live, dim grey when idle).
    _DOT = "●"

    def __init__(self, session_id: str, sprite_size: int = 120, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._session_id = session_id
        self._show_tokens = True   # mascot-hover token breakdown (gated by Settings)

        col = QVBoxLayout(self)
        # Side margins double as the inter-tile gap (the row spacing is 0) so a
        # leaving tile collapses to truly zero width with no leftover gap. The
        # generous TOP margin gives the mascot's drop-shadow glow room to render —
        # child widgets are clipped to the tile, so without it the glow's upper
        # halo is sliced off at the tile's top edge.
        col.setContentsMargins(12, _GLOW_PAD, 12, 4)
        col.setSpacing(4)
        col.setAlignment(Qt.AlignHCenter)
        # SetNoConstraint + a zero minimum let the enter/leave width animation
        # shrink the tile below the mascot's fixed size (the content is clipped),
        # so neighbours slide over instead of the tile popping in/out. The row
        # layout still allocates the tile its sizeHint width when unconstrained.
        col.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumWidth(0)

        self.sprite = SpritePlayer(size=sprite_size)
        # The drop shadow is the colored "glow" behind the mascot. Offset 0 so
        # it haloes evenly; the color is swapped per activity in update_state.
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(38)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(_IDLE_COLOR))
        self.sprite.setGraphicsEffect(self._glow)
        col.addWidget(self.sprite, 0, Qt.AlignHCenter)

        # Title label: elides at rest, scrolls on hover, full text in a tooltip.
        self.project_label = ScrollingLabel()
        self._label_max_w = max(sprite_size, _LABEL_MIN_W)
        self.project_label.setMaximumWidth(self._label_max_w)
        col.addWidget(self.project_label, 0, Qt.AlignHCenter)

        # Activity + a leading status dot share one row so the dot reads as a
        # badge on the label rather than floating loose.
        act_row = QHBoxLayout()
        act_row.setSpacing(5)
        act_row.setAlignment(Qt.AlignHCenter)
        self.status_dot = QLabel(self._DOT, objectName="tileDot")
        self.activity_label = QLabel("", objectName="tileActivity")
        act_row.addWidget(self.status_dot)
        act_row.addWidget(self.activity_label)
        col.addLayout(act_row)

        # Secondary line — the target being acted on (file/pattern/…, else the
        # tool name) when live, "last active Nm ago" when idle. Scrolls on hover
        # like the title, since file paths/queries can be long.
        self.sub_label = ScrollingLabel(px=10, bold=False, color=_MUTED,
                                        letter_spacing=0, max_w=self._label_max_w)
        col.addWidget(self.sub_label, 0, Qt.AlignHCenter)

        # Row of child mascots for live subagents — hidden until the session has any.
        self._agents: dict[str, AgentMascot] = {}
        self._agents_box = QWidget()
        self._agents_row = QHBoxLayout(self._agents_box)
        self._agents_row.setContentsMargins(0, 4, 0, 0)
        self._agents_row.setSpacing(6)
        self._agents_row.setAlignment(Qt.AlignHCenter)
        self._agents_box.hide()
        col.addWidget(self._agents_box)

    def has_agents(self) -> bool:
        return bool(self._agents)

    def agents_box_height(self) -> int:
        """Height the agents row contributes to the tile's sizeHint (0 if empty)."""
        return self._agents_box.sizeHint().height() if self._agents else 0

    def update_agents(self, agents: list[AgentState]) -> None:
        """Diff child mascots by agent id (add/remove/update in place) and show
        the row only while the session has live subagents."""
        incoming = {a.agent_id: a for a in agents}
        for aid in list(self._agents):
            if aid not in incoming:
                m = self._agents.pop(aid)
                self._agents_row.removeWidget(m)
                m.stop()
                m.deleteLater()
        for aid, agent in incoming.items():
            m = self._agents.get(aid)
            if m is None:
                m = AgentMascot(aid, parent=self._agents_box)
                self._agents[aid] = m
                self._agents_row.addWidget(m, 0, Qt.AlignVCenter)
            m.update_state(agent)
        self._agents_box.setVisible(bool(self._agents))

    def set_sprite_size(self, px: int) -> None:
        # set_size (not setFixedSize) so the mascot pixmap re-scales too; it
        # early-returns when the size is unchanged, so calling it every poll is
        # cheap and won't churn the layout.
        self.sprite.set_size(px)
        # Keep the label cap in step with the mascot width; the label re-elides
        # / re-evaluates its scroll on the resulting resize.
        new_w = max(px, _LABEL_MIN_W)
        if new_w != self._label_max_w:
            self._label_max_w = new_w
            self.project_label.setMaximumWidth(new_w)
            self.sub_label.setMaximumWidth(new_w)

    def update_state(self, state: TranscriptState) -> None:
        """Reflect one session's state: name, activity color/label, glow, dot,
        and the mascot animation (idle sessions get the calm sleep loop)."""
        self.project_label.setText(state.project_name or "unknown")

        # Per-session token breakdown on mascot hover (when enabled and there's
        # something to show).
        tokens = getattr(state, "tokens", None)
        if self._show_tokens and tokens is not None and tokens.total > 0:
            self.sprite.setToolTip(_token_tooltip(tokens))
        else:
            self.sprite.setToolTip("")

        idle = state.is_stale or state.activity == Activity.IDLE
        color = ACTIVITY_COLORS.get(state.activity, _IDLE_COLOR)

        if idle:
            # Dim glow + calm animation. A fresh, distinct set_anims key is
            # needed so a tile transitioning live->idle actually restarts on
            # the idle loop (set_anims no-ops on an unchanged key).
            self._glow.setColor(QColor(_IDLE_COLOR))
            self._glow.setBlurRadius(20)
            self.activity_label.setText(ACTIVITY_LABELS[Activity.IDLE])
            self.activity_label.setStyleSheet(f"color: {_IDLE_COLOR};")
            self.status_dot.setStyleSheet(f"color: {_IDLE_COLOR};")
            self.sub_label.setText(_ago_text(state.last_event_ts),
                                   tooltip=_abs_time_text(state.last_event_ts))
            self.sprite.set_anims(f"{self._session_id}:idle", _IDLE_ANIMS)
        else:
            self._glow.setColor(QColor(color))
            self._glow.setBlurRadius(38)
            self.activity_label.setText(ACTIVITY_LABELS.get(state.activity, ""))
            self.activity_label.setStyleSheet(f"color: {color};")
            # Live dot tracks the activity color so the tile reads as a unit.
            self.status_dot.setStyleSheet(f"color: {color};")
            # Show what's being acted on (file/pattern/…); fall back to the tool.
            self.sub_label.setText(state.target or state.tool_name or "")
            anims = ACTIVITY_ANIMS.get(state.activity) or _IDLE_ANIMS
            self.sprite.set_anims(f"{self._session_id}:{state.activity.value}", anims)

    def stop(self) -> None:
        self.sprite.stop()
        for m in self._agents.values():
            m.stop()

    # --- enter/leave transitions --------------------------------------------
    # A tile expands in (width 0 -> natural) and collapses out (natural -> 0),
    # so the row reflows smoothly as sessions come and go. The width rides the
    # widget's own maximumWidth property (the SetNoConstraint above lets it
    # shrink past the mascot, clipping the centred content). The glow stays on
    # throughout — we deliberately do NOT fade via a QGraphicsOpacityEffect on
    # the tile, because that would nest over the sprite's (and agents') own glow
    # effects, which Qt can't render (a graphics effect inside a graphics
    # effect). The shelf owns the animation objects so they aren't GC'd mid-flight.
    ENTER_MS = 240
    LEAVE_MS = 200
    _SIZE_MAX = 16777215  # Qt's QWIDGETSIZE_MAX — the "no maximum" sentinel.

    def build_enter_anim(self) -> QParallelAnimationGroup:
        target = max(1, self.sizeHint().width())
        self.setMaximumWidth(0)
        grp = QParallelAnimationGroup(self)
        grow = QPropertyAnimation(self, b"maximumWidth", grp)
        grow.setDuration(self.ENTER_MS)
        grow.setStartValue(0)
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.OutCubic)
        grp.addAnimation(grow)
        grp.finished.connect(self._clear_transition)
        return grp

    def build_leave_anim(self) -> QParallelAnimationGroup:
        grp = QParallelAnimationGroup(self)
        shrink = QPropertyAnimation(self, b"maximumWidth", grp)
        shrink.setDuration(self.LEAVE_MS)
        shrink.setStartValue(max(1, self.width()))
        shrink.setEndValue(0)
        shrink.setEasingCurve(QEasingCurve.InCubic)
        grp.addAnimation(shrink)
        return grp

    def _clear_transition(self) -> None:
        """Release the width cap so the tile sizes naturally again after enter."""
        self.setMaximumWidth(self._SIZE_MAX)


class SessionShelf(QWidget):
    """Header + horizontal scroll row of SessionTiles, diffed by session id."""

    # Uniform sprite size by session count — one big mascot looks great solo,
    # but a row of six must stay compact enough to fit the scroll viewport.
    @staticmethod
    def _sprite_size_for(count: int) -> int:
        if count <= 1:
            return 200
        if count == 2:
            return 160
        if count == 3:
            return 130
        return 110

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("shelfRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(SHELF_STYLESHEET)

        self._tiles: dict[str, SessionTile] = {}
        # Whether tiles expose the per-session token breakdown on mascot hover
        # (mirrors the Settings "Show token usage" toggle; applied to new tiles).
        self._show_tokens = True
        # Tiles mid-leave (animating out, still in the layout) keyed by id, and
        # the live animation groups keyed by tile so they aren't GC'd and so we
        # can tell when the shelf is "settled" (safe to reorder).
        self._leaving: dict[str, SessionTile] = {}
        self._anims: dict[SessionTile, object] = {}
        # The uniform sprite size currently applied to live tiles. When the
        # session count changes this target changes and survivors animate to it
        # rather than popping (which, with centre alignment, jolts the whole row).
        self._sprite_size: int | None = None
        # Animates the reserved shelf height when the tile size changes, so the
        # quota bars below glide to their new position instead of jumping.
        self._height_anim: QPropertyAnimation | None = None
        # Per-tile height beyond the mascot (labels + margins), measured once.
        self._tile_oh: int | None = None
        # Measured height an agents row adds to a tile (sprite + padding + the
        # column spacing). Cached the first time a tile actually has agents so
        # the reserve's add-back and _tile_overhead's subtraction always agree.
        self._agent_oh: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)  # tight gap between the header and the mascots

        self.header = QLabel("ACTIVE SESSIONS — 0", objectName="shelfHeader")
        self.header.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self.header)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("shelfScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer.addWidget(self._scroll, 1)

        self._row_widget = QWidget(objectName="shelfRow")
        self._row = QHBoxLayout(self._row_widget)
        self._row.setContentsMargins(4, 4, 4, 4)
        # Spacing is 0 — each tile carries its own side margins as the gap, so a
        # collapsing tile leaves no residual space behind when it animates out.
        self._row.setSpacing(0)
        self._row.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._scroll.setWidget(self._row_widget)

    def set_sessions(self, states) -> None:
        """Add/remove/update tiles by session id without rebuilding the row.

        Rebuilding every poll would thrash the sprite QTimers and flicker, so we
        keep tiles keyed by id, animate out the ones whose session vanished,
        animate in ones that appeared, and update the survivors in place.
        """
        # Dedup by session id (the dict keeps the watcher's newest-first order).
        # Sizing, tile creation and the header all key off this one map so they
        # can't disagree if a duplicate id ever slips through.
        incoming = {s.session_id: s for s in states if s.session_id}
        size = self._sprite_size_for(len(incoming))
        old_size = self._sprite_size
        self._sprite_size = size

        # Animate out tiles whose session left the shelf (they stay in the layout
        # and collapse, so the neighbours slide in to fill the gap).
        for sid in list(self._tiles):
            if sid not in incoming:
                self._start_leave(sid, self._tiles.pop(sid))

        # Create/update tiles in newest-first order.
        for index, (sid, state) in enumerate(incoming.items()):
            tile = self._tiles.get(sid)
            if tile is None:
                tile = self._resurrect(sid)  # a session that reappeared mid-leave
                if tile is None:
                    tile = SessionTile(sid, sprite_size=size, parent=self._row_widget)
                    tile._show_tokens = self._show_tokens
                    self._tiles[sid] = tile
                    self._insert_live(tile, index)
                    tile.set_sprite_size(size)
                    tile.update_state(state)
                    tile.update_agents(state.agents)
                    self._start_enter(tile)
                    continue
            # Survivor: smoothly scale to the new size when the count changed.
            if tile in self._anims:
                pass  # an enter/size animation owns the size — don't fight it
            elif old_size is not None and old_size != size:
                self._animate_tile_size(tile, old_size, size)
            else:
                tile.set_sprite_size(size)
            tile.update_state(state)
            tile.update_agents(state.agents)

        # Re-assert newest-first order. _reorder_live short-circuits when the
        # live order is unchanged (the common case) so there's no per-poll
        # churn, operates only on live tiles, and leaves entering tiles' width
        # animations running as they're repositioned.
        self._reorder_live(incoming)

        self._sync_height(old_size, size)
        self.header.setText(f"ACTIVE SESSIONS — {len(self._tiles)}")

    def set_header_visible(self, on: bool) -> None:
        """Show/hide the 'ACTIVE SESSIONS — N' header (hidden in single-mascot
        mode, where the count is meaningless)."""
        self.header.setVisible(on)

    def set_show_tokens(self, on: bool) -> None:
        """Enable/disable the per-session token breakdown on mascot hover. The
        actual tooltip text is (re)applied by the next update_state."""
        self._show_tokens = bool(on)
        for tile in (*self._tiles.values(), *self._leaving.values()):
            tile._show_tokens = self._show_tokens

    def _start_enter(self, tile: SessionTile) -> None:
        anim = tile.build_enter_anim()
        self._anims[tile] = anim
        anim.finished.connect(lambda t=tile: self._anims.pop(t, None))
        anim.start()

    def _animate_tile_size(self, tile: SessionTile, start: int, end: int) -> None:
        """Scale a survivor's mascot from `start` to `end` over the enter
        duration, so a count change reflows the row smoothly instead of popping."""
        anim = QPropertyAnimation(tile.sprite, b"renderSize", tile)
        anim.setDuration(SessionTile.ENTER_MS)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anims[tile] = anim
        anim.finished.connect(lambda t=tile: self._anims.pop(t, None))
        anim.start()

    def _start_leave(self, sid: str, tile: SessionTile) -> None:
        self._cancel_anim(tile)  # if it was still entering, drop that animation
        self._leaving[sid] = tile
        anim = tile.build_leave_anim()
        self._anims[tile] = anim
        anim.finished.connect(lambda s=sid, t=tile: self._finish_leave(s, t))
        anim.start()

    def _finish_leave(self, sid: str, tile: SessionTile) -> None:
        self._anims.pop(tile, None)
        if self._leaving.get(sid) is tile:
            del self._leaving[sid]
        self._row.removeWidget(tile)
        tile.stop()
        tile.deleteLater()

    def _resurrect(self, sid: str) -> SessionTile | None:
        """A session that reappeared before its leave finished: cancel the leave,
        restore the tile and re-enter it instead of spawning a duplicate."""
        tile = self._leaving.pop(sid, None)
        if tile is None:
            return None
        self._cancel_anim(tile)
        self._tiles[sid] = tile
        self._start_enter(tile)
        return tile

    def _cancel_anim(self, tile: SessionTile) -> None:
        anim = self._anims.pop(tile, None)
        if anim is not None:
            anim.stop()

    def _insert_live(self, tile: SessionTile, logical_index: int) -> None:
        """Insert `tile` so it becomes the logical_index-th live tile, skipping
        any tiles currently animating out when counting positions."""
        leaving = set(self._leaving.values())
        seen = 0
        for i in range(self._row.count()):
            w = self._row.itemAt(i).widget()
            if w is None or w in leaving:
                continue
            if seen == logical_index:
                self._row.insertWidget(i, tile, 0, Qt.AlignTop)
                return
            seen += 1
        self._row.addWidget(tile, 0, Qt.AlignTop)

    def _reorder_live(self, incoming) -> None:
        desired = [self._tiles[sid] for sid in incoming if sid in self._tiles]
        current = [
            w for i in range(self._row.count())
            if (w := self._row.itemAt(i).widget()) in self._tiles.values()
        ]
        if current == desired:
            return
        for w in current:
            self._row.removeWidget(w)
        for i, tile in enumerate(desired):
            self._row.insertWidget(i, tile, 0, Qt.AlignTop)

    def _sync_height(self, old_size: int | None, new_size: int) -> None:
        """Keep the reserved shelf height in step with the tile size so the quota
        bars below don't jump. Snap on the first fill (or when the size is
        unchanged); animate — same curve/duration as the mascot resize — when it
        changes. The height is derived analytically from the target sprite size,
        not from the live layout, so a tile mid enter-animation (width clamped to
        0) can't report a stale, clipped height."""
        target = self._reserved_height_for(new_size)
        start = self._scroll.minimumHeight()
        if old_size is None:
            # First fill — snap, no animation.
            if start != target:
                self._scroll.setMinimumHeight(target)
            return
        # Already animating toward this exact target? Let it finish — restarting
        # every poll (the steady state) would stutter and never settle.
        if self._height_anim is not None and self._height_anim.endValue() == target:
            return
        if self._height_anim is not None:
            self._height_anim.stop()
        if start == target:
            self._height_anim = None
            return
        anim = QPropertyAnimation(self._scroll, b"minimumHeight", self)
        anim.setDuration(SessionTile.ENTER_MS)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._on_height_anim_done)
        self._height_anim = anim
        anim.start()

    def _on_height_anim_done(self) -> None:
        self._height_anim = None

    def _any_agents(self) -> bool:
        return any(t.has_agents() for t in self._tiles.values())

    def _agent_extra(self) -> int:
        """Measured height an agents row adds to a tile. Taken from a real tile
        that has agents (so the reserve's add-back and the _tile_overhead
        subtraction use the SAME number and can't drift), cached once."""
        if self._agent_oh is None:
            tile = next((t for t in self._tiles.values() if t.has_agents()), None)
            if tile is None:
                return AGENT_ROW_FALLBACK  # nothing to measure yet
            self._agent_oh = tile.agents_box_height() + AGENTS_ROW_SPACING
        return self._agent_oh

    def _tile_overhead(self) -> int:
        """Tile height beyond the mascot AND the (optional) agents row — i.e. the
        labels + spacing + margins. Measured once from a real, already-styled
        tile (sizeHint minus the sprite size, minus the agents row if that tile
        has one). Width animations don't affect height, so a tile mid-enter
        measures fine."""
        if self._tile_oh is None:
            tile = next(iter(self._tiles.values()), None)
            if tile is None or self._sprite_size is None:
                return 64  # not cached — recompute once a real tile exists
            h = tile.sizeHint().height() - self._sprite_size
            if tile.has_agents():
                h -= self._agent_extra()
            self._tile_oh = max(0, h)
        return self._tile_oh

    def _reserved_height_for(self, sprite_size: int) -> int:
        # mascot + label overhead + an agents row (when any tile has subagents)
        # + row margins (8) + horizontal-scrollbar room (14). Kept uniform across
        # tiles so rows line up and labels are never clipped.
        agents_extra = self._agent_extra() if self._any_agents() else 0
        return sprite_size + self._tile_overhead() + agents_extra + 8 + 14

    def reserved_current(self) -> int:
        """Currently reserved scroll height (may be mid height-animation)."""
        return self._scroll.minimumHeight()

    def reserved_target(self) -> int:
        """Settled reserved scroll height for the current tile size — lets the
        host window aim at the final size while this is still animating."""
        if self._sprite_size is None:
            return self._scroll.minimumHeight()
        return self._reserved_height_for(self._sprite_size)

    def stop_all(self) -> None:
        if self._height_anim is not None:
            self._height_anim.stop()
            self._height_anim = None
        for anim in list(self._anims.values()):
            anim.stop()
        self._anims.clear()
        for tile in list(self._tiles.values()) + list(self._leaving.values()):
            tile.stop()


# ---------------------------------------------------------------------------
# Compact mode — a denser list view (one horizontal row per session) for when
# you're running many sessions. Distinct from the shelf (vertical mascots) and
# from mini (the tiny readout).
# ---------------------------------------------------------------------------

COMPACT_MASCOT = 38

COMPACT_STYLESHEET = f"""
QWidget#compactRoot {{ background: {_BG}; }}
QWidget#compactTitleBar {{ background: #0b0e13; }}
QLabel#compactTitle {{ font-size: 12px; font-weight: 700; color: {_TEXT};
                       letter-spacing: 1.5px; }}
QToolButton#compactBtn {{ background: transparent; color: #CE7D6B; border: none;
                          font-size: 13px; padding: 2px 7px; }}
QToolButton#compactBtn:hover {{ background: #1f2937; }}
QToolButton#compactSeg {{ background: transparent; color: {_MUTED}; border: none;
                          font-size: 12px; padding: 2px 6px; }}
QToolButton#compactSeg:hover {{ background: #1f2937; color: {_TEXT}; }}
QToolButton#compactSeg:checked {{ background: #243044; color: #CE7D6B; }}
QWidget#compactRow:hover {{ background: #161b22; }}
QLabel#compactBarLabel {{ font-size: 10px; font-weight: 600; color: {_MUTED};
                          letter-spacing: 1px; }}
QLabel#compactPctLbl {{ font-size: 11px; font-weight: 700; color: {_TEXT}; }}
QLabel#compactReset {{ font-size: 10px; color: {_MUTED}; }}
QLabel#compactRowTokens {{ font-size: 11px; font-weight: 700; color: {_MUTED}; }}
QLabel#compactRowDot {{ font-size: 11px; }}
QLabel#compactRowActivity {{ font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#compactRowAgents {{ font-size: 10px; font-weight: 700; color: {_MUTED}; }}
QScrollArea#compactScroll {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: #374151; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


class CompactRow(QWidget):
    """One session as a horizontal row: small mascot | title + tokens, then a
    second line of ``● activity · target`` (or 'last active …' when idle)."""

    _DOT = "●"

    def __init__(self, session_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("compactRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._sid = session_id
        self._show_tokens = True

        h = QHBoxLayout(self)
        h.setContentsMargins(10, 5, 10, 5)
        h.setSpacing(9)

        self.sprite = SpritePlayer(size=COMPACT_MASCOT)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(14)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(_IDLE_COLOR))
        self.sprite.setGraphicsEffect(self._glow)
        h.addWidget(self.sprite, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.title = ScrollingLabel(px=12, bold=True, color=_TEXT, max_w=230,
                                    align=Qt.AlignLeft)
        self.tokens = QLabel("", objectName="compactRowTokens")
        self.tokens.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.title, 1)
        top.addWidget(self.tokens, 0)
        col.addLayout(top)

        bot = QHBoxLayout()
        bot.setSpacing(5)
        self.dot = QLabel(self._DOT, objectName="compactRowDot")
        self.activity = QLabel("", objectName="compactRowActivity")
        self.sep = QLabel("·", objectName="compactRowActivity")
        self.target = ScrollingLabel(px=10, bold=False, color=_MUTED,
                                     letter_spacing=0, max_w=170, align=Qt.AlignLeft)
        self.agents = QLabel("", objectName="compactRowAgents")
        bot.addWidget(self.dot, 0)
        bot.addWidget(self.activity, 0)
        bot.addWidget(self.sep, 0)
        bot.addWidget(self.target, 1)
        bot.addWidget(self.agents, 0)
        col.addLayout(bot)

        h.addLayout(col, 1)

    def set_show_tokens(self, on: bool) -> None:
        self._show_tokens = bool(on)

    def update_state(self, state: TranscriptState) -> None:
        self.title.setText(state.project_name or "unknown")
        idle = state.is_stale or state.activity == Activity.IDLE
        color = ACTIVITY_COLORS.get(state.activity, _IDLE_COLOR)
        if idle:
            self._glow.setColor(QColor(_IDLE_COLOR))
            self._glow.setBlurRadius(6)
            self.dot.setStyleSheet(f"color: {_IDLE_COLOR};")
            self.activity.setText(ACTIVITY_LABELS[Activity.IDLE])
            self.activity.setStyleSheet(f"color: {_IDLE_COLOR};")
            self.sep.hide()
            self.target.setText(_ago_text(state.last_event_ts),
                                tooltip=_abs_time_text(state.last_event_ts))
            self.sprite.set_anims(f"crow:{self._sid}:idle", _IDLE_ANIMS)
        else:
            self._glow.setColor(QColor(color))
            self._glow.setBlurRadius(14)
            self.dot.setStyleSheet(f"color: {color};")
            self.activity.setText(ACTIVITY_LABELS.get(state.activity, ""))
            self.activity.setStyleSheet(f"color: {color};")
            tgt = state.target or state.tool_name or ""
            self.sep.setVisible(bool(tgt))
            self.target.setText(tgt)
            anims = ACTIVITY_ANIMS.get(state.activity) or _IDLE_ANIMS
            self.sprite.set_anims(f"crow:{self._sid}:{state.activity.value}", anims)

        tk = getattr(state, "tokens", None)
        if self._show_tokens and tk is not None and tk.work > 0:
            self.tokens.setText(fmt_tokens(tk.work))
            self.sprite.setToolTip(_token_tooltip(tk))
        else:
            self.tokens.setText("")
            self.sprite.setToolTip("")

    def update_agents(self, agents) -> None:
        n = len(agents)
        self.agents.setText(f"+{n}" if n else "")
        self.agents.setToolTip(
            f"{n} subagent{'s' if n != 1 else ''}" if n else "")

    def stop(self) -> None:
        self.sprite.stop()


class CompactView(QWidget):
    """Compact mode window: slim usage bars over a scrollable list of session
    rows. Frameless, draggable by its title bar, always-on-top, no taskbar."""

    set_mode_requested = Signal(str)  # a view segment -> switch to that mode
    grow_requested = Signal()    # double-click title / menu -> full view
    hide_requested = Signal()    # close button -> hide to tray
    quit_requested = Signal()

    WIDTH = 400
    MAX_ROWS = 6
    ROW_H = 50

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("compactRoot")
        self.setWindowTitle("Clawdmeter")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(COMPACT_STYLESHEET)
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
        )
        icon_path = assets_root() / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setFixedWidth(self.WIDTH)
        self._press_pos: QPoint | None = None
        self._rows: dict[str, CompactRow] = {}
        self._show_tokens = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # title bar
        self._title_bar = QWidget(objectName="compactTitleBar")
        trow = QHBoxLayout(self._title_bar)
        trow.setContentsMargins(10, 6, 6, 6)
        trow.setSpacing(6)
        icon_lbl = QLabel()
        if icon_path.exists():
            icon_lbl.setPixmap(QIcon(str(icon_path)).pixmap(18, 18))
        trow.addWidget(icon_lbl)
        trow.addWidget(QLabel("CLAWDMETER", objectName="compactTitle"))
        trow.addStretch(1)
        # View switcher segments (active highlighted), matching the full window.
        self._segs: dict[str, QToolButton] = {}
        for mode, glyph, tip in (("full", "▢", "Full view"),
                                 ("compact", "☰", "Compact view"),
                                 ("mini", "▪", "Mini view")):
            b = self._tbtn(glyph, tip)
            b.setObjectName("compactSeg")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=mode: self.set_mode_requested.emit(m))
            self._segs[mode] = b
            trow.addWidget(b)
        self.set_active_mode("compact")
        self.close_btn = self._tbtn("✕", "Hide to tray")  # ✕
        self.close_btn.clicked.connect(self.hide_requested.emit)
        trow.addSpacing(4)
        trow.addWidget(self.close_btn)
        outer.addWidget(self._title_bar)

        # slim bars
        bars = QWidget()
        bcol = QVBoxLayout(bars)
        bcol.setContentsMargins(12, 6, 12, 8)
        bcol.setSpacing(3)
        self.s_label, self.s_pct, self.s_bar, self.s_reset = self._slim_bar(bcol)
        bcol.addSpacing(4)
        self.w_label, self.w_pct, self.w_bar, self.w_reset = self._slim_bar(bcol)
        outer.addWidget(bars)

        # scrollable session list
        self._scroll = QScrollArea(objectName="compactScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._list_widget = QWidget(objectName="compactList")
        self._list_widget.setStyleSheet("QWidget#compactList { background: transparent; }")
        self._list = QVBoxLayout(self._list_widget)
        self._list.setContentsMargins(0, 0, 0, 4)
        self._list.setSpacing(0)
        self._list.addStretch(1)
        self._scroll.setWidget(self._list_widget)
        outer.addWidget(self._scroll, 1)

        menu = QMenu(self)
        act_full = QAction("Full view", self)
        act_full.triggered.connect(self.grow_requested.emit)
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_full)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._menu = menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda p: self._menu.exec(self.mapToGlobal(p)))

    def _tbtn(self, glyph: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setObjectName("compactBtn")
        b.setText(glyph)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def set_active_mode(self, mode: str) -> None:
        """Highlight the active view segment."""
        for m, b in self._segs.items():
            b.setChecked(m == mode)

    def _slim_bar(self, parent_col: QVBoxLayout):
        head = QHBoxLayout()
        head.setSpacing(6)
        label = QLabel("", objectName="compactBarLabel")
        pct = QLabel("-", objectName="compactPctLbl")
        pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(label)
        head.addStretch(1)
        head.addWidget(pct)
        bar = UsageBar(height=8)
        reset = QLabel("", objectName="compactReset")
        parent_col.addLayout(head)
        parent_col.addWidget(bar)
        parent_col.addWidget(reset)
        return label, pct, bar, reset

    @staticmethod
    def _apply_bar(label, pct, bar, reset, title, value, overage,
                   reset_min, tokens, show_tokens) -> None:
        if overage > 0:
            # Bar stays full in its normal colour with a red overflow past 100%;
            # the label gets a red OVERAGE tag and the % reads e.g. 120%.
            label.setText(
                f'{title} <span style="color:{_BAR_OVERAGE}; font-weight:700">'
                f'OVERAGE</span>')
            pct.setText(f"{100 + overage}%")
            bar.set_values(100, overage, heat(100))
        else:
            label.setText(title)
            pct.setText(f"{value}%")
            bar.set_values(value, 0, heat(value))
        line = f"resets in {format_minutes(reset_min)}"
        if show_tokens:
            line += f" · {fmt_tokens(tokens)}"
        reset.setText(line)

    def update_usage(self, s, sr: int, wr: int, ovr: int, show_tokens: bool) -> None:
        self._apply_bar(self.s_label, self.s_pct, self.s_bar, self.s_reset,
                        "SESSION 5h", s.session_pct, 0, sr, s.tokens_5h, show_tokens)
        ov = getattr(s, "overage_pct", 0)
        self._apply_bar(self.w_label, self.w_pct, self.w_bar, self.w_reset,
                        "WEEKLY 7d", s.weekly_pct, ov, wr, s.tokens_7d, show_tokens)
        self.w_bar.setToolTip(
            f"Overage resets in {format_minutes(ovr)}" if ov > 0 else "")

    def set_show_tokens(self, on: bool) -> None:
        self._show_tokens = bool(on)
        for row in self._rows.values():
            row.set_show_tokens(on)

    def set_sessions(self, states) -> None:
        incoming = {s.session_id: s for s in states if s.session_id}
        for sid in list(self._rows):
            if sid not in incoming:
                row = self._rows.pop(sid)
                self._list.removeWidget(row)
                row.stop()
                row.deleteLater()
        # Only churn the layout (detach + re-insert every row) when the roster
        # ORDER actually changed; a steady roster just updates rows in place.
        new_order = list(incoming)
        if new_order != list(self._rows):
            for row in self._rows.values():
                self._list.removeWidget(row)
            ordered = {}
            for index, sid in enumerate(new_order):
                row = self._rows.get(sid)
                if row is None:
                    row = CompactRow(sid, parent=self._list_widget)
                    row.set_show_tokens(self._show_tokens)
                self._list.insertWidget(index, row)
                row.show()
                ordered[sid] = row
            self._rows = ordered
        for sid, st in incoming.items():
            row = self._rows[sid]
            row.update_state(st)
            row.update_agents(getattr(st, "agents", []) or [])
        self._relayout()

    def _relayout(self) -> None:
        visible = min(max(1, len(self._rows)), self.MAX_ROWS)
        if visible == getattr(self, "_last_visible_rows", None):
            return
        self._last_visible_rows = visible
        self._scroll.setFixedHeight(visible * self.ROW_H + 6)
        self.adjustSize()

    def stop_all(self) -> None:
        for row in self._rows.values():
            row.stop()

    # --- frameless drag + grow-on-double-click (title bar only) -------------
    def _in_title(self, e) -> bool:
        return self._title_bar.geometry().contains(e.position().toPoint())

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self._in_title(e):
            self._press_pos = e.globalPosition().toPoint()
            e.accept()

    def mouseMoveEvent(self, e) -> None:
        if (e.buttons() & Qt.LeftButton) and self._press_pos is not None:
            moved = (e.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved >= QApplication.startDragDistance():
                self._press_pos = None
                winutil.start_native_move(int(self.winId()))
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._press_pos = None

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self._in_title(e):
            self.grow_requested.emit()
