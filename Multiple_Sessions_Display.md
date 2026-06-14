# Clawdmeter — Multi-Session View Exploration

> Working notes + mockups for making Clawdmeter show **multiple Claude Code
> sessions** instead of a single one. Hand this file back to a future session
> to pick up where we left off.

- **Repo:** `weltern/clawdmeter-windows`
- **Branch:** `claude/variant-c-mascot-shelf` (implementation) — off `develop`
- **Status:** **Variant C implemented + verified** on the branch above (43 tests pass,
  offscreen import/widget/dashboard smokes pass). Reviewed by a 3-way adversarial
  panel; all blocker/high/medium findings fixed (see §8.10). **Pending: a real-display
  visual pass** (`.\.venv\Scripts\python src\main.py --mock`) and commit/PR approval.
  Earlier research/mockups branch was `claude/multiple-sessions-review-CydBE`.

---

## 1. The question

> Can Clawdmeter be made to show multiple sessions rather than only a single
> session? Is it possible, how (high level), and what would the user see?

**Answer: Yes — but with one important caveat about what "session" means.**

---

## 2. Key finding — "session" means two different things

Clawdmeter (Python + PySide6/Qt) pulls from **two independent sources**:

| Source | File | What it is | Per-session? |
|---|---|---|---|
| **Usage %** (SESSION 5h / WEEKLY 7d bars) | `src/poller.py` | Anthropic API rate-limit headers, polled every 60s via OAuth token | **No — account-wide.** One number for the whole account, regardless of how many Claude Code windows are open |
| **Live activity** (mascot + CODING/READING/THINKING…) | `src/transcript.py` | Local Claude Code transcript files at `~/.claude/projects/<project>/<session-uuid>.jsonl` | **Yes — each `.jsonl` is one real conversation/session** |

So the only layer that can *truly* go multi-session is the **activity/mascot**
layer. The usage bars stay a single shared readout — the API can't split them
per conversation.

---

## 3. Where the single-session limit lives

`src/transcript.py` deliberately tracks only the **single most-recently-modified**
transcript:

```python
def _newest_transcript() -> Path | None:        # ~lines 142–155
    ...
    for p in TRANSCRIPTS_DIR.rglob("*.jsonl"):
        if m > newest_mtime:                     # keeps only THE newest
            newest = p
    return newest                                # returns ONE Path
```

- `TranscriptWatcher` stores it as a single `self._path: Path | None`.
- `Dashboard` holds a single `self._transcript_state`.
- Every UI widget (sprite, activity label, bars) is bound to that one object.
- There is no collection, no loop, no session picker anywhere.

**Practical effect today:** run two sessions at once and the mascot/activity
*flickers* between them — it always follows whichever session wrote to disk last.

---

## 4. How multi-session could work (high level)

1. **Discover all sessions** — change `_newest_transcript()` to return a *list*
   of recently-modified `.jsonl` files (e.g. active in the last N minutes).
2. **Watch them concurrently** — extend `TranscriptWatcher` to tail multiple
   files, keying state/offset by path (`dict[Path, TranscriptState]`).
3. **Hold a session collection** in `Dashboard` and emit per-session updates.
4. **Add UI** to surface them (see variants below).
5. **Leave the usage bars as-is** — shared/account-wide, shown once.

Effort: **moderate.** Mostly `transcript.py` + new UI widgets in `dashboard.py`.

**What the user would see:** the quota bars look the same, but instead of one
mascot jumping between sessions, multiple mascots/status indicators appear —
each labeled by project, animating independently. Glanceable "session A is
CODING, session B is THINKING."

---

## 5. Mockups

All faithful to the real app (colors `#0e1116`/`#CE7D6B`, actual mascot sprites,
existing window chrome). HTML sources + PNGs live in `mockups/`.

### Baseline proposal — `multi_session_mockup.png`
Shared `ACCOUNT QUOTA` panel on top; scrollable list of session cards below
(mini mascot + project + `CODING — Edit` + live/idle dot).

![baseline](multi_session_mockup.png)

