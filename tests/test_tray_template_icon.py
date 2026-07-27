"""macOS menu-bar icons must be template images.

A status-bar icon on macOS is expected to be a black silhouette plus alpha; the
OS recolours it for the light/dark menu bar and inverts it while the menu is
open. A full-colour icon among monochrome system items is the most immediately
visible "ported from Windows" tell, and it does not invert on click.

Windows and Linux trays are routinely full-colour, so the icon must be left
alone there.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dashboard  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _swatch() -> QPixmap:
    """A pixmap that is half opaque salmon, half transparent."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.fillRect(0, 0, 8, 16, QColor("#CE7D6B"))
    p.end()
    return pm


def test_macos_icon_is_a_template(monkeypatch):
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    icon = dashboard.tray_icon(_swatch())
    assert icon.isMask(), "macOS status-bar icons must be template images"


def test_macos_template_is_black_but_keeps_its_alpha(monkeypatch):
    # A template is defined by its alpha channel; the colour must be flattened,
    # and the transparent half must STAY transparent or the silhouette is a box.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    icon = dashboard.tray_icon(_swatch())
    img = icon.pixmap(16, 16).toImage()
    opaque = img.pixelColor(2, 8)
    clear = img.pixelColor(12, 8)
    assert opaque.alpha() > 0 and clear.alpha() == 0, "alpha channel not preserved"
    assert (opaque.red(), opaque.green(), opaque.blue()) == (0, 0, 0), (
        "template pixels must be black, not the source colour"
    )


def test_windows_and_linux_icons_are_untouched(monkeypatch):
    for platform in ("win32", "linux"):
        monkeypatch.setattr(dashboard.sys, "platform", platform)
        icon = dashboard.tray_icon(_swatch())
        assert not icon.isMask(), f"{platform}: tray icons are full-colour"
        c = icon.pixmap(16, 16).toImage().pixelColor(2, 8)
        assert (c.red(), c.green(), c.blue()) != (0, 0, 0), (
            f"{platform}: colour must survive"
        )


def test_accepts_a_path_as_well_as_a_pixmap(monkeypatch):
    # The live tray icon comes from assets/icon.png; the flash comes from a
    # generated QPixmap. Both call sites go through this helper.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    from sprite_player import assets_root
    path = assets_root() / "icon.png"
    assert path.exists(), "assets/icon.png is the real tray icon"
    assert dashboard.tray_icon(path).isMask()


def test_a_missing_file_does_not_explode(monkeypatch):
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    icon = dashboard.tray_icon("/no/such/icon.png")
    assert icon is not None and not icon.isMask()   # null pixmap, returned as-is


def test_the_alert_flash_icon_is_also_a_template(monkeypatch):
    # The reset flash swaps in a green variant; if that one stayed full-colour
    # the menu bar would flash colour on an otherwise monochrome icon.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    assert dashboard.tray_icon(dashboard._tray_alert_pixmap()).isMask()
