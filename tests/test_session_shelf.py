"""Offscreen smoke test for the mascot shelf UI (Variant C).

Builds a SessionShelf, feeds it fake sessions, and asserts the diff-by-id
behaviour: tiles are added, reused (not rebuilt), updated, and removed. Runs
fully headless via QT_QPA_PLATFORM=offscreen so it works in CI with no display.

This depends on transcript.py exposing the contract symbols (ACTIVITY_COLORS
and the extended TranscriptState fields). While transcript.py is being edited in
parallel those may be absent — in that case the whole module is skipped rather
than failing, so this test never blocks the shelf work.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_session_shelf.py`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# Comfortably longer than the enter/leave/resize animations so they settle.
SETTLE_MS = 400

# The shelf codes to the locked contract; if transcript.py hasn't landed the
# new symbols yet, skip cleanly instead of erroring the suite.
transcript = pytest.importorskip("transcript")
if not hasattr(transcript, "ACTIVITY_COLORS"):
    pytest.skip("transcript.ACTIVITY_COLORS not present yet", allow_module_level=True)

from transcript import Activity, TranscriptState  # noqa: E402
import session_shelf  # noqa: E402
from session_shelf import SessionShelf, SessionTile  # noqa: E402


_app = QApplication.instance() or QApplication([])


def _state(session_id: str, activity: Activity, *, project: str = "proj",
           tool: str | None = None, stale: bool = False) -> TranscriptState:
    return TranscriptState(
        activity=activity,
        tool_name=tool,
        transcript_path=None,
        last_event_ts=1000.0,
        session_id=session_id,
        cwd=None,
        project_name=project,
        is_stale=stale,
    )


def test_sprite_size_scales_with_count():
    f = SessionShelf._sprite_size_for
    assert f(0) == 200
    assert f(1) == 200
    assert f(2) == 160
    assert f(3) == 130
    assert f(4) == 110
    assert f(6) == 110


def test_add_update_remove_diffs_by_id():
    shelf = SessionShelf()

    shelf.set_sessions([
        _state("a", Activity.CODING, project="alpha"),
        _state("b", Activity.THINKING, project="beta"),
    ])
    assert set(shelf._tiles) == {"a", "b"}
    assert shelf.header.text() == "ACTIVE SESSIONS — 2"

    # Same ids again -> tiles reused, not rebuilt (diffing, no flicker).
    tile_a = shelf._tiles["a"]
    shelf.set_sessions([
        _state("a", Activity.READING, project="alpha"),
        _state("b", Activity.THINKING, project="beta"),
    ])
    assert shelf._tiles["a"] is tile_a
    assert tile_a.project_label.text() == "alpha"

    # Drop one, add another -> count and membership track the diff.
    shelf.set_sessions([
        _state("a", Activity.CODING, project="alpha"),
        _state("c", Activity.PLANNING, project="gamma"),
    ])
    assert set(shelf._tiles) == {"a", "c"}
    assert shelf.header.text() == "ACTIVE SESSIONS — 2"

    shelf.set_sessions([])
    assert shelf._tiles == {}
    assert shelf.header.text() == "ACTIVE SESSIONS — 0"

    shelf.stop_all()


def test_tile_idle_shows_last_active_and_calm_anim():
    tile = SessionTile("x", sprite_size=120)
    tile.update_state(_state("x", Activity.IDLE, project="notes-cli", stale=True))
    assert tile.activity_label.text() == "IDLE"
    assert "last active" in tile.sub_label.text() or tile.sub_label.text() == "idle"
    tile.stop()


def test_tile_live_shows_tool_and_activity():
    tile = SessionTile("y", sprite_size=120)
    tile.update_state(_state("y", Activity.CODING, project="api", tool="Edit"))
    assert tile.activity_label.text() == "CODING"
    assert tile.sub_label.text() == "Edit"
    tile.stop()


def _layout_order(shelf: SessionShelf) -> list[str]:
    """Session ids in left-to-right layout order (not first-seen order)."""
    by_widget = {tile: sid for sid, tile in shelf._tiles.items()}
    order = []
    for i in range(shelf._row.count()):
        w = shelf._row.itemAt(i).widget()
        if w in by_widget:
            order.append(by_widget[w])
    return order


def test_tiles_reorder_to_match_newest_first():
    # Regression: the shelf must render in the watcher's newest-first order, not
    # the order ids were first seen. Sessions take turns being most-recent.
    shelf = SessionShelf()
    shelf.set_sessions([
        _state("a", Activity.CODING),
        _state("b", Activity.THINKING),
        _state("c", Activity.READING),
    ])
    assert _layout_order(shelf) == ["a", "b", "c"]

    # c is now newest -> it must move to the front.
    shelf.set_sessions([
        _state("c", Activity.READING),
        _state("a", Activity.CODING),
        _state("b", Activity.THINKING),
    ])
    assert _layout_order(shelf) == ["c", "a", "b"]

    # A brand-new session promoted to front while one drops off.
    shelf.set_sessions([
        _state("d", Activity.PLANNING),
        _state("c", Activity.READING),
        _state("b", Activity.THINKING),
    ])
    assert _layout_order(shelf) == ["d", "c", "b"]
    assert set(shelf._tiles) == {"b", "c", "d"}
    shelf.stop_all()


def test_existing_tile_sprite_resizes_with_count():
    # Regression: a survivor tile's mascot must re-scale when the count (and so
    # the target size) changes — not just the QLabel box, the rendered pixmap.
    # The resize is animated, so settle each step before asserting the result.
    shelf = SessionShelf()
    shelf.show()
    shelf.set_sessions([_state("a", Activity.CODING)])
    QTest.qWait(SETTLE_MS)
    tile_a = shelf._tiles["a"]
    assert tile_a.sprite._size == 200          # solo -> 200

    shelf.set_sessions([
        _state("a", Activity.CODING),
        _state("b", Activity.THINKING),
        _state("c", Activity.READING),
    ])
    QTest.qWait(SETTLE_MS)
    assert tile_a.sprite._size == 130          # 3 sessions -> 130, survivor re-scaled
    assert tile_a.sprite.maximumWidth() == 130

    shelf.set_sessions([_state("a", Activity.CODING)])
    QTest.qWait(SETTLE_MS)
    assert tile_a.sprite._size == 200          # back to solo -> re-grown
    shelf.stop_all()


def test_enter_starts_collapsed_and_leave_defers_removal():
    # Timing-free check of the transition lifecycle: a new tile starts collapsed
    # (width animation engaged) and a removed tile animates out (parked in
    # _leaving, still in the layout) rather than being deleted instantly.
    shelf = SessionShelf()
    shelf.set_sessions([_state("a", Activity.CODING)])
    shelf.set_sessions([_state("a", Activity.CODING), _state("b", Activity.THINKING)])
    tile_b = shelf._tiles["b"]
    assert tile_b.maximumWidth() == 0          # enter begins fully collapsed
    assert tile_b in shelf._anims              # its enter animation is tracked

    tile_a = shelf._tiles["a"]
    shelf.set_sessions([_state("b", Activity.THINKING)])
    assert "a" not in shelf._tiles             # removed from the live set...
    assert shelf._leaving.get("a") is tile_a   # ...but parked, animating out
    assert tile_a in shelf._anims
    # Still parented to the row (visible while it collapses), not yet deleted.
    assert tile_a.parent() is not None
    shelf.stop_all()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