### Variant A — Hero + switcher — `variant_a_hero_switcher.png`
Keeps today's big animated hero mascot for the *focused* session + full quota
bars; row of clickable chips at the bottom switches the hero.
**Lowest-risk / smallest diff. Preserves current personality.**
Downside: only one mascot animates at a time.

![A](variant_a_hero_switcher.png)

### Variant B — Grid — `variant_b_grid.png`
Equal tile per session, each with its own full-size mascot + activity; quota
collapses to a slim strip on top.
**Best "wow" — all mascots animate in parallel.** Scales nicely to 4–6.

![B](variant_b_grid.png)

### Variant C — Mascot shelf / lineup — `variant_c_shelf.png`
Mascots stand in a row with a colored activity glow, names underneath.
**Most personality / "ambient desktop pet."** Gets cramped past ~4 sessions.

![C](variant_c_shelf.png)

### Variant D — Compact / tray strip — `variant_d_compact.png`
Dense one-line rows, tiny mascots, quota reduced to `5h 9% | 7d 6%`.
**Natural evolution of the existing compact widget.** Scales to many; least playful.

![D](variant_d_compact.png)

---

## 6. Recommendation

- **B** = strongest demo of the core idea (parallel mascots).
- **A** = safest to actually ship (smallest architectural change).
- **C / D** = great as *alternate modes*, not the default (C for fun, D for the
  compact widget).
- Not mutually exclusive: **A or B as the main window + D as the compact mode**
  covers everything.

---

## 7. Open follow-ups (not yet done)

- [ ] Combine ideas (e.g. B main + D compact)?
- [ ] Animate a variant into a GIF to see the mascots actually move?
- [x] Decide idle behavior — auto-drop stale sessions after N min, or keep listed?
      → resolved in §8.7 (two-tier window: dim at 90s, drop at 10m).
- [x] Turn the chosen direction into a concrete implementation plan.
      → **Variant C** chosen. Full plan in §8 below.

---

## 8. Implementation plan — Variant C (Mascot Shelf / Lineup)

> Front-runner chosen. This section is the concrete, code-level plan. Grounded
> in the real source as of this branch: `src/transcript.py`, `src/dashboard.py`,
> `src/sprite_player.py`, `src/mood.py`.

### 8.0 Verified ground truth (re-confirmed against the code + live transcripts)

- **`cwd` is in every `user`/`assistant` transcript event** and holds the real
  working directory (e.g. `C:\Claude`). So the friendly project name is just
  `Path(cwd).name` — **no fragile slug-parsing needed.** (The folder slug
  `C--Claude` is ambiguous because `-` is both the separator *and* legal inside
  project names like `Clawdmeter-Windows`; `cwd` sidesteps that entirely.)
- **Subagent transcripts** live at
  `…/<project>/<session-uuid>/subagents/agent-*.jsonl`. Today's
  `rglob("*.jsonl")` would wrongly surface each subagent as its own "session."
  Discovery must **exclude any path with a `subagents` component**.
- **Multiple `SpritePlayer`s already coexist safely** — the compact widget and
  the reset toast each own one alongside the 240px hero. Parallel-animating
  mascots is therefore proven; no shared-state hazard. Each `SpritePlayer` owns
  its own `QTimer`, and `set_anims(key, names)` **no-ops on an unchanged key** —
  the diffing strategy below leans on that to avoid restarting animations.
- The dashboard's transcript→UI path is small and localized: `_on_transcript`
  (`dashboard.py:1784`) → `_update_sprite_selection` (`:1793`), with
  transcript activity taking precedence over the rate-based mood fallback.

### 8.1 Scope

- **Multiplied:** the mascot/activity layer (one tile per live Claude Code
  session) — `transcript.py` + a new shelf widget in `dashboard.py`.
