"""The Settings tab rail must fit its labels in whatever font is in use.

Found 2026-07-27 on Ubuntu (Cantarell 11): the rail was a hard 148px, which
fits Segoe UI but clipped the three longest labels -- "Appearance",
"Connection" and "Notifications" rendered as "Appearanc", "Connectior",
"Notificatior".

These tests deliberately assert MECHANISM, not pixel counts. The offscreen Qt
platform has no real font database: it reports the family name from the
stylesheet but measures every string at a flat 13px per character, so
"Notifications" comes out at 169px where real Segoe UI is nearer 85px. Any
assertion about absolute widths here would be testing a fiction. What can be
tested honestly is that the rail responds to what the font metrics say, and
that it never drops below the width Windows shipped with.

The real check is visual, on a Linux box with a wide font.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402
import theme  # noqa: E402

_app = QApplication.instance() or QApplication([])
app_settings.set_theme = lambda name: None


@pytest.fixture
def panel():
    # Restore the application stylesheet afterwards. Setting it and walking
    # away leaks ~14KB of QSS into every test module that runs later, and a
    # test in this suite has already been broken once by exactly that kind of
    # cross-module global leak.
    _previous = _app.styleSheet()
    _app.setStyleSheet(theme.build_qss(theme.active()))
    host = QWidget()
    p = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    try:
        yield p
    finally:
        p.deleteLater()
        host.deleteLater()
        _app.setStyleSheet(_previous)


def _rail(panel):
    return panel.findChild(QWidget, "settingsNav")


def test_rail_is_never_narrower_than_the_shipped_windows_width(panel):
    """Widening only: a font that measures small keeps 148, so the Windows
    layout stays byte-identical to what users already have.

    The font has to be shrunk deliberately. Asserting the floor against the
    default offscreen font proves nothing -- it measures "Notifications" at
    13 chars x 13px = 169px, so the computed width already clears 148 and the
    max() never binds. Deleting the max() outright left this green, which is
    how an earlier pass wrongly recorded this claim as refuted: the revert it
    tested was not one this assertion could ever have caught.
    """
    rail = _rail(panel)
    tiny = QFont()
    tiny.setPixelSize(1)
    for btn in panel._nav_group.buttons():
        btn.setFont(tiny)
    panel._size_settings_nav(rail, rail.layout())

    # The branch is only under test when the natural width is below the floor.
    widest = max(b.fontMetrics().horizontalAdvance(b.text())
                 for b in panel._nav_group.buttons())
    m = rail.layout().contentsMargins()
    natural = (widest + dashboard.SettingsPanel.NAV_BTN_H_PADDING
               + m.left() + m.right())
    assert natural < dashboard.SettingsPanel.NAV_MIN_WIDTH, (
        f"font did not shrink below the floor (natural={natural}) -- this "
        f"test would pass without the floor being applied at all")

    assert rail.minimumWidth() == dashboard.SettingsPanel.NAV_MIN_WIDTH, \
        "a narrow font shrank the rail below the width Windows shipped with"


def test_rail_widens_when_the_font_needs_more_room(panel):
    rail = _rail(panel)
    before = rail.minimumWidth()

    big = QFont()
    big.setPixelSize(40)                     # stands in for a wide desktop font
    for btn in panel._nav_group.buttons():
        btn.setFont(big)
    panel._size_settings_nav(rail, rail.layout())

    after = rail.minimumWidth()
    assert after > before, (
        "the rail ignored the font -- this is the bug that clipped "
        "'Notifications' on Ubuntu")

    widest = max(b.fontMetrics().horizontalAdvance(b.text())
                 for b in panel._nav_group.buttons())
    m = rail.layout().contentsMargins()
    assert after >= (widest + dashboard.SettingsPanel.NAV_BTN_H_PADDING
                     + m.left() + m.right()), \
        "the rail is narrower than its widest label plus padding"


def test_padding_constant_still_matches_the_stylesheet():
    """Guards the one duplicated number in the fix.

    Qt offers no way to read a stylesheet's box model back off a widget, so
    the button's horizontal padding is mirrored in NAV_BTN_H_PADDING. If
    someone retunes the QSS rule and not the constant, the rail goes back to
    being too narrow -- silently, and only on other people's machines.
    """
    qss = theme.build_qss(theme.active())
    m = re.search(r"QPushButton#navBtn\s*\{[^}]*?padding:\s*\d+px\s+(\d+)px", qss)
    assert m, "the navBtn padding rule moved; update NAV_BTN_H_PADDING with it"
    assert dashboard.SettingsPanel.NAV_BTN_H_PADDING == int(m.group(1)) * 2, (
        f"QSS says {m.group(1)}px per side, constant says "
        f"{dashboard.SettingsPanel.NAV_BTN_H_PADDING} total")
