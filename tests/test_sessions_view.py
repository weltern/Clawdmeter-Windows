"""Unit tests for dashboard._view_states — how the Settings session-view toggles
(show multiple sessions / show subagents) transform the watcher's states.

Pure logic, but importing dashboard pulls in PySide6, so run headless via
QT_QPA_PLATFORM=offscreen. Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcript import Activity, AgentState, TranscriptState  # noqa: E402
from dashboard import _SINGLE_TILE_ID, _view_states  # noqa: E402


def _st(sid: str, agents=()) -> TranscriptState:
    return TranscriptState(
        activity=Activity.CODING,
        tool_name=None,
        transcript_path=None,
        last_event_ts=1.0,
        session_id=sid,
        cwd=None,
        project_name=sid,
        is_stale=False,
        agents=list(agents),
    )


def test_both_on_passes_through():
    a = _st("a", [AgentState("x", Activity.CODING, None)])
    b = _st("b")
    out = _view_states([a, b], show_multiple=True, show_subagents=True)
    assert [s.session_id for s in out] == ["a", "b"]
    assert len(out[0].agents) == 1


def test_subagents_off_strips_agents():
    a = _st("a", [AgentState("x", Activity.CODING, None)])
    out = _view_states([a], show_multiple=True, show_subagents=False)
    assert out[0].agents == []


def test_single_mode_keeps_focused_under_stable_id():
    a, b = _st("a"), _st("b")
    out = _view_states([a, b], show_multiple=False, show_subagents=True)
    assert len(out) == 1
    assert out[0].session_id == _SINGLE_TILE_ID
    assert out[0].project_name == "a"  # focused (newest) session's content

    # When the focused session changes, the tile id stays stable so the shelf
    # reuses (updates) the one tile instead of swapping it with an animation.
    out2 = _view_states([b, a], show_multiple=False, show_subagents=True)
    assert out2[0].session_id == _SINGLE_TILE_ID
    assert out2[0].project_name == "b"


def test_single_mode_empty_stays_empty():
    assert _view_states([], show_multiple=False, show_subagents=True) == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
