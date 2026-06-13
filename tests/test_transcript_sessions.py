"""Unit tests for the PURE multi-session helpers in transcript.py.

These cover only the disk/Qt-free helpers (`project_name_from_cwd`,
`is_subagent_path`, `select_active`, `_classify`) so they run fast and
deterministically.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_transcript_sessions.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from transcript import (  # noqa: E402
    ACTIVE_WINDOW_SECONDS,
    MAX_SESSIONS,
    Activity,
    _classify,
    is_subagent_path,
    project_name_from_cwd,
    select_active,
)


def test_project_name_from_cwd_uses_cwd_leaf():
    cwd = r"C:\Claude\ClonedRepos\Clawdmeter-Windows"
    assert project_name_from_cwd(cwd, None) == "Clawdmeter-Windows"


def test_project_name_from_cwd_falls_back_to_transcript_parent():
    # cwd missing -> use the transcript's parent directory name.
    tp = Path("/home/u/.claude/projects/C--Some-Project/abc123.jsonl")
    assert project_name_from_cwd(None, tp) == "C--Some-Project"
    assert project_name_from_cwd("", tp) == "C--Some-Project"


def test_project_name_from_cwd_total_fallback():
    assert project_name_from_cwd(None, None) == "unknown"
    assert project_name_from_cwd("", None) == "unknown"


def test_is_subagent_path():
    sub = Path("/home/u/.claude/projects/proj/abc-uuid/subagents/agent-x.jsonl")
    normal = Path("/home/u/.claude/projects/proj/abc-uuid.jsonl")
    assert is_subagent_path(sub) is True
    assert is_subagent_path(normal) is False


def test_select_active_filters_subagents_window_and_sorts():
    now = 1_000_000.0
    a = Path("/p/a.jsonl")            # newest, in window
    b = Path("/p/b.jsonl")            # older, in window
    old = Path("/p/old.jsonl")        # outside the window -> dropped
    sub = Path("/p/x/subagents/s.jsonl")  # subagent -> dropped
    entries = [
        (b, now - 100.0),
        (sub, now - 1.0),
        (a, now - 5.0),
        (old, now - ACTIVE_WINDOW_SECONDS - 1.0),
    ]
    result = select_active(entries, now)
    assert result == [a, b]           # newest-first, no subagent, no stale-out-of-window


def test_select_active_caps_at_limit():
    now = 1_000_000.0
    # MAX_SESSIONS + 2 fresh files, each progressively older.
    entries = [
        (Path(f"/p/{i}.jsonl"), now - float(i)) for i in range(MAX_SESSIONS + 2)
    ]
    result = select_active(entries, now)
    assert len(result) == MAX_SESSIONS
    # The newest (smallest age) survive, in newest-first order.
    assert result == [Path(f"/p/{i}.jsonl") for i in range(MAX_SESSIONS)]


def test_classify_mcp_tool_is_integrating():
    content = [{"type": "tool_use", "name": "mcp__github__get_pr"}]
    activity, tool = _classify(content)
    assert activity is Activity.INTEGRATING
    assert tool == "github/get_pr"


def test_classify_unknown_tool_is_coding():
    activity, tool = _classify([{"type": "tool_use", "name": "SomeNewTool"}])
    assert activity is Activity.CODING
    assert tool == "SomeNewTool"


def test_classify_thinking_or_text_is_thinking():
    assert _classify([{"type": "thinking"}])[0] is Activity.THINKING
    assert _classify([{"type": "text", "text": "hi"}])[0] is Activity.THINKING


def test_classify_empty_is_idle():
    activity, tool = _classify([])
    assert activity is Activity.IDLE
    assert tool is None


def test_classify_last_tool_use_wins():
    # Both blocks present: the last tool_use should win over earlier ones/text.
    content = [
        {"type": "text", "text": "thinking out loud"},
        {"type": "tool_use", "name": "Read"},
        {"type": "tool_use", "name": "Bash"},
    ]
    activity, tool = _classify(content)
    assert activity is Activity.CODING
    assert tool == "Bash"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
