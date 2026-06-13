"""Watch Claude Code's local JSONL transcripts to drive the mascots.

Claude Code writes an append-only transcript per session at
    ~/.claude/projects/<project-slug>/<session-uuid>.jsonl
Each line is a JSON object representing one event. Assistant events have
`message.content` as a list of blocks; the interesting ones are:

  * `tool_use`  - {"type":"tool_use","name":"Bash"|"Edit"|...}
  * `thinking`  - model is reasoning
  * `text`      - assistant prose

By tailing each recently-touched transcript and looking at the latest block,
we know in near-real-time what every concurrent Claude Code session is doing.
Schema is undocumented so we parse defensively (unknown blocks => fall through
to "thinking").

Sessions whose transcript hasn't been appended to within ACTIVE_WINDOW_SECONDS
drop off the shelf entirely; those still on the shelf but quiet for more than
STALE_SECONDS are reported IDLE/stale so the dashboard can dim them.
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
# A transcript stays on the shelf only while its file was touched this recently.
# Beyond this window the session is considered finished and its tile vanishes.
ACTIVE_WINDOW_SECONDS = 600
# Bounds the number of live tails (timers + window width); overflow scrolls.
MAX_SESSIONS = 6
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

# Activity -> glow color shown behind the mascot tile (and used for its activity
# label). Anchored to the app palette (#CE7D6B / #0e1116); IDLE is a dim grey so
# stale tiles read as "no real glow".
ACTIVITY_COLORS: dict[Activity, str] = {
    Activity.CODING:      "#CE7D6B",  # brand warm — matches mockup's active glow
    Activity.READING:     "#5FB3A1",  # teal
    Activity.SEARCHING:   "#8B7DD8",  # violet
    Activity.PLANNING:    "#E0A458",  # amber
    Activity.INTEGRATING: "#C77DBB",  # magenta
    Activity.THINKING:    "#5B8DEF",  # cool blue — matches mockup's middle mascot
    Activity.IDLE:        "#3a3f4b",  # dim grey — no real glow
}


@dataclass
class TranscriptState:
    activity: Activity
    tool_name: str | None       # raw tool name when CODING/READING/etc
    transcript_path: Path | None
    last_event_ts: float | None
    session_id: str | None = None      # transcript file stem (the uuid) — stable tile key
    cwd: str | None = None
    project_name: str | None = None    # friendly name shown on the tile
    is_stale: bool = False             # in the shelf window but quiet > STALE_SECONDS


def is_subagent_path(p: Path) -> bool:
    """True for subagent transcripts (…/<uuid>/subagents/agent-*.jsonl).

    Subagents are not user-facing sessions — they fold into their parent — so
    discovery must skip any path that has a `subagents` component.
    """
    return "subagents" in p.parts


def project_name_from_cwd(cwd: str | None, transcript_path: Path | None) -> str:
    """Best-effort friendly name for a tile.

    Prefer the working directory's leaf (`Path(cwd).name`) since `cwd` rides the
    event stream verbatim and sidesteps the ambiguous folder slug. Fall back to
    the transcript's parent directory name, then to "unknown".
    """
    if cwd:
        name = Path(cwd).name
        if name:
            return name
    if transcript_path is not None:
        parent = transcript_path.parent.name
        if parent:
            return parent
    return "unknown"


def select_active(
    entries: list[tuple[Path, float]],
    now: float,
    window: float = ACTIVE_WINDOW_SECONDS,
    limit: int = MAX_SESSIONS,
) -> list[Path]:
    """Pick the paths that belong on the shelf, newest-first.

    `entries` is a list of (path, mtime) already gathered by the caller (pure:
    no stat/rglob in here). Drops subagent paths, keeps only files touched within
    `window`, sorts newest-mtime first, and caps to `limit`.
    """
    kept: list[tuple[float, Path]] = []
    for path, mtime in entries:
        if is_subagent_path(path):
            continue
        if now - mtime <= window:
            kept.append((mtime, path))
    kept.sort(key=lambda t: t[0], reverse=True)
    return [path for _, path in kept[:limit]]


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


class _SessionTail:
    """Owns the byte-tailing state + last-known activity for ONE transcript file.

    Keying this per path lets several files tail independently without sharing
    offsets. The tailing logic (offset tracking, partial-last-line hold-back,
    defensive json parsing) is the original single-watcher behaviour, unchanged.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.last_activity: Activity | None = None
        self.last_tool: str | None = None
        self.last_event_ts: float | None = None
        self.cwd: str | None = None

    def poll(self, now: float) -> TranscriptState:
        """Read any newly-appended bytes and return this session's current state."""
        path = self.path
        try:
            # One stat() so size and mtime describe the same file state — two
            # calls can straddle an append/rotation and disagree.
            st = path.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            # File momentarily unavailable — report what we last knew.
            return self._state(Activity.IDLE, None, is_stale=True)

        if size < self.offset:
            # Truncated/rotated under us; restart from the top.
            self.offset = 0
        if size > self.offset:
            self._read_new(size)

        fresh = self.last_event_ts is not None and (now - mtime) <= STALE_SECONDS
        if fresh:
            return self._state(self.last_activity or Activity.THINKING, self.last_tool)
        # On the shelf (caller filtered by ACTIVE_WINDOW_SECONDS) but quiet: dim it.
        return self._state(Activity.IDLE, None, is_stale=True)

    def _read_new(self, size: int) -> None:
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read(size - self.offset)
        except OSError:
            return

        # If the last byte isn't a newline, that line is still being written.
        # Hold the partial bytes back until the next poll.
        if chunk and not chunk.endswith(b"\n"):
            last_nl = chunk.rfind(b"\n")
            if last_nl < 0:
                return  # incomplete; don't advance offset
            self.offset += last_nl + 1
            chunk = chunk[: last_nl + 1]
        else:
            self.offset = size

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
        # cwd rides BOTH user and assistant events, so capture it from any event
        # — the tile can be named before the model's first reply.
        cwd = ev.get("cwd")
        if isinstance(cwd, str) and cwd:
            self.cwd = cwd

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
        self.last_activity = activity
        self.last_tool = tool
        self.last_event_ts = time.time()

    def _state(
        self, activity: Activity, tool: str | None, is_stale: bool = False
    ) -> TranscriptState:
        return TranscriptState(
            activity=activity,
            tool_name=tool if activity != Activity.IDLE else None,
            transcript_path=self.path,
            last_event_ts=self.last_event_ts,
            session_id=self.path.stem,
            cwd=self.cwd,
            project_name=project_name_from_cwd(self.cwd, self.path),
            is_stale=is_stale,
        )


