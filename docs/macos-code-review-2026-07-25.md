# macOS code review — `feat/macos-native-window`

**Date:** 2026-07-25
**Scope:** `git diff develop..feat/macos-native-window` (native NSWindow chrome, compact/mini HUD, live-theme fix, `.dmg` packaging) plus the macOS code paths already merged to `develop` — `macos_window.py`, `macos_keychain.py`, the `NSColorSampler` eyedropper in `color_picker.py`, the darwin branches in `poller.py` / `token_refresh.py` / `run_at_startup.py` / `update_check.py`, `build-macos.sh`, `Clawdmeter.spec`, `packaging/`.
**Method:** 5 parallel reviewers (conventions, bug scan, git history, macOS professional practice, comment compliance), then every finding re-verified by hand against the source. No PR exists — the branch is local-only, so this file is the record.

**Branch state at review time:** `cf6688f` (4 commits ahead of `develop`), 322 tests green.

**Severity rule used:** realistic probability of trigger × actual impact if triggered — not "a reviewer confirmed the mechanism is real."

---

## Group 1 — Correctness / bugs

### 🔴 High

#### 1. "Always on top" silently destroys the native window chrome

**Files:** `src/dashboard.py:3913-3917`, `src/winutil.py:76-90`, `src/dashboard.py:3286-3290`

`Dashboard.showEvent` applies the native styling exactly once and latches it:

```python
if sys.platform == "darwin" and not getattr(self, "_macos_styled", False):
    self._macos_styled = True
    QTimer.singleShot(0, lambda: macos_window.style(self, theme.active().bg_deep))
```

