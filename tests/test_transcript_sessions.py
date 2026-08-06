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
import time
from datetime import datetime, timezone
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
    _SessionTail,
    any_agent_active,
    group_sessions,
    is_agent_transcript,
    is_subagent_path,
    parent_transcript_for_subagent,
    fmt_tokens,
    parse_iso_ts,
    project_name_from_cwd,
    select_active,
    session_label,
    sum_token_windows,
    TokenUsage,
)


def test_project_name_from_cwd_uses_cwd_leaf():
    # Build with the running OS separator so Path(cwd).name resolves the leaf on
    # both Windows and POSIX (a backslash literal isn't a separator on Linux).
    cwd = os.sep.join(["C:", "Claude", "ClonedRepos", "Clawdmeter-Windows"])
    assert project_name_from_cwd(cwd, None) == "Clawdmeter-Windows"


def test_project_name_from_cwd_falls_back_to_transcript_parent():
    # cwd missing -> use the transcript's parent directory name.
    tp = Path("/home/u/.claude/projects/C--Some-Project/abc123.jsonl")
    assert project_name_from_cwd(None, tp) == "C--Some-Project"
    assert project_name_from_cwd("", tp) == "C--Some-Project"


def test_project_name_from_cwd_total_fallback():
    assert project_name_from_cwd(None, None) == "unknown"
    assert project_name_from_cwd("", None) == "unknown"


def test_token_usage_work_excludes_cache():
    tu = TokenUsage()
    tu.add_usage({"input_tokens": 100, "output_tokens": 50,
                  "cache_read_input_tokens": 9000,
                  "cache_creation_input_tokens": 200})
    tu.add_usage({"input_tokens": 10, "output_tokens": 5})  # a 2nd turn
    assert (tu.input, tu.output, tu.cache_read, tu.cache_write) == (110, 55, 9000, 200)
    assert tu.work == 165               # input + output only
    assert tu.total == 165 + 9200       # everything


def test_session_tail_accumulates_tokens_across_turns():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    # a text/thinking turn carries usage too and must count (not just tool turns)
    tail._consume_event({
        "type": "assistant", "timestamp": "2026-06-14T03:00:00Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}],
                    "usage": {"input_tokens": 3, "output_tokens": 7}},
    })
    tail._consume_event({
        "type": "assistant", "timestamp": "2026-06-14T03:01:00Z",
        "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read"}],
                    "usage": {"input_tokens": 2, "output_tokens": 1,
                              "cache_read_input_tokens": 500}},
    })
    assert tail.tokens.work == 13        # (3+7) + (2+1)
    assert tail.tokens.cache_read == 500
    snap = tail._state(Activity.READING, "Read")
    assert snap.tokens.work == 13
    # the emitted snapshot is independent of further accumulation
    tail._consume_event({
        "type": "assistant", "timestamp": "2026-06-14T03:02:00Z",
        "message": {"role": "assistant", "content": [{"type": "text"}],
                    "usage": {"input_tokens": 100, "output_tokens": 0}},
    })
    assert snap.tokens.work == 13
    assert tail.tokens.work == 113


def test_fmt_tokens_humanizes():
    assert fmt_tokens(0) == "0"
    assert fmt_tokens(940) == "940"
    assert fmt_tokens(8_100) == "8.1K"
    assert fmt_tokens(914_000) == "914K"      # >=100 drops the decimal
    assert fmt_tokens(19_400_000) == "19.4M"
    assert fmt_tokens(2_170_000_000) == "2.2B"


def test_account_window_tokens_sums_5h_and_7d(tmp_path):
    import json
    from transcript import account_window_tokens, _TOKEN_EVENT_CACHE
    _TOKEN_EVENT_CACHE.clear()
    now = time.time()

    def iso(epoch):
        return datetime.fromtimestamp(epoch, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")

    def ev(ts, inp, out):
        return json.dumps({
            "type": "assistant", "timestamp": iso(ts),
            "message": {"role": "assistant", "content": [{"type": "text"}],
                        "usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": 99999}},
        })

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.jsonl").write_text(
        ev(now - 60, 10, 20) + "\n" + ev(now - 6 * 3600, 5, 5), encoding="utf-8")
    # File freshly written (recent mtime) but its only event is 8 days old:
    # excluded by the per-event window even though the file passes the mtime gate.
    (proj / "b.jsonl").write_text(ev(now - 8 * 24 * 3600, 1000, 1000), encoding="utf-8")

    w5, w7 = account_window_tokens(now, root=tmp_path)
    assert w5 == 30          # only the now-60 event (10+20); cache excluded
    assert w7 == 40          # + the now-6h event (5+5); 8-day-old event excluded


