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
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QColor, QFont

from mood import GROUP_ANIMS
from sprite_player import SpritePlayer
from transcript import (
    ACTIVITY_ANIMS,
    ACTIVITY_COLORS,
    ACTIVITY_LABELS,
    Activity,
    AgentState,
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
QLabel#tileProject {{ color: {_TEXT}; }}
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


# Cap for the project/title label. Session titles (from ai-title/custom-title)
# are far longer than a cwd leaf, so the label is elided to at most this width
# (or the mascot's width if larger) and the full title moves to a tooltip — a
# long title can't stretch a tile far wider than its mascot.
_LABEL_MIN_W = 150

# Child-mascot (subagent) sizing.
AGENT_SPRITE = 38
# Column spacing above the agents row (matches the tile's QVBoxLayout spacing).
AGENTS_ROW_SPACING = 4
# Height reserved for the agents row before a real one can be measured; once a
# tile has agents the true measured height is used instead (see _agent_extra).
AGENT_ROW_FALLBACK = 46


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

        self.project_label = QLabel("…", objectName="tileProject")
        self.project_label.setAlignment(Qt.AlignHCenter)
        # Set the font in code (not just QSS): elidedText() measures via the
        # widget's fontMetrics(), which does NOT reflect a QSS-applied font, so
        # eliding against the QSS font would under-measure and never truncate.
        _pf = QFont()
        _pf.setPixelSize(13)
        _pf.setBold(True)
        _pf.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
        self.project_label.setFont(_pf)
        # Full (un-elided) label text, kept so we can re-elide when the tile
        # resizes and expose the whole title as a tooltip.
        self._project_text = ""
        self._label_max_w = max(sprite_size, _LABEL_MIN_W)
        self.project_label.setMaximumWidth(self._label_max_w)
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
        # Keep the label cap in step with the mascot width and re-elide.
        new_w = max(px, _LABEL_MIN_W)
        if new_w != self._label_max_w:
            self._label_max_w = new_w
            self._apply_project_label()

    def _apply_project_label(self) -> None:
        """Show the title elided to the tile width, with the full text as a
        tooltip when it doesn't fit."""
        w = self._label_max_w
        self.project_label.setMaximumWidth(w)
        text = self._project_text or "unknown"
        elided = self.project_label.fontMetrics().elidedText(text, Qt.ElideRight, w)
        self.project_label.setText(elided)
        self.project_label.setToolTip(text if elided != text else "")

    def update_state(self, state: TranscriptState) -> None:
        """Reflect one session's state: name, activity color/label, glow, dot,
        and the mascot animation (idle sessions get the calm sleep loop)."""
        self._project_text = state.project_name or "unknown"
        self._apply_project_label()

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
