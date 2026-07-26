"""The shelf sizes every tile identically, from one calculation.

Per-tile sizing could never satisfy "all the mascots the same size" or "the
session names line up": each tile carries a different amount of text (an idle
session has a "last active" line, a live one may not) and a different number of
subagents, so each was left a different amount of room for its mascot. The shelf
computes the box once from the worst case and hands it to every tile.

Elements drop out rather than scaling into illegibility: mascot + subagent
mascots -> mascot + "N subagents" -> text only.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import session_shelf  # noqa: E402
from session_shelf import SessionShelf  # noqa: E402
from transcript import Activity, AgentState, TranscriptState  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _state(sid, proj, activity=Activity.THINKING, tool=None):
    return TranscriptState(activity=activity, tool_name=tool,
                           transcript_path=None, last_event_ts=1000.0,
                           session_id=sid, cwd=None, project_name=proj,
                           is_stale=False)


def _shelf(n=4, mixed=True):
    """A live shelf. ``mixed`` deliberately varies the text per tile — some with
    a sub-line, some without — which is what exposed the misalignment."""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    shelf = SessionShelf()
    lay.addWidget(shelf, 1)
    host.resize(900, 700)
    host.show()
    states = []
    for i in range(n):
        if mixed and i % 2:
            states.append(_state(f"s{i}", f"session-{i}", Activity.CODING,
                                 "dashboard.py"))
        else:
            states.append(_state(f"s{i}", f"a-much-longer-session-name-{i}"))
    shelf.set_sessions(states)
    QTest.qWait(600)
    return host, shelf


def _settle(host, h, w=900):
    host.resize(w, h)
    QTest.qWait(400)


def _edges(shelf):
    return [t.sprite._render_size() for t in shelf._tiles.values()]


def _name_tops(shelf):
    return [t.project_label.mapTo(t, t.project_label.rect().topLeft()).y()
            for t in shelf._tiles.values()]


# --- uniformity --------------------------------------------------------------

def test_every_mascot_is_exactly_the_same_size():
    host, shelf = _shelf()
    try:
        for h in (700, 500, 400, 300):
            _settle(host, h)
            e = _edges(shelf)
            assert len(set(e)) == 1, f"uneven mascots at {h}px: {e}"
    finally:
        host.deleteLater()


def test_session_names_line_up_across_tiles_with_different_text():
    # Half these tiles have a sub-line and half do not. When the sprite absorbed
    # the slack, two-row tiles gave their mascot more height and their names
    # started lower than the three-row tiles'.
    host, shelf = _shelf()
    try:
        for h in (700, 400, 300):
            _settle(host, h)
            tops = _name_tops(shelf)
            assert len(set(tops)) == 1, f"names misaligned at {h}px: {tops}"
    finally:
        host.deleteLater()


def test_a_tile_with_subagents_keeps_the_same_mascot_as_its_neighbours():
    # A session sprouting subagents used to steal height from its own mascot,
    # leaving it visibly smaller than the rest of the row.
    host, shelf = _shelf()
    try:
        first = next(iter(shelf._tiles.values()))
        first.update_agents([
            AgentState(agent_id=f"a{i}", activity=Activity.THINKING,
                       tool_name=None) for i in range(3)
        ])
        QTest.qWait(300)
        for h in (700, 420, 300):
            _settle(host, h)
            e = _edges(shelf)
            assert len(set(e)) == 1, f"agents skewed the row at {h}px: {e}"
    finally:
        host.deleteLater()


# --- scaling -----------------------------------------------------------------

def test_mascots_scale_up_and_down_with_the_window():
    host, shelf = _shelf(n=2)
    try:
        _settle(host, 300)
        small = _edges(shelf)[0]
        _settle(host, 800)
        big = _edges(shelf)[0]
        assert big > small, f"did not grow: {small} -> {big}"
        _settle(host, 300)
        assert _edges(shelf)[0] == small, "did not return to the same size"
    finally:
        host.deleteLater()


def test_the_mascot_recovers_after_collapsing_to_text_only():
    host, shelf = _shelf(n=2)
    try:
        _settle(host, 800)
        assert _edges(shelf)[0] > 0
        _settle(host, 150)
        assert _edges(shelf)[0] == 0, "should collapse to text-only"
        _settle(host, 800)
        assert _edges(shelf)[0] > 0, "must come back — this used to self-lock"
    finally:
        host.deleteLater()


def test_the_mascot_is_never_drawn_below_the_legibility_floor():
    host, shelf = _shelf(n=4)
    try:
        for h in range(140, 420, 20):
            _settle(host, h)
            e = _edges(shelf)[0]
            assert e == 0 or e >= SessionShelf.MIN_MASCOT, (
                f"{h}px window rendered a {e}px mascot"
            )
    finally:
        host.deleteLater()


# --- what survives -----------------------------------------------------------

def test_the_text_rows_always_survive():
    host, shelf = _shelf(n=3)
    try:
        for h in (700, 400, 250, 160):
            _settle(host, h)
            for t in shelf._tiles.values():
                assert t.project_label.isVisibleTo(t)
                assert t.activity_label.isVisibleTo(t)
    finally:
        host.deleteLater()


def test_subagents_fall_back_to_a_text_line_when_they_will_not_fit():
    host, shelf = _shelf(n=2)
    try:
        first = next(iter(shelf._tiles.values()))
        first.update_agents([
            AgentState(agent_id=f"a{i}", activity=Activity.THINKING,
                       tool_name=None) for i in range(3)
        ])
        _settle(host, 800)
        assert first._agents_box.isVisibleTo(first), "roomy: draw the mascots"
        _settle(host, 260)
        assert not first._agents_box.isVisibleTo(first), "tight: no clipping"
        assert first.agents_label.isVisibleTo(first)
        assert first.agents_label.text() == "3 subagents"
    finally:
        host.deleteLater()


def test_the_subagent_line_is_singular_for_one():
    host, shelf = _shelf(n=2)
    try:
        first = next(iter(shelf._tiles.values()))
        first.update_agents([AgentState(agent_id="a0",
                                        activity=Activity.THINKING,
                                        tool_name=None)])
        _settle(host, 260)
        assert first.agents_label.text() == "1 subagent"
    finally:
        host.deleteLater()


def test_a_tile_without_subagents_shows_neither_form():
    host, shelf = _shelf(n=2)
    try:
        for h in (800, 260):
            _settle(host, h)
            for t in shelf._tiles.values():
                assert not t._agents_box.isVisibleTo(t)
                assert not t.agents_label.isVisibleTo(t)
    finally:
        host.deleteLater()


def test_an_empty_sub_line_is_hidden_so_later_rows_move_up():
    host, shelf = _shelf(n=1, mixed=False)
    try:
        tile = next(iter(shelf._tiles.values()))
        sid = next(iter(shelf._tiles))
        tile.update_state(_state(sid, "proj", Activity.THINKING, tool=None))
        assert not tile.sub_label.isVisibleTo(tile), (
            "an empty sub-line must not hold its row open above the "
            "subagent count"
        )
    finally:
        host.deleteLater()


# --- stability ---------------------------------------------------------------

def test_re_running_the_layout_changes_nothing():
    # The oscillation guard: sizing is computed from the container, so running
    # it again must be a no-op. If it were not, a resize would never settle.
    host, shelf = _shelf(n=3)
    try:
        for h in (700, 400, 260, 180):
            _settle(host, h)
            first = _edges(shelf)
            for _ in range(5):
                shelf._layout_tiles()
            assert _edges(shelf) == first, f"unstable at {h}px"
    finally:
        host.deleteLater()


def test_the_text_only_floor_is_measured_not_derived_by_subtraction():
    # Regression found by instrumentation: the floor was computed as
    # tile.sizeHint().height() - sprite_size. Once the sprite became
    # scale-to-fit that returned 0, so the floor silently collapsed to 33px and
    # the last text line clipped. It now measures the rows directly.
    host, shelf = _shelf(n=1)
    try:
        tile = next(iter(shelf._tiles.values()))
        floor = shelf._reserved_height_for(0)
        assert floor > tile._text_rows_height(), (
            f"floor {floor}px does not cover the {tile._text_rows_height()}px "
            "of text it exists to protect"
        )
        assert floor >= 100, f"floor collapsed to {floor}px"
    finally:
        host.deleteLater()


def test_the_agents_text_is_themed_not_inline():
    assert "QLabel#tileAgentsText" in session_shelf.SHELF_STYLESHEET
    host, shelf = _shelf(n=1)
    try:
        tile = next(iter(shelf._tiles.values()))
        assert not tile.agents_label.styleSheet()
    finally:
        host.deleteLater()
