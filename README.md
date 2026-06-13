# Clawdmeter-Windows

Standalone Windows desktop dashboard for Claude Code usage.

<p align="center">
  <img src="assets/ClawdMeter.gif" width="420"
       alt="Clawdmeter-Windows — the Clawd mascot reacting live to Claude Code activity, with session and weekly usage">
</p>

## What it shows

- **Session (5h) %** with reset countdown
- **Weekly (7d) %** with reset countdown
- A live label + Clawd animation showing what Claude Code is doing, falling back
  to a usage-rate mood when idle
- A system-tray icon whose fill arc tracks session % — **hover it for a quick
  session & weekly readout**

![Hover the tray icon for a session and weekly usage readout](assets/Screenshot-6.png)

## The mascot reacts to what Claude Code is doing

Clawd's animation and the label beneath it follow your live Claude Code session
in near-real-time — read from the local transcript:

|  |  |
|:--:|:--:|
| ![Coding](assets/Screenshot-4.png) | ![Reading](assets/Screenshot-3.png) |
| **CODING** — editing, writing, running commands | **READING** — reading, grepping, globbing |
| ![Searching](assets/Screenshot-7.png) | ![Planning](assets/Screenshot-9.png) |
| **SEARCHING** — web fetch / search | **PLANNING** — todos, sub-agents & task management |

There's also an **INTEGRATING** mood for when Claude Code reaches out through an
MCP server tool — the label shows which server it's talking to (e.g.
`INTEGRATING — github/list_issues`).

When Claude Code goes quiet, the mascot falls back to a mood driven by your
usage rate — sleepy when you're idle, dancing when you're burning through
tokens (the same 4-group logic as the original firmware).

## Compact view

Want something tiny that stays out of the way? Click the **compact-mode button**
in the title bar to shrink the dashboard down to a small always-on-top readout —
the mini mascot beside your session and weekly percentages, each with its reset
time. It's frameless, keeps no taskbar entry, and is draggable (it remembers
where you left it). **Double-click** it — or right-click → **Expand** — to pop
back to the full dashboard.

![Clawdmeter-Windows compact view — a tiny always-on-top readout with the mascot and session and weekly percentages](assets/Screenshot-compact-view.png)

## Download

Grab the latest `Clawdmeter.exe` from the
[Releases](../../releases) page — it's a single self-contained file (~27 MB,
bundling Python + Qt), no install needed. Just run it.

> **Heads up:** the exe is not code-signed, so Windows SmartScreen may show a
> "Windows protected your PC / unknown publisher" prompt the first time you run
> it. Click **More info → Run anyway**. If you'd rather not trust the binary,
> [run from source](#run-from-source) or [build it yourself](#build-the-standalone-exe).

## How it works

It reads your Claude Code OAuth token from `~/.claude/.credentials.json`,
sends a minimal 1-token request to `api.anthropic.com/v1/messages` on a
configurable interval (60s by default), and reads the rate-limit headers from
the response. The window minimises to the system tray; closing the window
hides it. **Quit** from the tray menu fully exits.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer (the code uses 3.10+ syntax)

## Run from source

```powershell
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python src\main.py
```

Add `--mock` to drive the UI with synthetic data (no API calls):

```powershell
.\.venv\Scripts\python src\main.py --mock
```

## Build the standalone .exe

```powershell
.\build.ps1
```

Output: `dist\Clawdmeter.exe` — single-file, no console window, ~27 MB.

`Clawdmeter.spec` prunes the parts of PySide6/Qt the app doesn't use (the
QML/Quick stack, the ~20 MB software-OpenGL fallback, unused image-format and
platform plugins, and Qt's bundled translations) to keep the exe roughly half
the size of an unpruned PySide6 build. If you start importing additional Qt
modules, check the pruning block in the spec so you don't strip something you
now need.

## Settings

Open the settings panel from the gear icon in the title bar.

![Clawdmeter-Windows settings panel](assets/Screenshot-2-Settings.png)

- **Credentials** — by default the app reads `~/.claude/.credentials.json`. Use
  **Use alternative credentials** (or set `CLAUDE_CREDENTIALS_PATH`) to point at
  a non-default `.credentials.json`.
- **Token** — Claude's OAuth access token expires roughly every 8 hours,
  which would otherwise blank the dashboard. With **Auto-refresh when expired**
  on (the default), the app mints a fresh token automatically so it stays live.
  The **Refresh token now** button is a manual override and is enabled only when
  the token is actually expired.
- **Window** — toggle **Always on top**, **Auto-hide title bar**, and **Quit on
  close** (closes the app instead of minimizing to the tray).
- **Usage polling** — how often the app checks your usage. Each check is a tiny
  billed API request, so the interval is adjustable from **10 to 600 seconds**
  (60 by default): lower is fresher but makes more requests; higher is gentler
  on your quota when you leave it running. Out-of-range entries snap to the
  nearest allowed value.
- **Notifications** — **Notify on limit reset** pings you the moment a usage
  limit resets so you know you can resume — but only when you were actually near
  the limit (or already throttled), so it stays quiet otherwise. It shows a tray
  notification and briefly flashes the tray icon; **Play a sound**, **Pop the
  window to front**, and **Send a push to my phone** are optional extras you can
  switch off. The phone push reaches you via either **ntfy** or **Telegram**:
  with [ntfy](https://ntfy.sh) (no account or API key) you subscribe to a topic
  of your choosing in the ntfy app — pick a long, hard-to-guess topic since
  anyone who knows it can read your alerts; with **Telegram** you create a bot
  via @BotFather and enter its token and your chat ID. Keep both secret.
- **Start menu** — add or remove a Start-menu shortcut (right-click it in Start
  to pin).

The panel scrolls if the window is too short to fit every section.

## Credit

- **Original project** — concept, firmware, and daemon by Hermann Björgvin
  (@HermannBjorgvin): <https://github.com/HermannBjorgvin/Clawdmeter>. This is a
  software-only Windows port of that work.
- **Clawd pixel art** — the mascot sprites originate from
  <https://claudepix.vercel.app> (as noted in `assets/sprites/manifest.json`),
  extracted from the upstream firmware's `splash_animations.h`.
- **Clawd mascot** — the Clawd character is © Anthropic PBC (see below).

## License & disclaimers

The **source code** in this repository is licensed under the
[MIT License](LICENSE).

The Clawd mascot sprites and related artwork (`assets/sprites/`,
`assets/_splash_animations.h`) are **not** covered by the MIT License. The
Clawd mascot is © Anthropic PBC and remains Anthropic's property. These assets
are included under the same "gray area" as the upstream project and are not
licensed for reuse — if you fork or redistribute, you are responsible for your
own use of them. See [NOTICE](NOTICE) for the full attribution and asset
carve-out.

This is an unofficial, independent project. It is **not affiliated with,
endorsed by, or sponsored by Anthropic**. "Claude", "Clawd", and "Anthropic"
are trademarks of Anthropic PBC, used here for descriptive/identification
purposes only.

This software is provided "as is", without warranty of any kind. Use at your
own risk.
