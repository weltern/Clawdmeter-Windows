"""No widget in the settings panel may hang over its container's bottom edge.

This is the invariant that was violated. `notify_how_box` is sized from its
layout's minimum, and the word-wrapped hint inside it reports one line there
while rendering two, so the box came out ~4px shorter than its content. Its
last child — the "Send a push notification" checkbox — sat at y=109..132 inside
a 129px box, and the row that fell outside was its indicator's bottom border.

Measured on macOS 15.6.1 against the running app via native screen captures:
17 rendered rows instead of 18, reading as a square-cornered box, while the
identical checkbox directly above it was fine. Every other explanation was
tested and cleared first — see docs/macos-checkbox-indicator-clip.md.

A child overflowing its parent is clipped on any platform; macOS just made it
visible here. So this asserts the geometry, not the pixels.
"""
import os
import sys

import pytest
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import theme  # noqa: E402


@pytest.fixture
def panel():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    prev_sheet, prev_theme = app.styleSheet(), theme.active_name()
    app.setStyleSheet(theme.build_qss(theme.active()))
    import dashboard
    host = QWidget()
    p = dashboard.SettingsPanel(host, lambda *_a: None, lambda *_a: None)
    p.resize(566, 620)
    p.show()
    p._nav_group.button(4).click()          # Notifications — where it showed
    for _ in range(6):
        app.processEvents()
    yield p, app
    p.deleteLater()
    host.deleteLater()
    app.processEvents()
    app.setStyleSheet(prev_sheet)
    if theme.active_name() != prev_theme:
        theme.set_active(prev_theme)


def _overflows(panel):
    """Visible widgets whose bottom edge falls outside their parent."""
    bad = []
    for w in panel.findChildren(QWidget):
        parent = w.parentWidget()
        # isVisible() walks the ancestor chain: a child of a hidden container is
        # never laid out, so its geometry means nothing.
        if not w.isVisible() or parent is None or parent.height() <= 0:
            continue
        if w.height() <= 0 or parent.layout() is None:
            continue
        over = (w.y() + w.height()) - parent.height()
        if over > 0:
            bad.append((w, parent, over))
    return bad


def test_no_widget_hangs_over_its_container(panel):
    p, _app = panel
    bad = _overflows(p)
    assert not bad, "\n".join(
        f"{type(w).__name__} {getattr(w, 'text', lambda: '')()!r:40s} "
        f"overflows {parent.objectName() or type(parent).__name__} by {over}px"
        for w, parent, over in bad)


def test_the_notify_box_has_room_below_its_last_child(panel):
    """The specific container that failed, named so a regression is obvious."""
    p, _app = panel
    box = p.notify_how_box
    assert box.layout().contentsMargins().bottom() > 0, (
        "notify_how_box lost its bottom margin; its last child will be clipped")
    last = p.notify_push_check
    assert last.y() + last.height() <= box.height(), (
        f"{last.text()!r} ends at {last.y() + last.height()} inside a "
        f"{box.height()}px box")