def _idle_state() -> TranscriptState:
    """The empty-shelf state — lets the dashboard fall back to rate-based mood."""
    return TranscriptState(
        activity=Activity.IDLE,
        tool_name=None,
        transcript_path=None,
        last_event_ts=None,
    )


class TranscriptWatcher(QObject):
    """Tails every recently-active Claude Code transcript and emits their states.

    Emits the full shelf via `sessions_changed`; `state_changed` keeps the old
    single-mascot contract by carrying the focused (newest) session.
    """

    sessions_changed = Signal(object)  # list[TranscriptState], newest-first
    state_changed = Signal(object)     # BACK-COMPAT: focused state, or IDLE when empty

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._tails: dict[Path, _SessionTail] = {}
        # Discovery order (newest-first) decides which tail is "focused".
        self._order: list[Path] = []
        self._poll_count = 0

    def start(self) -> None:
        self._rescan()
        self._poll()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _scan_entries(self) -> list[tuple[Path, float]]:
        """Gather (path, mtime) for every transcript on disk (the I/O half)."""
        if not TRANSCRIPTS_DIR.exists():
            return []
        entries: list[tuple[Path, float]] = []
        for p in TRANSCRIPTS_DIR.rglob("*.jsonl"):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            entries.append((p, m))
        return entries

    def _rescan(self) -> None:
        """Refresh the set of tailed files: add new ones, drop those that left."""
        active = select_active(self._scan_entries(), time.time())
        self._order = active
        active_set = set(active)
        # Drop tails whose file fell out of the window so their offsets are freed.
        for path in list(self._tails):
            if path not in active_set:
                del self._tails[path]
        for path in active:
            if path not in self._tails:
                self._tails[path] = _SessionTail(path)

    def _poll(self) -> None:
        self._poll_count += 1
        if self._poll_count % RESCAN_DIR_EVERY_N_POLLS == 0:
            self._rescan()

        now = time.time()
        states: list[TranscriptState] = []
        for path in self._order:
            tail = self._tails.get(path)
            if tail is None:
                continue
            states.append(tail.poll(now))

        self.sessions_changed.emit(states)
        self.state_changed.emit(states[0] if states else _idle_state())
