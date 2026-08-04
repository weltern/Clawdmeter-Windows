"""Regression guards for two confirmed colour-picker bugs (F4, F3).

F4 — a hex field edited to unparseable text must snap back to the colour in
effect, so the readout never disagrees with the swatch.

F3 — the eyedropper must sample from the screen under the cursor using THAT
screen's device-pixel ratio. A single whole-desktop ratio mis-maps every screen
scaled differently from the primary, and the error grows with distance from the
screen's origin.

Both tests are written to FAIL on the un-fixed code:
  * F4: drop the `else:` branch in `_from_hex` -> the field keeps the bad text.
  * F3: sample with one ratio off the virtual-desktop origin (the old
    `int((gpos - self._vg.topLeft()) * self._dpr)`) -> wrong pixel on screen 2.

Runs headless via QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from color_picker import ColorPicker, EyedropperOverlay  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --------------------------------------------------------------------- F4 ---

@pytest.mark.parametrize("garbage", ["#ZZZ", "notacolor", "", "#12345"])
def test_invalid_hex_reverts_field_to_current_colour(app, garbage):
    """Typing unparseable text and committing restores the live colour."""
    p = ColorPicker()
    p.set_color("#3366CC")
    assert p.hex.text().upper() == "#3366CC"

    p.hex.setText(garbage)
    p.hex.editingFinished.emit()      # what Enter / focus-out fires

    assert p.color().lower() == "#3366cc", "the colour itself must not change"
    assert p.hex.text().upper() == "#3366CC", (
        f"field still shows {p.hex.text()!r} — readout disagrees with the swatch")


def test_valid_hex_still_applies(app):
    """The revert must not swallow legitimate edits."""
    seen: list[str] = []
    p = ColorPicker()
    p.set_color("#3366CC")
    p.colorChanged.connect(seen.append)

    p.hex.setText("#FF0000")
    p.hex.editingFinished.emit()

    assert p.color().lower() == "#ff0000"
    assert p.hex.text().upper() == "#FF0000"
    assert [s.lower() for s in seen] == ["#ff0000"]


# --------------------------------------------------------------------- F3 ---

def _solid(w: int, h: int, colour: str) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(colour))
    return img


def _overlay_with_two_screens() -> EyedropperOverlay:
    """An overlay wired by hand to a fake mixed-DPI desktop.

    Screen 1: logical 0,0 1000x800 at 1.0x  -> a 1000x800 image, all red,
              except a single green pixel at device (10, 10).
    Screen 2: logical 1000,0 800x600 at 2.0x -> a 1600x1200 image, all blue,
              except a single white pixel at device (1000, 400), i.e. logical
              (1000 + 500, 200) on the desktop.

    Bypasses __init__ (it screenshots real hardware); only `_shots` and `_vg`
    matter to `_sample`.
    """
    o = EyedropperOverlay.__new__(EyedropperOverlay)
    s1 = _solid(1000, 800, "#ff0000")
    s1.setPixelColor(10, 10, QColor("#00ff00"))
    s2 = _solid(1600, 1200, "#0000ff")
    s2.setPixelColor(1000, 400, QColor("#ffffff"))
    o._shots = [
        (QRect(0, 0, 1000, 800), None, s1, 1.0),
        (QRect(1000, 0, 800, 600), None, s2, 2.0),
    ]
    o._vg = QRect(0, 0, 1800, 800)
    return o


def test_sample_uses_the_primary_screens_own_ratio(app):
    o = _overlay_with_two_screens()
    assert o._sample(QPoint(10, 10)).name() == "#00ff00"
    assert o._sample(QPoint(500, 400)).name() == "#ff0000"


def test_sample_uses_the_second_screens_own_ratio(app):
    """The 2.0x screen's marker sits at logical (1500, 200) on the desktop.

    With one shared 1.0x ratio the old code read device (500, 200) of that
    image — solid blue — and never found the marker.
    """
    o = _overlay_with_two_screens()
    assert o._sample(QPoint(1500, 200)).name() == "#ffffff", (
        "sampled the wrong pixel on the differently-scaled screen")


def test_sample_offset_grows_with_distance_on_a_scaled_screen(app):
    """Neighbouring logical pixels on the 2.0x screen must not collide.

    Under the old single-ratio mapping, points far from the virtual-desktop
    origin drifted; this pins the mapping at both ends of screen 2.
    """
    o = _overlay_with_two_screens()
    s2 = o._shots[1][2]
    s2.setPixelColor(0, 0, QColor("#123456"))          # logical (1000, 0)
    s2.setPixelColor(1598, 1198, QColor("#654321"))    # logical (1799, 599)
    assert o._sample(QPoint(1000, 0)).name() == "#123456"
    assert o._sample(QPoint(1799, 599)).name() == "#654321"


def test_sample_clamps_outside_every_screen(app):
    """A point in no screen falls back to the first shot, clamped, not crashing."""
    o = _overlay_with_two_screens()
    assert o._sample(QPoint(-50, -50)).name() == "#ff0000"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