`_set_always_on_top()` calls `winutil.set_topmost()`, which **off Windows** toggles `Qt.WindowStaysOnTopHint`. That recreates the underlying NSWindow (the function's own comment documents this: *"Off Windows, toggling WindowStaysOnTopHint recreates the native window"*), then `_reshow()` calls `show()` again — re-firing `showEvent` on the **new** NSWindow. But `_macos_styled` is still `True` on the surviving Python object, so `macos_window.style()` never runs again.

**Failure scenario:** Settings → tick "Always on top" on macOS. The transparent titlebar, full-size content view and fullscreen-disable are all lost; the window reverts to a stock opaque NSWindow with the 28px title-bar inset back. Persists until the app is restarted.

**Not affected:** startup. `Dashboard.__init__` sets the persisted flag *before* the first `show()` (`dashboard.py:2725-2727`), so a user who has always-on-top saved from a previous session is fine. Only the runtime toggle breaks.

**RESOLVED** — but not the way this section first proposed, and the first attempt failed on hardware. Recording both, because the trap is worth remembering.

*Failed attempt:* re-key the guard off `winId()` so a rebuilt window gets re-styled. **This does not work.** On macOS `winId()` is the **NSView** pointer, not the NSWindow — Qt keeps the same `QNSView` and re-parents it into the new NSWindow, so `winId()` is unchanged across the rebuild and the guard still skips the repair. Verified broken on the M2.

*Actual fix:* stop recreating the window at all. Set the NSWindow's **level** in place —
`window.level = .floating` (`NSFloatingWindowLevel`, 3) / `NSNormalWindowLevel` (0) — which is what Mac apps and every window-pinning utility do. One property on the existing window: no teardown, no rebuild, nothing to re-apply, no flicker. It is the exact AppKit counterpart of the `SetWindowPos` path `winutil` already uses on Windows for the same reason; `Qt.WindowStaysOnTopHint` is the portable-but-destructive route.

Implemented as `macos_window.set_level()`, taken by `winutil.set_topmost()` on macOS (Linux / no-pyobjc still falls back to the flag toggle). The `showEvent` latch was deleted outright — `style()` is idempotent, so re-applying it on every show costs nothing and removes the whole stale-latch failure mode. **Verified on the M2:** toggled repeatedly, chrome stayed native.

*Known limit, undecided:* a floating window still will not appear over **another app's** fullscreen Space. That needs `NSWindowCollectionBehaviorFullScreenAuxiliary`, which is mutually exclusive with the `FullScreenNone` bit we set to keep the green button as zoom. Open decision.

---

### 🟠 Medium

#### 2. Debug `print()` ships in every macOS build

**File:** `src/macos_window.py:93-103`

```python
# DIAGNOSTIC: is the content area reserving space for the title bar? ...
try:
    cv = win.contentView()
    print(f"[macwin] frame=... contentLayoutRect_h=...", file=sys.stderr, flush=True)
except Exception:   # noqa: BLE001 - diagnostic only
    pass
```

Introduced by the exploratory commit `1b8fa72` (*"[test] … Experimental branch — drop if it doesn't look right"*) and never removed across the three follow-up commits. `style()` runs on every window show **and** every `WindowStateChange` (minimize / zoom / restore), so this fires repeatedly through normal use, dumping NSWindow geometry into the user's unified log.

It also diverges from the repo's own precedent for writing to stderr from a frozen build — `dashboard.py:3741-3743` checks `sys.stderr is not None` first; this relies on a blanket `except Exception: pass` instead.

**Fix:** delete it, or gate it behind a debug flag. **~2 min.**

#### 3. Custom-theme dialog has clipped corners

**Files:** `src/dashboard.py:1388-1394`, `src/theme.py:606-609`

`CustomThemeEditor.showEvent` masks the dialog's content layer to a 12px radius:

```python
QTimer.singleShot(0, lambda: macos_window.round_window(self, 12))
```

But its stylesheet is plain `STYLESHEET` with `objectName("root")`, and the `#root` rule is:

```css
QWidget#root {
    background-color: #0e1116;
    border: 1px solid #1f2937;
}
```

No `border-radius`, on any platform. `MiniWidget` and `CompactView` each got an explicit darwin-only radius append (`\nQWidget#miniRoot{border-radius:13px}` / `#compactRoot{border-radius:13px}`) precisely so the QSS-painted border follows the native mask. This dialog was missed.

**Failure scenario:** Settings → Appearance → Custom theme on macOS. The 1px border traces a square and is chopped off at each corner by the rounded mask, instead of curving with it.

**Fix:** same darwin-only radius append the other two windows use. **~10 min.**

---

### 🟡 Low

#### 4. `setCollectionBehavior_` replaces the mask instead of OR-ing it

**File:** `src/macos_window.py:89`

```python
win.setStyleMask_(int(win.styleMask()) | _STYLE_FULL_SIZE_CONTENT_VIEW)   # correct
win.setCollectionBehavior_(_COLLECTION_FULLSCREEN_NONE)                   # clobbers
```

Discards any collection-behaviour bits Qt already set (Spaces / Mission Control / window cycling). Qt's default is likely `0` today, so no observed impact — latent inconsistency with the line two above it.

#### 5. Main window's NSWindow background colour is not refreshed on a live theme switch

**File:** `src/dashboard.py:170` (`apply_theme`) vs `:3913-3917`, `:3925-3934`

`macos_window.style(self, theme.active().bg_deep)` paints the NSWindow background, and only runs from `showEvent` / `changeEvent`. `apply_theme()`'s new `apply_theme_style()` broadcast reaches `MiniWidget` and `CompactView` but not `Dashboard`, which defines no such method. After a theme switch the native window background keeps the old palette's `bg_deep` until the next minimize/zoom.

**Visible as:** a wrong-coloured sliver that Cocoa exposes during a live drag-resize, before Qt repaints. Cosmetic and brief.

#### 6. Stale comments

- `src/dashboard.py:2428`, `:2610-2612`, `:2717-2719` — three `NavRail` comments assert it is "full-height" / "spans the whole left side". On macOS it now starts 28px down (`TOP_INSET = 28 if sys.platform == "darwin" else 0`) and is 28px shorter. `reposition()`'s own docstring was updated; these three weren't.
- `build-macos.sh:5-8` — still claims *"The build VM is Intel x86_64, so this yields an x86_64 .app that also runs on Apple Silicon via Rosetta 2."* We now build on an M2 and produce **arm64**. The statement is not merely stale, it is backwards: an arm64 binary does not run on Intel at all.
- `src/dashboard.py:3928-3929` — the `changeEvent` comment defends against a "fullscreen transition", but `macos_window.style()` sets `NSWindowCollectionBehaviorFullScreenNone`, so native fullscreen is unreachable for this window. The `not self.isFullScreen()` guard is dead weight.

---

## Group 2 — Not how professional Mac developers / tech companies do it

### 🔴 High

#### 1. No Developer ID signing or notarization — ad-hoc only

**Files:** `build-macos.sh:66-74`, `Clawdmeter.spec:230` (`codesign_identity=None`, `entitlements_file=None` — no hardened runtime)

This is the ship-blocker for public macOS distribution. `build-macos.sh` currently says an ad-hoc signature *"does NOT satisfy Gatekeeper for a downloaded app — that still needs right-click → Open."* **That is now false: macOS 15 Sequoia removed the Control-click bypass** — and Sequoia (15.6.1) is what our own M2 runs.

What a downloader actually faces today: open the app → blocked → System Settings → Privacy & Security → scroll to find the blocked item → "Open Anyway" → admin password. And because every build carries a *fresh* ad-hoc signature, no trust carries over — they repeat it for **every release**.

The professional path for non-App-Store distribution is Developer ID signing + Hardened Runtime + `xcrun notarytool submit` + `xcrun stapler staple`. None of that plumbing exists; `.github/workflows/build.yml` has no macOS target at all.

**Cost:** $99/yr Apple Developer Program, plus roughly half a day of build/CI plumbing.

Sources: [Sequoia removes the Control-click bypass](https://www.idownloadblog.com/2024/08/07/apple-macos-sequoia-gatekeeper-change-install-unsigned-apps-mac/) · [Apple Developer Forums](https://developer.apple.com/forums/thread/767435)

#### 2. arm64-only, not `universal2`

**Files:** `build-macos.sh`, `Clawdmeter.spec:230,290` (`target_arch=None`)

Every mainstream Mac app ships a universal binary. We produce a single-arch arm64 build, which **cannot run on Intel Macs at all**. Needs an x86_64 Python/PySide6 leg (or a CI cross-build) merged with `lipo`, or an explicit, documented decision to be Apple-Silicon-only.

---

### 🟠 Medium

#### 3. Tray icon is not a macOS template image

**File:** `src/dashboard.py:251-266` (`_tray_pixmap`), `:269-281` (`_tray_alert_pixmap`) — no darwin branch, `setIsMask()` never called

macOS menu-bar icons are expected to be monochrome **template images** so they adapt to light/dark menu bars and invert when clicked. Ours is a full-colour donut (indigo / amber / red by heat) handed to `QSystemTrayIcon.setIcon()` unmodified on every platform. Sitting among Wi-Fi, battery and Bluetooth, it is the single most immediately visible "this was ported from Windows" tell, and it will not invert on highlight.

**This is a design decision, not a bug to patch blindly:** the colour carries real information (cool / warm / hot). The tradeoff is monochrome-plus-tooltip (Mac convention) versus keeping the colour and accepting the non-native look.

#### 4. Keychain read shells out to `/usr/bin/security`

**File:** `src/macos_keychain.py:64-70`

```python
subprocess.run(["security", "find-generic-password", "-s", service_name(), "-w"], ...)
```

Two consequences of doing it via the CLI rather than the Security framework in-process:

- The OS permission dialog reads ***"security" wants to use your confidential information stored in "Claude Code-credentials"*** — not "Clawdmeter wants…". Confusing for a user who has never heard of `security`.
- If they click **Always Allow** (the obvious way to dismiss it), the grant attaches to the shared `/usr/bin/security` binary, not to Clawdmeter — so any other script on the machine that shells out to `security` inherits standing access to that Keychain item.

The norm is `SecItemCopyMatching` in-process (via pyobjc), so the prompt and the grant are scoped to the calling app.

**Unverified, worth a 2-minute check on the M2:** `_SECURITY_TIMEOUT_SECONDS = 10` (`macos_keychain.py:42`) with a 60s poll cadence. If the permission dialog is on screen and the user takes longer than 10s, `subprocess.run(timeout=10)` kills `security` mid-prompt and the read is swallowed as "no credentials" — the dialog would plausibly reappear each poll until they answer inside a 10-second window. This is inferred from documented Keychain ACL behaviour, **not observed on hardware**.

Source: [scriptingosx.com — Keychain access from shell scripts](https://scriptingosx.com/2021/04/get-password-from-keychain-in-shell-scripts/)

#### 5. Run-at-login uses a hand-written LaunchAgent plist, not `SMAppService`

**File:** `src/run_at_startup.py:190-280`

Apple introduced `SMAppService` in Ventura (13, 2022) specifically to replace hand-managed `~/Library/LaunchAgents` plists. Since Ventura, macOS surfaces even hand-written LaunchAgents in **System Settings → General → Login Items & Extensions**, and toggling one off there disables the job **without deleting the plist**. `_macos_is_enabled()` checks only `plist_path().exists()`, so after a user disables auto-launch the OS-taught way, Clawdmeter's own checkbox keeps reporting "ON" and keeps rewriting the (now OS-disabled) plist on every launch. Behaviour matches user intent; our UI lies about it.

Sources: [managing Login Items in Ventura](https://macblog.org/manage-custom-login-items/) · [SMAppService overview](https://theevilbit.github.io/posts/smappservice/)

---

### 🟡 Low

#### 6. `codesign --deep` is deprecated and Apple calls it harmful

**File:** `build-macos.sh:73`

`codesign --force --deep --sign -` over a PyInstaller onedir bundle stamps identical entitlements on every nested binary and can miss code outside recognised nested-code locations. Harmless right now (ad-hoc, no entitlements — nothing to mis-apply), but it must be replaced the moment High #1 is addressed. Correct approach is signing inner binaries first, then the bundle, with explicit entitlements.

Source: [Apple Developer Forums — "--deep Considered Harmful"](https://developer.apple.com/forums/thread/129980)

#### 7. No in-app updater on macOS

**File:** `src/update_check.py:81-95`

Sparkle (signed appcast + EdDSA-signed archives) is the de facto norm for non-App-Store Mac software. Ours is notify-only, which is a deliberate, documented scope cut — the code itself flags that self-replacing a *running* `.app` needs solving properly rather than hacking around. Listed only because it **compounds High #1**: with no in-app update path, every future release re-runs the full Gatekeeper gauntlet rather than just the first install.

---

## What came back clean

Verified correct; do not re-litigate:

- **Transparent-titlebar `NSWindow`** rather than `Qt.FramelessWindowHint` — genuinely how VS Code / Slack / Spotify do custom chrome (native rounded corners, real shadow, real traffic lights).
- **`LSUIElement: True`** in `Clawdmeter.spec` — correct for a menu-bar-only agent app (no Dock icon, no Cmd-Tab entry).
- **`NSColorSampler`** eyedropper — the native API, and it sidesteps the Screen Recording permission the DIY screenshot overlay needed.
- **dmgbuild drag-to-install `.dmg`** — `Applications` symlink, `UDZO` compressed read-only, window layout written straight to `.DS_Store` (so it builds headlessly over SSH, which `create-dmg` cannot).
- **onedir + `BUNDLE`** — forward-compatible with PyInstaller v7, deliberately chosen.
- **Info.plist keys** — `CFBundleShortVersionString`, `CFBundleVersion`, `NSHighResolutionCapable`, `LSMinimumSystemVersion: 11.0` all present.
- **Platform guards** — the Windows/Linux `else` branches in `MiniWidget`, `CompactView`, `TitleBar`, `Dashboard.__init__` and `NavRail` reproduce pre-branch behaviour exactly.
- **The live-theme fix** (`dd99ccf`) — correct, and covered by two regression tests that fail on the pre-fix code.
- **`.dmg` build failure modes** — missing `tiffutil`, missing `dmgbuild[badge_icons]`, `dmgbuild` failing, `hdiutil` verification failing all degrade with an explicit warning rather than silently shipping a broken artifact.

---

## Status

Fixed and verified in commit-pending work on `feat/macos-native-window` (327 tests green; every new regression test proven to fail on the pre-fix source):

| # | Item | Status |
|---|------|--------|
| G1 High 1 | Always-on-top strips native chrome | ✅ fixed via NSWindow level — **verified on M2** |
| G1 Med 2 | Diagnostic `print()` | ✅ removed |
| G1 Med 3 | Theme-dialog corner radius | ✅ fixed (shared `MACOS_RADIUS`), plus an `apply_theme_style()` hook so the radius append doesn't break theme-following — checked on M2, no visible regression |
| G1 Low 4 | `setCollectionBehavior_` clobbering | ✅ now ORs into the existing mask |
| G1 Low 5 | NSWindow bg on live theme switch | ✅ `Dashboard.apply_theme_style()` — checked on M2, no artefact on resize |
| G1 Low 6 | Stale comments | ⬜ not started |
| G2 all | Distribution + native-feel gaps | ⬜ not started |

**Auto-hide title bar on macOS — RESOLVED, removed** (`2e87e33`). The traffic lights are drawn by AppKit in the NSWindow titlebar region, *not* by our `TitleBar` widget, so `_apply_auto_hide` collapsing our widget to height 0 left them stranded over the content with the wordmark and view buttons gone. Apple's HIG also says never hide or reposition them, and the small-footprint need is already covered on Mac by the compact and mini HUD views. `AUTO_HIDE_SUPPORTED` is the single gate, enforced in `_apply_auto_hide()` so a value persisted on Windows and synced to a Mac still comes up off; the checkbox is hidden (not greyed out) on macOS. Confirmed absent on the M2.

## Suggested order of work

| # | Item | Group | Effort |
|---|------|-------|--------|
| 1 | Always-on-top strips native chrome | G1 High | ~10 min |
| 2 | Remove the diagnostic `print()` | G1 Med | ~2 min |
| 3 | Custom-theme dialog corner radius | G1 Med | ~10 min |
| 4 | Stale comments (NavRail, build-macos.sh arch, fullscreen) | G1 Low | ~10 min |
| 5 | Decide: template tray icon vs keep the colour | G2 Med | decision first |
| 6 | Developer ID + notarization + stapling | G2 High | $99/yr + ~half a day |
| 7 | universal2 build | G2 High | needs an x86_64 build leg |
| 8 | Keychain via `SecItemCopyMatching`; `SMAppService` login item | G2 Med | ~half a day each |

Items 1–4 are the whole of what is actually broken: roughly 30 minutes.
