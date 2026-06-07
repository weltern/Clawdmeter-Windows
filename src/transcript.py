"""Watch Claude Code's local JSONL transcripts to drive the sprite.

Claude Code writes an append-only transcript per session at
    ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
Each line is a JSON object representing one event. Assistant events have
`message.content` as a list of blocks; the interesting ones are:

  * `tool_use`  - {"type":"tool_use","name":"Bash"|"Edit"|...}
  * `thinking`  - model is reasoning
  * `text`      - assistant prose

By tailing the newest transcript and looking at the latest block, we know
in near-real-time what Claude Code is doing. Schema is undocumented so we
parse defensively (unknown blocks => fall through to "thinking").

When the newest transcript hasn't been appended to for STALE_SECONDS, we
report IDLE so the dashboard can fall back to its rate-based logic.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"
POLL_MS = 500
STALE_SECONDS = 90
RESCAN_DIR_EVERY_N_POLLS = 10


class Activity(Enum):
    IDLE = "idle"               # transcript stale / not found -> rate-based fallback
    CODING = "coding"           # tool that writes/runs (Bash, Edit, Write, ...)
    READING = "reading"         # tool that observes (Read, Grep, Glob, ...)
    SEARCHING = "searching"     # web tools
    PLANNING = "planning"       # task/todo/sub-agent management
    INTEGRATING = "integrating" # MCP server tool (mcp__<server>__<tool>)
    THINKING = "thinking"       # text/thinking block, no tool


# Prefix that Claude Code gives every MCP server tool: mcp__<server>__<tool>.
MCP_TOOL_PREFIX = "mcp__"


# Tool name -> activity. Names are matched case-insensitively. Unknown tools
# fall through to CODING because most undocumented tools do *something*.
TOOL_MAP: dict[str, Activity] = {
    "bash": Activity.CODING,
    "powershell": Activity.CODING,
    "bashoutput": Activity.CODING,
    "edit": Activity.CODING,
    "write": Activity.CODING,
    "multiedit": Activity.CODING,
    "notebookedit": Activity.CODING,
    "killshell": Activity.CODING,
    "senduserfile": Activity.CODING,

    "read": Activity.READING,
    "glob": Activity.READING,
    "grep": Activity.READING,
    "notebookread": Activity.READING,
    "toolsearch": Activity.READING,

    "webfetch": Activity.SEARCHING,
    "websearch": Activity.SEARCHING,

    "taskcreate": Activity.PLANNING,
    "taskupdate": Activity.PLANNING,
    "tasklist": Activity.PLANNING,
    "taskget": Activity.PLANNING,
    "taskoutput": Activity.PLANNING,
    "taskstop": Activity.PLANNING,
    "todowrite": Activity.PLANNING,
    "agent": Activity.PLANNING,
    "task": Activity.PLANNING,
    "sendmessage": Activity.PLANNING,
    "exitplanmode": Activity.PLANNING,
    "askuserquestion": Activity.PLANNING,
    "skill": Activity.PLANNING,
    "slashcommand": Activity.PLANNING,
}


def _activity_for_tool(name: str) -> Activity:
    """Map a raw tool name to an Activity.

    MCP server tools are recognised by their `mcp__` prefix before consulting
    the static map; unknown tools fall through to CODING.
    """
    lower = name.lower()
    if lower.startswith(MCP_TOOL_PREFIX):
        return Activity.INTEGRATING
    return TOOL_MAP.get(lower, Activity.CODING)


def _pretty_tool_name(name: str) -> str:
    """Shorten an MCP tool name for the label: mcp__github__get_pr -> github/get_pr."""
    if name.lower().startswith(MCP_TOOL_PREFIX):
        parts = name.split("__")
        if len(parts) >= 3:
            return f"{parts[1]}/{'__'.join(parts[2:])}"
        if len(parts) == 2 and parts[1]:
            return parts[1]
    return name


# Activity -> animation names (must exist in assets/sprites/manifest.json).
ACTIVITY_ANIMS: dict[Activity, list[str]] = {
    Activity.CODING:      ["work coding"],
    Activity.READING:     ["work think"],
    Activity.SEARCHING:   ["idle look around"],
    Activity.PLANNING:    ["idle blink", "idle look around"],
    Activity.INTEGRATING: ["expression surprise", "idle look around"],
    Activity.THINKING:    ["work think"],
    # IDLE has no entry — dashboard falls back to rate-based group.
}

ACTIVITY_LABELS: dict[Activity, str] = {
    Activity.CODING:      "CODING",
    Activity.READING:     "READING",
    Activity.SEARCHING:   "SEARCHING",
    Activity.PLANNING:    "PLANNING",
    Activity.INTEGRATING: "INTEGRATING",
    Activity.THINKING:    "THINKING",
    Activity.IDLE:        "IDLE",
}


@dataclass
class TranscriptState:
    activity: Activity
    tool_name: str | None       # raw tool name when CODING/READING/etc
    transcript_path: Path | None
    last_event_ts: float | None


def _newest_transcript() -> Path | None:
    if not TRANSCRIPTS_DIR.exists():
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for p in TRANSCRIPTS_DIR.rglob("*.jsonl"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > newest_mtime:
            newest_mtime = m
            newest = p
    return newest


def _classify(content: list) -> tuple[Activity, str | None]:
    """Return (activity, tool_name) for the latest assistant content blocks.

    Walks the blocks back-to-front; the last tool_use wins. If there are no
    tool_use blocks but there are thinking/text blocks, returns THINKING.
    """
    last_tool: str | None = None
    has_thinking_or_text = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                last_tool = name
        elif btype in ("thinking", "text"):
            has_thinking_or_text = True
    if last_tool is not None:
        return _activity_for_tool(last_tool), _pretty_tool_name(last_tool)
    if has_thinking_or_text:
        return Activity.THINKING, None
    return Activity.IDLE, None


class TranscriptWatcher(QObject):
    """Tails Claude Code's newest JSONL and emits the latest activity."""

    state_changed = Signal(object)  # TranscriptState

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._path: Path | None = None
        self._offset: int = 0
        self._last_activity: Activity | None = None
        self._last_tool: str | None = None
        self._last_event_ts: float | None = None
        self._poll_count = 0

    def start(self) -> None:
        self._rescan()
        self._poll()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _rescan(self) -> None:
        newest = _newest_transcript()
        if newest != self._path:
            self._path = newest
            self._offset = 0
            self._last_event_ts = None

    def _poll(self) -> None:
        self._poll_count += 1
        if self._poll_count % RESCAN_DIR_EVERY_N_POLLS == 0:
            self._rescan()

        path = self._path
        if path is None or not path.exists():
            self._emit(Activity.IDLE, None)
            return

        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            return

        if size > self._offset:
            self._read_new(path, size)

        if self._last_event_ts is None or (time.time() - mtime) > STALE_SECONDS:
            self._emit(Activity.IDLE, None)
        else:
            self._emit(self._last_activity or Activity.THINKING, self._last_tool)

    def _read_new(self, path: Path, size: int) -> None:
        try:
            with path.open("rb") as f:
                f.seek(self._offset)
                chunk = f.read(size - self._offset)
        except OSError:
            return

        # If the last byte isn't a newline, that line is still being written.
        # Hold the partial bytes back until the next poll.
        if chunk and not chunk.endswith(b"\n"):
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                return  # incomplete; don't advance offset
            self._offset += last_nl + 1
            chunk = chunk[: last_nl + 1]
        else:
            self._offset = size

        text = chunk.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._consume_event(ev)

    def _consume_event(self, ev: dict) -> None:
        msg = ev.get("message")
        if not isinstance(msg, dict):
            return
        if msg.get("role") != "assistant":
            return
        content = msg.get("content")
        if not isinstance(content, list):
            return
        activity, tool = _classify(content)
        if activity == Activity.IDLE:
            return
        self._last_activity = activity
        self._last_tool = tool
        self._last_event_ts = time.time()

    def _emit(self, activity: Activity, tool: str | None) -> None:
        state = TranscriptState(
            activity=activity,
            tool_name=tool if activity != Activity.IDLE else None,
            transcript_path=self._path,
            last_event_ts=self._last_event_ts,
        )
        self.state_changed.emit(state)