def _usage_line(ts_iso, msg_id, uuid, inp, out, cache_read=0, cache_write=0,
                blocks=None):
    """One assistant JSONL record, shaped like Claude Code writes them.

    Claude Code splits ONE assistant message across several records — one per
    content block — and every record repeats the same `message.id`. Early
    records carry a PARTIAL `output_tokens` (the running total at that point in
    the stream); the last record carries the final figure.
    """
    import json
    return json.dumps({
        "type": "assistant", "timestamp": ts_iso, "uuid": uuid,
        "message": {
            "role": "assistant", "id": msg_id,
            "content": blocks if blocks is not None else [{"type": "text"}],
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": cache_write},
        },
    })


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def test_account_window_tokens_collapses_one_message_across_its_records(tmp_path):
    """One message split across 3 records must be counted ONCE.

    Summing per record multiplies the total by the block count. The final
    output figure is the LARGEST one (output_tokens is a running total), so the
    message is worth input(2) + output(328) = 330, not 344.
    """
    from transcript import account_window_tokens, _TOKEN_EVENT_CACHE
    _TOKEN_EVENT_CACHE.clear()
    now = time.time()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "s.jsonl").write_text("\n".join([
        _usage_line(_iso(now - 60), "msg_A", "u1", 2, 5),      # partial
        _usage_line(_iso(now - 59), "msg_A", "u2", 2, 5),      # partial
        _usage_line(_iso(now - 58), "msg_A", "u3", 2, 328),    # final
    ]), encoding="utf-8")

    w5, w7 = account_window_tokens(now, root=tmp_path)
    assert w5 == 330, "one message counted once, at its final output figure"
    assert w7 == 330


def test_account_window_tokens_ignores_records_replayed_into_another_file(tmp_path):
    """Resuming a session replays the same records into a NEW transcript file.

    Both files live under ~/.claude/projects and both get scanned, so a
    per-file-only dedup still double-counts every replayed message.
    """
    from transcript import account_window_tokens, _TOKEN_EVENT_CACHE
    _TOKEN_EVENT_CACHE.clear()
    now = time.time()
    proj = tmp_path / "proj"
    proj.mkdir()
    records = "\n".join([
        _usage_line(_iso(now - 60), "msg_A", "u1", 2, 5),
        _usage_line(_iso(now - 58), "msg_A", "u2", 2, 328),
        _usage_line(_iso(now - 40), "msg_B", "u3", 7, 11),
    ])
    (proj / "original.jsonl").write_text(records, encoding="utf-8")
    (proj / "resumed.jsonl").write_text(records, encoding="utf-8")   # the replay

    w5, _w7 = account_window_tokens(now, root=tmp_path)
    assert w5 == 330 + 18, "a replayed message is the same message, not a second one"


def test_account_window_tokens_keeps_the_richest_copy_of_a_message(tmp_path):
    """Guards the FIX, not the bug: a replay copy can carry all-zero usage.

    Keeping whichever copy is seen first would zero out real usage whenever the
    zeroed file happens to be walked first, so the collapse must keep the
    largest value per bucket.
    """
    from transcript import account_window_tokens, _TOKEN_EVENT_CACHE
    _TOKEN_EVENT_CACHE.clear()
    now = time.time()
    proj = tmp_path / "proj"
    proj.mkdir()
    # "a" sorts before "b": the zeroed copy is walked first.
    (proj / "a-zeroed.jsonl").write_text(
        _usage_line(_iso(now - 60), "msg_A", "u1", 0, 0), encoding="utf-8")
    (proj / "b-real.jsonl").write_text(
        _usage_line(_iso(now - 60), "msg_A", "u1", 2, 667), encoding="utf-8")

    w5, _w7 = account_window_tokens(now, root=tmp_path)
    assert w5 == 669, "the real numbers must survive, whatever the walk order"


def test_session_tail_collapses_one_message_across_its_records():
    """Per-session shelf total: same collapse, but streaming record by record."""
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    for uuid, out in (("u1", 5), ("u2", 5), ("u3", 328)):
        tail._consume_event({
            "type": "assistant", "timestamp": "2026-06-14T03:00:00Z", "uuid": uuid,
            "message": {"role": "assistant", "id": "msg_A",
                        "content": [{"type": "text"}],
                        "usage": {"input_tokens": 2, "output_tokens": out,
                                  "cache_read_input_tokens": 500}},
        })
    assert tail.tokens.work == 330      # 2 + 328, not (2+5)+(2+5)+(2+328)
    assert tail.tokens.cache_read == 500  # cache counted once, not 3x


