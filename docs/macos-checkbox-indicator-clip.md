# macOS: checkbox indicator loses its bottom border row

**Status:** open, unfixed. Cosmetic, macOS only.

The `QCheckBox` indicator renders 17 pixel rows instead of 18 — the bottom
border row is absent, so an unchecked box reads as square-bottomed. A checked
box hides it (a solid fill loses a row invisibly), which is why toggling the
control appears to "fix" it.

## Confirmed

Native 1x screen captures (no remote-desktop scaling in the path), on both
Macs, on a **fresh default config** with prefs wiped:

```
y=14 ################      <- top cap row
y=15 ##++++++++++++++##
y=16..29  #++++++++++++#   <- 14 side rows
y=30 ##++++++++++++++##
y=31 ................      <- bottom cap row MISSING
```

- Reproduces on macOS 15.6.1 (M2, arm64) and 15.7.7 (Intel VM), same build.
- Windows renders it correctly.
- Not the theme: all four themes produce identical checkbox QSS geometry
  (`sizeHint=24`, indicator `h=18 y=3 bot=20`); only colours differ.
- Not the config: reproduces with preferences deleted.
- Not remote-display scaling: reproduces in native `screencapture` output and
  over plain VNC without stretching.

## Ruled out: layout squeeze

The first hypothesis was that a page sitting a pixel or two under its sizeHint
makes `QVBoxLayout` shave a checkbox to 23px, leaving a 17px content box (QSS
`padding: 3px 0`) for an 18px indicator. That mechanism is **real** — a
container short by 1px does shrink checkboxes — but it is **not** what happens
here. Measured in the app on macOS at the exact window size that shows the bug:

```
window=566x511 viewport=480 body=480 hint=283 vbar_max=0
  Send a push notification   h=24 hint=24 min=24 ind(y=3 h=18 bot=20)
```

Correct height, correct indicator rect, no deficit, no scrollbar needed. The
geometry is right and the painting is short.

`Dashboard.__init__` still pins every settings checkbox to its `sizeHint` as
hardening against that separate squeeze (see
`tests/test_settings_checkbox_clipping.py`), but it does not address this bug.

## Also ruled out as evidence

- `widget.grab()` renders the widget standalone at its sizeHint and shows all 18
  rows. It cannot see what the layout or the compositor did, so it is useless
  for this class of bug. Two wrong conclusions were drawn from it.
- Screenshots that pass through RDM or any resizing step. A 1px border survives
  or vanishes depending on where it lands in the resample grid, and two
  identical checkboxes in one image measured 21 and 20 rows.

## Next lead

Geometry is correct, so suspect `QStyleSheetStyle`'s rounded-rect painting of
`QCheckBox::indicator` on macOS — likely a box-model rounding difference with
`width/height: 16px` + `border: 1px` + `border-radius: 2px`. Worth trying, each
verified with a native capture: drop `border-radius`, set explicit
`min-height`/`max-height`, or bump the indicator to an even size.

## Capturing evidence on a headless Mac

`screencapture` returns the desktop with all app windows omitted unless the
calling process holds Screen Recording. Granting it to Terminal does **not**
cover SSH — the responsible process there is `sshd`, and `CGWindowListCreateImage`
returns a 0x0 image. Either add `/usr/libexec/sshd-keygen-wrapper` to Screen
Recording, or run `screencapture -i ~/Desktop/x.png` in Terminal on the Mac
itself and pull the file.
