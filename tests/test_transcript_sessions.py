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
    AGENT_ACTIVE_SECONDS,
    MAX_AGENTS_PER_SESSION,
    MAX_SESSIONS,
    Activity,
    AgentState,
    _classify,
    any_agent_active,
    group_sessions,
    is_agent_transcript,
    is_subagent_path,
    parent_transcript_for_subagent,
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


def test_parent_transcript_for_subagent():
    sub = Path("/u/.claude/projects/proj/abc-uuid/subagents/agent-x.jsonl")
    assert parent_transcript_for_subagent(sub) == Path(
        "/u/.claude/projects/proj/abc-uuid.jsonl"
    )
    # Not a subagent path -> returned unchanged.
    normal = Path("/u/.claude/projects/proj/abc-uuid.jsonl")
    assert parent_transcript_for_subagent(normal) == normal


def test_group_sessions_attaches_agents():
    now = 1_000_000.0
    parent = Path("/p/proj/sess.jsonl")
    a1 = Path("/p/proj/sess/subagents/agent-1.jsonl")
    a2 = Path("/p/proj/sess/subagents/agent-2.jsonl")
    entries = [
        (parent, now - 10.0),
        (a1, now - 2.0),
        (a2, now - 1.0),
    ]
    groups = group_sessions(entries, now)
    assert len(groups) == 1
    p, agents = groups[0]
    assert p == parent
    assert set(agents) == {a1, a2}          # both fresh agents attached
    assert agents[0] == a2                   # newest-first


def test_group_sessions_keeps_supervisor_alive_via_subagents():
    # Parent transcript is older than the shelf window, but an active subagent
    # keeps the session listed (the parent freezes during a Task call).
    now = 1_000_000.0
    parent = Path("/p/proj/sess.jsonl")
    agent = Path("/p/proj/sess/subagents/agent-1.jsonl")
    entries = [
        (parent, now - (ACTIVE_WINDOW_SECONDS + 50.0)),  # parent itself is "stale"
        (agent, now - 3.0),                              # but its agent is live
    ]
    groups = group_sessions(entries, now)
    assert [p for p, _ in groups] == [parent]
    assert groups[0][1] == [agent]


def test_is_agent_transcript_excludes_journal_and_workflows():
    base = "/u/.claude/projects/proj/sess/subagents"
    assert is_agent_transcript(Path(f"{base}/agent-abc.jsonl")) is True
    # journal + the nested workflows tree must NOT count as child agents.
    assert is_agent_transcript(Path(f"{base}/workflows/wf_1/journal.jsonl")) is False
    assert is_agent_transcript(Path(f"{base}/workflows/wf_1/agent-x.jsonl")) is False
    assert is_agent_transcript(Path("/u/.claude/projects/proj/sess.jsonl")) is False


def test_group_sessions_ignores_journal_and_workflow_tree():
    # The real on-disk shape: a session with one true subagent plus journal +
    # nested workflow agents. Only the true subagent should attach.
    now = 1_000_000.0
    parent = Path("/p/proj/sess.jsonl")
    real = Path("/p/proj/sess/subagents/agent-real.jsonl")
    journal = Path("/p/proj/sess/subagents/workflows/wf_1/journal.jsonl")
    wf_agent = Path("/p/proj/sess/subagents/workflows/wf_1/agent-nested.jsonl")
    entries = [
        (parent, now - 10.0),
        (real, now - 2.0),
        (journal, now - 1.0),     # newest, but bookkeeping -> excluded
        (wf_agent, now - 1.0),    # nested workflow agent -> excluded
    ]
    groups = group_sessions(entries, now)
    assert groups == [(parent, [real])]


def test_group_sessions_caps_agents_newest_first():
    now = 1_000_000.0
    parent = Path("/p/proj/sess.jsonl")
    entries = [(parent, now - 100.0)]
    # MAX_AGENTS_PER_SESSION + 3 fresh agents, progressively older by index.
    for i in range(MAX_AGENTS_PER_SESSION + 3):
        entries.append((Path(f"/p/proj/sess/subagents/agent-{i}.jsonl"), now - float(i)))
    _, agents = group_sessions(entries, now)[0]
    assert len(agents) == MAX_AGENTS_PER_SESSION
    # The newest (smallest age) survive, newest-first.
    assert agents == [Path(f"/p/proj/sess/subagents/agent-{i}.jsonl")
                      for i in range(MAX_AGENTS_PER_SESSION)]


def test_group_sessions_synthesizes_orphan_parent():
    # A live agent whose parent .jsonl wasn't scanned still surfaces its session.
    now = 1_000_000.0
    agent = Path("/p/proj/sess/subagents/agent-1.jsonl")
    groups = group_sessions([(agent, now - 2.0)], now)
    assert groups == [(Path("/p/proj/sess.jsonl"), [agent])]


def test_any_agent_active():
    code = AgentState(agent_id="a", activity=Activity.CODING, tool_name=None)
    idle = AgentState(agent_id="b", activity=Activity.IDLE, tool_name=None)
    stale = AgentState(agent_id="c", activity=Activity.CODING, tool_name=None, is_stale=True)
    assert any_agent_active([idle, code]) is True   # one working
    assert any_agent_active([idle, stale]) is False  # all idle/stale
    assert any_agent_active([]) is False


def test_group_sessions_drops_finished_agents():
    now = 1_000_000.0
    parent = Path("/p/proj/sess.jsonl")
    live = Path("/p/proj/sess/subagents/agent-live.jsonl")
    done = Path("/p/proj/sess/subagents/agent-done.jsonl")
    entries = [
        (parent, now - 5.0),
        (live, now - 2.0),
        (done, now - (AGENT_ACTIVE_SECONDS + 5.0)),  # quiet too long -> dropped
    ]
    groups = group_sessions(entries, now)
    assert groups[0][1] == [live]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