def test_session_tail_clears_dedup_state_when_the_file_is_truncated(tmp_path):
    """A rotated/truncated transcript is re-read from the top.

    `poll` resets the additive tally on truncation; the dedup bookkeeping must
    reset with it, or every re-read record looks like an already-counted
    duplicate and the tally stays stuck at zero.
    """
    f = tmp_path / "sess.jsonl"
    f.write_text("\n".join([
        _usage_line("2026-06-14T03:00:00Z", "msg_A", "u1", 2, 328),
        _usage_line("2026-06-14T03:01:00Z", "msg_B", "u2", 7, 11),
    ]) + "\n", encoding="utf-8")
    tail = _SessionTail(f)
    tail.poll(time.time())
    assert tail.tokens.work == 330 + 18

    # Rotated: shorter file, msg_A only -> triggers the truncation reset.
    f.write_text(
        _usage_line("2026-06-14T03:00:00Z", "msg_A", "u1", 2, 328) + "\n",
        encoding="utf-8")
    tail.poll(time.time())
    assert tail.tokens.work == 330, "re-read after truncation must re-count msg_A"


def test_sum_token_windows_respects_5h_and_7d():
    now = 1_000_000.0
    events = [
        (now - 60, 10),             # in 5h and 7d
        (now - 4 * 3600, 20),       # in 5h and 7d
        (now - 6 * 3600, 30),       # in 7d only (older than 5h)
        (now - 8 * 24 * 3600, 40),  # older than 7d -> excluded
        (None, 99),                 # no timestamp -> excluded
    ]
    w5, w7 = sum_token_windows(events, now)
    assert w5 == 30                 # 10 + 20
    assert w7 == 60                 # 10 + 20 + 30


def test_parse_iso_ts_handles_z_and_fractions():
    expected = datetime(2026, 6, 14, 3, 26, 31, 977000, tzinfo=timezone.utc).timestamp()
    assert parse_iso_ts("2026-06-14T03:26:31.977Z") == expected
    # no fractional seconds
    assert parse_iso_ts("2026-06-14T03:26:31Z") == datetime(
        2026, 6, 14, 3, 26, 31, tzinfo=timezone.utc
    ).timestamp()
    # explicit offset (not Z)
    assert parse_iso_ts("2026-06-14T03:26:31+00:00") == expected - 0.977
    # absent / garbage -> None so callers fall back to wall-clock
    assert parse_iso_ts(None) is None
    assert parse_iso_ts("") is None
    assert parse_iso_ts("not-a-timestamp") is None


def test_last_active_uses_event_timestamp_not_wall_clock():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    ts = "2026-06-14T03:26:31.977Z"
    tail._consume_event({
        "type": "assistant",
        "timestamp": ts,
        "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": "Read"}]},
    })
    # The displayed "last active" anchors to the event's own time, so reading
    # an old backlog at startup doesn't read as "just now".
    assert tail.last_event_ts == parse_iso_ts(ts)


def test_last_active_falls_back_to_wall_clock_without_timestamp():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    before = time.time()
    tail._consume_event({
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash"}]},
    })
    assert tail.last_event_ts is not None
    assert tail.last_event_ts >= before


def test_session_label_prefers_custom_then_ai_then_cwd():
    # OS-native separator so the cwd leaf resolves on Windows and POSIX alike.
    cwd = os.sep.join(["C:", "Claude", "ClonedRepos", "Clawdmeter-Windows"])
    # custom title wins over everything
    assert session_label("My Tab", "Auto Title", cwd, None) == "My Tab"
    # no custom -> auto (ai) title
    assert session_label(None, "Auto Title", cwd, None) == "Auto Title"
    # neither title -> cwd leaf
    assert session_label(None, None, cwd, None) == "Clawdmeter-Windows"
    # blank/whitespace titles are ignored and fall through
    assert session_label("   ", "", cwd, None) == "Clawdmeter-Windows"
    # surrounding whitespace is trimmed
    assert session_label("  Spaced  ", None, None, None) == "Spaced"
    # total fallback
    assert session_label(None, None, None, None) == "unknown"


def test_session_tail_captures_titles_and_uses_them_as_label():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    # auto title arrives first; with no custom title it becomes the label.
    tail._consume_event({"type": "ai-title", "aiTitle": "Auto X", "sessionId": "s"})
    assert tail.ai_title == "Auto X"
    assert tail.custom_title is None
    assert tail._state(Activity.IDLE, None, is_stale=True).project_name == "Auto X"

    # a custom title overrides the auto one.
    tail._consume_event({"type": "custom-title", "customTitle": "Mine", "sessionId": "s"})
    assert tail.custom_title == "Mine"
    assert tail._state(Activity.IDLE, None, is_stale=True).project_name == "Mine"

    # clearing the custom title falls back to the auto title.
    tail._consume_event({"type": "custom-title", "customTitle": "", "sessionId": "s"})
    assert tail.custom_title is None
    assert tail._state(Activity.IDLE, None, is_stale=True).project_name == "Auto X"


