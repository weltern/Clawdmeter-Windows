"""Headless tests for live theme application (dashboard.apply_theme).

Switching a theme must restyle existing widgets in place and refresh the
module colour caches used by the custom-painted widgets — no restart. Runs via
QT_QPA_PLATFORM=offscreen. Never writes the real registry (set_theme is stubbed).

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402
import session_shelf  # noqa: E402
import statviz  # noqa: E402
import theme  # noqa: E402

_app = QApplication.instance() or QApplication([])
# Tests must never persist a theme into HKCU.
app_settings.set_theme = lambda name: None


def _reset():
    dashboard.apply_theme(theme.DEFAULT_NAME)


def test_apply_updates_base_stylesheet_and_active():
    try:
        base_before = dashboard.STYLESHEET
        dashboard.apply_theme("Dracula")
        assert theme.active_name() == "Dracula"
        assert dashboard.STYLESHEET != base_before
        assert dashboard.STYLESHEET == theme.build_qss(theme.get("Dracula"))
    finally:
        _reset()


def test_apply_restyles_existing_widgets_in_place():
    w = QWidget()
    try:
        dashboard.apply_theme(theme.DEFAULT_NAME)
        w.setStyleSheet(dashboard.STYLESHEET)   # a widget carrying the base sheet
        dashboard.apply_theme("Nord")
        # The tree-walk swapped the old base sheet for the new one on this widget.
        assert w.styleSheet() == dashboard.STYLESHEET
        assert "#88c0d0" in w.styleSheet()      # Nord's frost accent
    finally:
        _reset()
        w.deleteLater()


def test_apply_refreshes_custom_paint_caches():
    try:
        dashboard.apply_theme("Gruvbox")
        gruv = theme.get("Gruvbox")
        assert statviz._ACCENT.name().lower() == gruv.accent.lower()
        assert gruv.bg.lower() in session_shelf.SHELF_STYLESHEET.lower()
        # The usage-bar fill tracks the accent off the default theme.
        assert session_shelf._BAR_HEAT["cool"] == gruv.accent
        assert session_shelf._BAR_OVERAGE == gruv.danger
    finally:
        _reset()


def test_default_bar_ramp_is_preserved_exactly():
    try:
        dashboard.apply_theme("Midnight Salmon")
        assert session_shelf._BAR_HEAT == {
            "cool": "#CE7D6B", "warm": "#B85C42", "hot": "#8B2E1A"}
        assert session_shelf._BAR_OVERAGE == "#A50F1A"
    finally:
        _reset()


def test_apply_every_preset_without_error():
    try:
        for name in theme.names():
            dashboard.apply_theme(name)
            assert theme.active_name() == name
    finally:
        _reset()


def test_apply_follow_system_resolves_and_remembers_selection():
    try:
        dashboard.apply_theme(theme.SYSTEM)
        assert theme.selected() == theme.SYSTEM
        # Resolves to a real dark/light preset per the OS scheme.
        assert theme.active_name() in (theme.SYSTEM_DARK, theme.SYSTEM_LIGHT)
    finally:
        _reset()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
