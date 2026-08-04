# The settings checkbox with the missing bottom border — solved

**Status:** fixed. It was never a macOS painting bug.

## What it looked like

On macOS, the "Send a push notification" checkbox rendered its indicator 17
pixel rows tall instead of 18 — the bottom border row absent, so an unchecked
box read as square-cornered. Ticking it appeared to fix it (a solid fill hides a
missing row). The identical checkbox four pixels above it was always fine.

## What it actually was

`notify_how_box` is sized from its layout's **minimum**, and the word-wrapped
hint inside it reports a single line there while rendering two. The box came out
~4px shorter than the content it holds, so its last child hung over the bottom
edge and was clipped:

```
push checkbox geometry  = QRect(0, 109, 342, 24)   -> occupies rows 109..132
notify_how_box height   = 129                       -> valid rows 0..128
how_layout bottom margin = 0
```

The indicator's last row lands at 109+20 = 129 — the first row outside the box.
Exactly one row, exactly the one that disappeared.

A child overflowing its parent is clipped on every platform. macOS only made it
visible because that one row happened to be a border.

**Fix:** give `how_layout` a bottom margin so the box has room for its content.
Verified on macOS 15.6.1 against the running app: all six checkboxes render 18
rows with a full 16px bottom cap, the push one included.

## Everything that was tested and cleared first

Each of these was a real hypothesis, checked with native 1x screen captures on
hardware, and each was wrong:

| # | hypothesis | result |
|---|---|---|
| 1 | the indicator stylesheet — border-radius 0/2/4, padding 0/3/4, size 15/16/18px, min/max-height pinned, unstyled | all 18 rows |
| 2 | the height the checkbox is given — sizeHint, sizeHint±1, unconstrained, inside a scroll area | all 18 rows, including 23px |
| 3 | the full 14k theme stylesheet installed on the QApplication | all 18 rows |
| 4 | `macos_window.style()` transparent-titlebar chrome | all 18 rows |
| 5 | the scroll area's structure — transparent viewport, WA_StyledBackground body, the app's margins | all 18 rows |
| 6 | last-visible-widget position, incl. a hidden sibling after it | all 18 rows |
| 7 | stale paint — forced repaint, resize, hide/show | unchanged |
| 8 | theme, saved preferences, remote-display scaling | reproduces without all three |

A checkbox squeezed to `sizeHint-1` still paints all 18 rows, which is what
finally disproved the layout-squeeze theory an earlier "fix" had been built on.
That change was reverted.

## Two methodology notes worth keeping

**`widget.grab()` is not evidence.** It renders a widget standalone at its
sizeHint and cannot see what the layout or the compositor did to it. It reported
a perfect 18-row indicator throughout and produced two wrong conclusions.

**Screenshots that pass through any scaling are not evidence either.** A 1px
border survives or vanishes depending on where it lands in the resample grid;
two identical checkboxes in one RDM screenshot measured 21 and 20 rows. Only
native captures at 1x settled anything.

## Capturing evidence on a headless Mac

`screencapture` returns the desktop with all app windows omitted unless the
calling process holds Screen Recording, and granting it to Terminal does not
cover SSH — the responsible process there is `sshd`, so `CGWindowListCreateImage`
returns a 0x0 image. Route the capture through the approved app instead:

```
osascript -e 'tell application "Terminal" to do script "screencapture -x /tmp/x.png; exit"'
```

Pair it with the app printing each widget's `mapToGlobal` position, and the
measurement becomes exact instead of a crop guessed by eye.
