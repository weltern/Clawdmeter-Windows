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

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Qt,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor

from mood import GROUP_ANIMS
from sprite_player import SpritePlayer
from transcript import (
    ACTIVITY_ANIMS,
    ACTIVITY_COLORS,
    ACTIVITY_LABELS,
    Activity,
    TranscriptState,
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
QLabel#tileProject {{
    font-size: 13px; font-weight: 700; color: {_TEXT}; letter-spacing: 0.5px;
}}
QLabel#tileActivity {{
    font-size: 11px; font-weight: 600; letter-spacing: 1px;
}}
QLabel#tileDot {{ font-size: 11px; }}
QLabel#tileSub {{ font-size: 10px; color: {_MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{
    background: #374151; border-radius: 4px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #4b5563; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


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


class SessionTile(QWidget):
    """One session: glowing mascot + project name + activity + status dot."""

    # A single status dot reused for both states — the glyph stays, only its
    # color changes (warm/active accent when live, dim grey when idle).
    _DOT = "●"

    def __init__(self, session_id: str, sprite_size: int = 120, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._session_id = session_id
        self._fade = None  # QGraphicsOpacityEffect during an enter/leave anim

        col = QVBoxLayout(self)
        # Side margins double as the inter-tile gap (the row spacing is 0) so a
        # leaving tile collapses to truly zero width with no leftover gap.
        col.setContentsMargins(12, 4, 12, 4)
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

        self.project_label = QLabel("…", objectName="tileProject")
        self.project_label.setAlignment(Qt.AlignHCenter)
        col.addWidget(self.project_label)

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

        # Secondary line — tool name when live, "last active Nm ago" when idle.
        self.sub_label = QLabel("", objectName="tileSub")
        self.sub_label.setAlignment(Qt.AlignHCenter)
        col.addWidget(self.sub_label)

    def set_sprite_size(self, px: int) -> None:
        # set_size (not setFixedSize) so the mascot pixmap re-scales too; it
        # early-returns when the size is unchanged, so calling it every poll is
        # cheap and won't churn the layout.
        self.sprite.set_size(px)

    def update_state(self, state: TranscriptState) -> None:
        """Reflect one session's state: name, activity color/label, glow, dot,
        and the mascot animation (idle sessions get the calm sleep loop)."""
        self.project_label.setText(state.project_name or "unknown")

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
            self.sub_label.setText(_ago_text(state.last_event_ts))
            self.sprite.set_anims(f"{self._session_id}:idle", _IDLE_ANIMS)
        else:
            self._glow.setColor(QColor(color))
            self._glow.setBlurRadius(38)
            self.activity_label.setText(ACTIVITY_LABELS.get(state.activity, ""))
            self.activity_label.setStyleSheet(f"color: {color};")
            # Live dot tracks the activity color so the tile reads as a unit.
            self.status_dot.setStyleSheet(f"color: {color};")
            self.sub_label.setText(state.tool_name or "")
            anims = ACTIVITY_ANIMS.get(state.activity) or _IDLE_ANIMS
            self.sprite.set_anims(f"{self._session_id}:{state.activity.value}", anims)

    def stop(self) -> None:
        self.sprite.stop()

    # --- enter/leave transitions --------------------------------------------
    # A tile fades + expands in (width 0 -> natural) and fades + collapses out
    # (natural -> 0), so the row reflows smoothly as sessions come and go. The
    # opacity rides a QGraphicsOpacityEffect on the tile; the width rides the
    # widget's own maximumWidth property (the SetNoConstraint above lets it
    # shrink past the mascot). The shelf owns the animation objects so they
    # aren't garbage-collected mid-flight.
    ENTER_MS = 240
    LEAVE_MS = 200
    _SIZE_MAX = 16777215  # Qt's QWIDGETSIZE_MAX — the "no maximum" sentinel.

    def _begin_fade(self) -> QGraphicsOpacityEffect:
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._fade = eff
        return eff

    def build_enter_anim(self) -> QParallelAnimationGroup:
        target = max(1, self.sizeHint().width())
        eff = self._begin_fade()
        eff.setOpacity(0.0)
        self.setMaximumWidth(0)
        grp = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(eff, b"opacity", grp)
        fade.setDuration(self.ENTER_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        grow = QPropertyAnimation(self, b"maximumWidth", grp)
        grow.setDuration(self.ENTER_MS)
        grow.setStartValue(0)
        grow.setEndValue(target)
        grow.setEasingCurve(QEasingCurve.OutCubic)
        grp.addAnimation(fade)
        grp.addAnimation(grow)
        grp.finished.connect(self._clear_transition)
        return grp

    def build_leave_anim(self) -> QParallelAnimationGroup:
        eff = self._begin_fade()
        eff.setOpacity(1.0)
        grp = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(eff, b"opacity", grp)
        fade.setDuration(self.LEAVE_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InCubic)
        shrink = QPropertyAnimation(self, b"maximumWidth", grp)
        shrink.setDuration(self.LEAVE_MS)
        shrink.setStartValue(max(1, self.width()))
        shrink.setEndValue(0)
        shrink.setEasingCurve(QEasingCurve.InCubic)
        grp.addAnimation(fade)
        grp.addAnimation(shrink)
        return grp

    def _clear_transition(self) -> None:
        """Release the width cap and drop the opacity effect so the tile renders
        normally (and the sprite's own glow is unobstructed) after an enter."""
        self.setMaximumWidth(self._SIZE_MAX)
        self.setGraphicsEffect(None)
        self._fade = None


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
        # Tiles mid-leave (animating out, still in the layout) keyed by id, and
        # the live animation groups keyed by tile so they aren't GC'd and so we
        # can tell when the shelf is "settled" (safe to reorder).
        self._leaving: dict[str, SessionTile] = {}
        self._anims: dict[SessionTile, QParallelAnimationGroup] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

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
                    self._tiles[sid] = tile
                    self._insert_live(tile, index)
                    tile.set_sprite_size(size)
                    tile.update_state(state)
                    self._start_enter(tile)
                    continue
            tile.set_sprite_size(size)
            tile.update_state(state)

        # Re-assert newest-first order. _reorder_live short-circuits when the
        # live order is unchanged (the common case) so there's no per-poll
        # churn, operates only on live tiles, and leaves entering tiles' width
        # animations running as they're repositioned.
        self._reorder_live(incoming)

        self._refresh_min_height()
        self.header.setText(f"ACTIVE SESSIONS — {len(self._tiles)}")

    def _start_enter(self, tile: SessionTile) -> None:
        anim = tile.build_enter_anim()
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
        self._refresh_min_height()

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

    def _refresh_min_height(self) -> None:
        # Reserve vertical room for a whole tile (mascot + the labels beneath
        # it). Without this the shelf gets squeezed in a short window and, since
        # the vertical scrollbar is off, the labels below the mascot are clipped.
        # +14 leaves room for the horizontal scrollbar shown when tiles overflow.
        wanted_h = self._row_widget.sizeHint().height() + 14
        if wanted_h != self._scroll.minimumHeight():
            self._scroll.setMinimumHeight(wanted_h)

    def stop_all(self) -> None:
        for anim in list(self._anims.values()):
            anim.stop()
        self._anims.clear()
        for tile in list(self._tiles.values()) + list(self._leaving.values()):
            tile.stop()