def test_session_tail_falls_back_to_cwd_leaf_without_titles():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    tail._consume_event({
        "type": "user",
        # OS-native separator so Path(cwd).name yields the leaf on either platform.
        "cwd": os.sep.join(["C:", "Work", "my-repo"]),
        "message": {"role": "user", "content": "hi"},
    })
    assert tail._state(Activity.IDLE, None, is_stale=True).project_name == "my-repo"


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
    activity, tool, target = _classify(content)
    assert activity is Activity.INTEGRATING
    assert tool == "github/get_pr"


def test_classify_unknown_tool_is_coding():
    activity, tool, target = _classify([{"type": "tool_use", "name": "SomeNewTool"}])
    assert activity is Activity.CODING
    assert tool == "SomeNewTool"


def test_classify_thinking_or_text_is_thinking():
    assert _classify([{"type": "thinking"}])[0] is Activity.THINKING
    assert _classify([{"type": "text", "text": "hi"}])[0] is Activity.THINKING


def test_classify_empty_is_idle():
    activity, tool, target = _classify([])
    assert activity is Activity.IDLE
    assert tool is None
    assert target is None


def test_classify_last_tool_use_wins():
    # Both blocks present: the last tool_use should win over earlier ones/text.
    content = [
        {"type": "text", "text": "thinking out loud"},
        {"type": "tool_use", "name": "Read"},
        {"type": "tool_use", "name": "Bash"},
    ]
    activity, tool, target = _classify(content)
    assert activity is Activity.CODING
    assert tool == "Bash"


def test_classify_extracts_target_from_tool_input():
    # OS-native separator so Path(file_path).name yields the leaf on either platform.
    a, tool, target = _classify(
        [{"type": "tool_use", "name": "Read",
          "input": {"file_path": os.sep.join(["C:", "x", "thisisatest.txt"])}}])
    assert (a, tool, target) == (Activity.READING, "Read", "thisisatest.txt")
    assert _classify([{"type": "tool_use", "name": "Grep",
                       "input": {"pattern": "def foo"}}])[2] == "def foo"
    assert _classify([{"type": "tool_use", "name": "Bash",
                       "input": {"command": "npm run build"}}])[2] == "npm"
    assert _classify([{"type": "tool_use", "name": "WebFetch",
                       "input": {"url": "https://example.com/page"}}])[2] == "example.com"
    # the winning (last) tool_use supplies the target
    assert _classify([
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/first.py"}},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/b/second.py"}},
    ])[2] == "second.py"
    # no natural target -> None (display falls back to the tool name)
    assert _classify([{"type": "tool_use", "name": "TodoWrite",
                       "input": {"todos": []}}])[2] is None


def test_tool_target_covers_more_tools():
    def tgt(name, inp):
        return _classify([{"type": "tool_use", "name": name, "input": inp}])[2]
    assert tgt("Write", {"file_path": "/a/b/out.json"}) == "out.json"
    assert tgt("MultiEdit", {"file_path": "/a/b/c.py"}) == "c.py"
    assert tgt("NotebookEdit", {"notebook_path": "/n/book.ipynb"}) == "book.ipynb"
    assert tgt("NotebookEdit", {"file_path": "/n/fallback.ipynb"}) == "fallback.ipynb"
    assert tgt("Glob", {"pattern": "**/*.ts"}) == "**/*.ts"
    assert tgt("PowerShell", {"command": "Get-ChildItem -Recurse"}) == "Get-ChildItem"
    assert tgt("WebSearch", {"query": "qt elide label"}) == "qt elide label"
    assert tgt("Task", {"description": "scan logs", "subagent_type": "x"}) == "scan logs"
    assert tgt("Task", {"subagent_type": "Explore"}) == "Explore"   # description fallback
    assert tgt("Read", {}) is None                                   # missing field -> None
    assert tgt("Bash", {"command": "   "}) is None                   # blank -> None


def test_session_tail_target_in_state():
    tail = _SessionTail(Path("/p/proj/sess.jsonl"))
    tail._consume_event({
        "type": "assistant", "timestamp": "2026-06-14T03:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Edit",
             "input": {"file_path": "/proj/src/dashboard.py"}}]},
    })
    st = tail._state(Activity.CODING, "Edit")
    assert st.target == "dashboard.py"
    # idle state carries no target
    assert tail._state(Activity.IDLE, None, is_stale=True).target is None


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