- **Unchanged / shown once:** the SESSION 5h + WEEKLY 7d quota bars
  (account-wide; the API can't split them per conversation — see §2).
- **Compact mode:** stays a single (focused) mascot for now. Full multi-session
  compact is Variant D, tracked separately.

### 8.2 Data model (`transcript.py`)

Extend the existing dataclass rather than replace it (keeps the single-session
path working during incremental rollout — new fields default to `None`):

```python
@dataclass
class TranscriptState:
    activity: Activity
    tool_name: str | None
    transcript_path: Path | None
    last_event_ts: float | None
    # NEW:
    session_id: str | None = None     # transcript stem (uuid) — stable tile key
    cwd: str | None = None            # from the event stream
    project_name: str | None = None   # Path(cwd).name, slug fallback
    is_stale: bool = False            # within shelf window but quiet >STALE_SECONDS
```

New constants:

```python
ACTIVE_WINDOW_SECONDS = 600   # appears on the shelf at all (mtime within 10 min)
STALE_SECONDS = 90            # within window but quiet -> shown dim/IDLE (existing)
MAX_SESSIONS = 6              # bounds timers + width; overflow scrolls
```

New activity→glow color map (drives the tile glow + label color; final hexes are
a design tweak, but anchored to the existing palette `#CE7D6B` / `#0e1116`):

```python
ACTIVITY_COLORS: dict[Activity, str] = {
    Activity.CODING:      "#CE7D6B",  # brand warm — matches mockup's active glow
    Activity.READING:     "#5FB3A1",  # teal
    Activity.SEARCHING:   "#8B7DD8",  # violet
    Activity.PLANNING:    "#E0A458",  # amber
    Activity.INTEGRATING: "#C77DBB",  # magenta
    Activity.THINKING:    "#5B8DEF",  # cool blue — matches mockup's middle mascot
    Activity.IDLE:        "#3a3f4b",  # dim grey — no real glow
}
```

### 8.3 Refactor `TranscriptWatcher` into a multi-file manager

Pull the per-file tailing state out of the watcher into a small helper, then let
the watcher own a dict of them. The byte-tailing logic (`_read_new`, partial-line
hold-back, `_classify`) moves wholesale into the helper — it's already correct,
it just needs to run once per path.

```python
class _SessionTail:
    """Owns offset + last-known activity for ONE transcript file."""
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.last_activity: Activity | None = None
        self.last_tool: str | None = None
        self.last_event_ts: float | None = None
        self.cwd: str | None = None
    def poll(self) -> TranscriptState: ...      # reads new bytes, returns state
    def _consume_event(self, ev): ...           # also captures cwd from ANY role
```

`_consume_event` change: capture `cwd` whenever present (it rides `user` events
too, not just `assistant`), so a tile is named even before the model's first
reply:

```python
cwd = ev.get("cwd")
if isinstance(cwd, str) and cwd:
    self.cwd = cwd
# ...existing assistant-only activity classification continues below...
```

Watcher becomes a manager:

```python
class TranscriptWatcher(QObject):
    sessions_changed = Signal(object)   # list[TranscriptState], newest-first
    state_changed    = Signal(object)   # BACK-COMPAT: the focused (newest) state

    def _active_transcripts(self) -> list[Path]:
        now = time.time()
        out = []
        for p in TRANSCRIPTS_DIR.rglob("*.jsonl"):
            if "subagents" in p.parts:           # fold subagents into parent
                continue
            try: m = p.stat().st_mtime
            except OSError: continue
            if now - m <= ACTIVE_WINDOW_SECONDS:
                out.append((m, p))
        out.sort(reverse=True)                    # newest first
        return [p for _, p in out[:MAX_SESSIONS]]

    def _poll(self):
        # periodic rescan -> add new tails, drop tails whose file left the window
        # poll every tail -> build list[TranscriptState]
        # mark is_stale = (now - mtime) > STALE_SECONDS
        # emit sessions_changed(list); emit state_changed(list[0]) if any
```

Keeping `state_changed` (focused = `list[0]`) means the **compact widget keeps
working untouched** while the main window moves to the shelf — and lets Phase 1
land + be verified before any UI exists.

Discovery cost note: `rglob` still walks the whole `projects/` tree each rescan
(true today), but we only `stat()` for the mtime filter and only ever *open*
the ≤`MAX_SESSIONS` active files. No new per-file parsing cost at scale.

### 8.4 New UI — `SessionShelf` + `SessionTile` (`dashboard.py`)

```
ACTIVE SESSIONS — 3
┌──────────┐ ┌──────────┐ ┌──────────┐
│  (glow)  │ │  (glow)  │ │ (no glow)│
│  mascot  │ │  mascot  │ │  mascot  │
├──────────┤ ├──────────┤ ├──────────┤
│ project  │ │ project  │ │ project  │
│ CODING   │ │ THINKING │ │ IDLE     │
└──────────┘ └──────────┘ └──────────┘
```

- **`SessionTile(QWidget)`** = `SpritePlayer` (size ~120) + bold project label +
  activity label (colored via `ACTIVITY_COLORS`) + optional live/idle dot.
  - **Glow** = `QGraphicsDropShadowEffect` on the sprite (blurRadius ~40,
    offset 0, color = activity color). No new dependency — `QtWidgets` only, and
    `QGraphicsOpacityEffect` is already used in this file, so the effects path is
    known-good in the frozen build. IDLE → no effect (or grey, low alpha).
  - `update(state)` sets label text/color, swaps glow color, and calls
    `sprite.set_anims(f"{sid}:{activity}", ACTIVITY_ANIMS[activity])` — the
    unchanged-key no-op keeps the animation running smoothly across polls.
- **`SessionShelf(QWidget)`** = header (`ACTIVE SESSIONS — N`) + a row of tiles
  inside a **horizontal `QScrollArea`** (scrollbar hidden until overflow).
  - `set_sessions(list[TranscriptState])`: **diff, don't rebuild.** Keep
    `dict[session_id -> SessionTile]`; add new, remove gone, `update()` the rest.
    Rebuilding every 500 ms poll would thrash animations and flicker — diffing is
    essential and mirrors the existing `set_anims` design.
  - The `QScrollArea` resolves the doc's "C gets cramped past ~4" worry: window
    width stays stable; extra sessions scroll instead of ballooning the window.

### 8.5 Wire into `Dashboard`

- In `__init__`, **replace** the centered 240px `self.sprite` block
  (`dashboard.py:1272-1277`) with `self.shelf = SessionShelf(...)`.
- Connect `self._transcript.sessions_changed` → `self._on_sessions`.
- `_on_sessions(states)`:
  - `0 active sessions` → fall back to **today's behavior**: one rate-driven
    mascot + the `group_label` mood (sleepy/dancing by usage rate via
    `RateGroupTracker`). Preserves the current personality when nothing's
    running. (Keep a single hero `SpritePlayer` for this empty state, or render
    one rate-driven tile.)
  - `≥1` → `self.shelf.set_sessions(states)`.
  - Always drive the **compact** mascot from the focused session `states[0]`
    (via the existing `_set_sprite_anims` on `self.compact.sprite` only).
- `group_label`: repurpose as the shelf header, or retire (each tile self-labels).
- **Tile sizing** scales with count for a good single-session look without a
  giant window: `1→200, 2→160, 3→130, 4+→110` px, all inside the scroll area.

### 8.6 Mock mode (`_start_mock`, `dashboard.py:1592`)

Synthesize three fake sessions cycling activities and feed them through the same
`_on_sessions` path, so the shelf is fully developable/screenshot-able without
launching real concurrent Claude Code windows:

```
clawdmeter-windows  CODING   (live)
api-gateway         THINKING (live)
notes-cli           IDLE     (4m ago)
```

Run with `.\.venv\Scripts\python src\main.py --mock`.

### 8.7 Resolved open decisions (recommended defaults)

| Decision | Default | Why |
|---|---|---|
| **Idle policy** | Two-tier: **dim** at `STALE_SECONDS` (90s), **drop** at `ACTIVE_WINDOW_SECONDS` (10 min) | Matches mockup's "notes-cli · IDLE · last active 4m ago"; avoids a wall of dead sessions |
| **Max sessions** | **6**, then horizontal scroll | Bounds timers/CPU and window width |
| **Subagents** | **Excluded** (folded into parent) | They're not user-facing sessions |
| **Name collisions** (two sessions, same folder) | Show same name for now | Rare; revisit with a parent-dir suffix if it bites |
| **gitBranch** | Available (`HEAD`/branch in events) — **off by default** | Optional secondary label; not in the mockup |
| **Compact mode** | Single focused mascot for now | Multi = Variant D, separate effort |

### 8.8 Phased rollout (each phase independently verifiable)

1. **Data layer** — `transcript.py` multi-watch + `cwd`/name capture +
   subagent exclusion. Verify by logging `sessions_changed` with two real
   sessions open; existing single-mascot UI keeps working via `state_changed`.
2. **Shelf UI** — `SessionTile` + `SessionShelf`, wired behind `_on_sessions`,
   driven in `--mock`. Verify visually against `variant_c_shelf.png`.
3. **Polish** — glow colors, idle dimming/drop, tile sizing + scroll, empty-state
   fallback to the rate-driven mood.
4. **Settings (optional)** — expose `MAX_SESSIONS` and the idle-drop minutes;
   confirm `build.ps1`/`Clawdmeter.spec` needs **no** new Qt modules (drop-shadow
   is `QtWidgets`, already in use).

### 8.9 Risks / watch-items

- **Animation thrash** if tiles are rebuilt per poll → mandatory diff-by-id +
  `set_anims` key dedupe (§8.4).
- **Per-poll cost** with many tiles: N sprite `QTimer`s + the watcher timer.
  `MAX_SESSIONS=6` keeps it small; sprite frames are 20×20 blits.
- **`_alpha_bbox`** runs a per-pixel scan on anim load; identical anims load on
  several tiles. Cheap at 20×20, but a small shared cache is an easy win if
  profiling flags it.
- **Window min-size** (`440×550`) assumes the hero; revisit `setMinimumSize`
  once the shelf + scroll area replace it.

### 8.10 Implementation status (built on `claude/variant-c-mascot-shelf`)

Built via a parallel agent workflow (data layer ∥ UI module, then integration), then
a 3-way adversarial review, then fixes. **Not committed/pushed** — working tree only.

**Files**
- `src/transcript.py` — multi-file watcher: `_SessionTail` per file, `sessions_changed`
  (list, newest-first) + back-compat `state_changed` (focused/IDLE), pure helpers
  (`is_subagent_path`, `project_name_from_cwd`, `select_active`), `ACTIVITY_COLORS`,
  extended `TranscriptState`. Original byte-tailing preserved verbatim.
- `src/session_shelf.py` (new) — `SessionTile` (glow via `QGraphicsDropShadowEffect`)
  + `SessionShelf` (diff-by-id, **re-orders to newest-first**, count-based sizing).
- `src/dashboard.py` — `_on_sessions` (hero↔shelf toggle, compact mirrors focused
  session), 3-session `--mock`, `shelf.stop_all()` on quit, `_shelf_active` guard.
- `src/sprite_player.py` — `set_size()` (re-scales the pixmap, not just the box) +
  `resume()` (wake the paused hero).
- `tests/test_transcript_sessions.py` (11) + `tests/test_session_shelf.py` (6, incl.
  reorder + survivor-resize regressions). **Full suite: 43 passing.**

**Review findings fixed** — blocker: `set_sprite_size` never re-scaled the mascot;
high: shelf never re-ordered tiles (showed first-seen order); high/med: compact mascot
driven by two competing `set_anims` keys (flicker every usage poll); med: stale
`group_label` over a live shelf; plus lows (single `stat()`, dead `_mock_group`, min
width 500→520, deduped iteration, paused hidden hero, dot objectName). **Deferred**
(per §8.9, non-blocking): module-level shared pixmap cache across `SpritePlayer`s.

**Packaging:** no `requirements.txt` / `Clawdmeter.spec` / `build.ps1` change — the spec
follows imports from `main.py` (`pathex=['src']`), so `session_shelf.py` auto-bundles.

---

## Working agreement (reminder)

**Do not `git push` or open PRs without explicit approval** — local commits only
until asked. (Recorded in `CLAUDE.md`.)