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
app_settings.set_custom_base = lambda base: None
app_settings.set_system_targets = lambda *a: None


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


def test_scrolling_labels_follow_theme_on_switch():
    # Regression: the session-tile name (role="text") and "working on" line
    # (role="muted") must re-read the palette on a switch, not keep the colour
    # captured at construction (which made the name invisible on light themes).
    name = session_shelf.ScrollingLabel()               # role="text" (default)
    sub = session_shelf.ScrollingLabel(role="muted")
    try:
        dashboard.apply_theme("Daybreak")               # walk refreshes both
        assert name._color.name().lower() == theme.get("Daybreak").text.lower()
        assert sub._color.name().lower() == theme.get("Daybreak").text_dim.lower()
        dashboard.apply_theme("Nord")
        assert name._color.name().lower() == theme.get("Nord").text.lower()
        assert sub._color.name().lower() == theme.get("Nord").text_dim.lower()
    finally:
        _reset()
        name.deleteLater()
        sub.deleteLater()


def test_apply_custom_theme_uses_derived_palette():
    try:
        theme.set_custom_base(theme.custom_base_from(theme.get("Dracula")))
        dashboard.apply_theme(theme.CUSTOM)
        assert theme.selected() == theme.CUSTOM
        assert theme.active_name() == theme.CUSTOM
        assert theme.active().accent == theme.get("Dracula").accent   # base flows through
    finally:
        theme.set_custom_base(theme.custom_base_from(theme.MIDNIGHT_SALMON))
        _reset()


def test_custom_editor_previews_then_applies():
    seed = theme.custom_base_from(theme.MIDNIGHT_SALMON)
    seed["bg"] = "#ffffff"
    ed = dashboard.CustomThemeEditor(seed)
    try:
        ed._select_role("accent")
        ed._on_color("#dddddd")          # working copy only — app NOT changed yet
        assert ed._working["accent"] == "#dddddd"
        assert theme.active().accent != "#dddddd"   # app untouched until Apply
        ed._apply()                      # commit to the whole app
        assert theme.selected() == theme.CUSTOM
        assert theme.active().accent == "#dddddd"
        ed._fix_contrast()               # fixes the working copy
        ed._apply()
        assert theme.contrast(theme.custom_base()["accent"],
                              theme.custom_base()["bg"]) >= 4.5
    finally:
        theme.set_custom_base(theme.custom_base_from(theme.MIDNIGHT_SALMON))
        _reset()
        ed.deleteLater()


def test_macos_radius_windows_still_follow_theme_switch(monkeypatch):
    # Regression (macOS-only, found on the M2): the mini/compact windows append a
    # `border-radius` rule to their stylesheet for the native rounded HUD look, so
    # apply_theme's exact-match swap (`sheet == old`) never matches them. The
    # explicit re-theme hook that was meant to cover that lived on SettingsPanel
    # but read `self.mini`/`self.compact_view` — Dashboard attributes — behind
    # hasattr guards, so it was a silent no-op and the two views kept the old
    # palette's colours on every live switch. apply_theme now broadcasts
    # apply_theme_style() during its widget walk, so no entry point can miss it.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    monkeypatch.setattr(session_shelf.sys, "platform", "darwin")
    dashboard.apply_theme("Nord")
    mini = dashboard.MiniWidget()
    compact = session_shelf.CompactView()
    try:
        assert "border-radius:13px" in mini.styleSheet()      # the append is on
        assert "border-radius:13px" in compact.styleSheet()

        dashboard.apply_theme("Daybreak")
        day = theme.get("Daybreak")
        # Rebuilt from the NEW palette, and still rounded.
        assert mini.styleSheet() == (
            dashboard.STYLESHEET + "\nQWidget#miniRoot{border-radius:13px}")
        assert compact.styleSheet() == (
            session_shelf.COMPACT_STYLESHEET
            + "\nQWidget#compactRoot{border-radius:13px}")
        assert day.bg.lower() in mini.styleSheet().lower()
        assert day.bg.lower() in compact.styleSheet().lower()
    finally:
        _reset()
        mini.deleteLater()
        compact.deleteLater()


def test_custom_editor_rounds_and_follows_theme_on_macos(monkeypatch):
    # Regression: round_window() masks the dialog's content layer to
    # MACOS_RADIUS, so the QSS must round #root's 1px border to the same value
    # or it is drawn square and clipped at each corner. And because that append
    # breaks apply_theme's exact-match swap, the dialog needs the same
    # apply_theme_style() broadcast hook the mini/compact windows got.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    dashboard.apply_theme("Nord")
    ed = dashboard.CustomThemeEditor(theme.custom_base_from(theme.MIDNIGHT_SALMON))
    try:
        radius = f"border-radius:{dashboard.CustomThemeEditor.MACOS_RADIUS}px"
        assert radius in ed.styleSheet()
        dashboard.apply_theme("Daybreak")
        assert ed.styleSheet() == (
            dashboard.STYLESHEET + f"\nQWidget#root{{{radius}}}")
        assert theme.get("Daybreak").bg.lower() in ed.styleSheet().lower()
    finally:
        theme.set_custom_base(theme.custom_base_from(theme.MIDNIGHT_SALMON))
        _reset()
        ed.deleteLater()


def test_set_topmost_on_macos_never_touches_qt_window_flags(monkeypatch):
    # Regression: Always-on-top used to flip Qt.WindowStaysOnTopHint, which makes
    # Qt tear down and rebuild the NSWindow — silently discarding the transparent
    # titlebar / full-size content view from macos_window.style(). The window came
    # back as stock chrome for the rest of the session. On macOS we now set the
    # NSWindow level in place instead, so the native window is never rebuilt.
    import winutil

    touched, levels = [], []
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    monkeypatch.setattr(winutil.macos_window, "set_level",
                        lambda w, on: levels.append(on) or True)

    class _W:
        def setWindowFlag(self, *a):
            touched.append(a)          # must never happen on macOS

        def isVisible(self):
            return True

    winutil.set_topmost(_W(), True)
    assert levels == [True]
    assert touched == [], "Qt window flag toggled — this rebuilds the NSWindow"


def test_set_topmost_falls_back_to_qt_flag_without_pyobjc(monkeypatch):
    # Linux, or macOS without pyobjc: set_level returns False and we must still
    # fall back to the portable flag-toggle path.
    import winutil

    touched = []
    monkeypatch.setattr(winutil, "is_windows", lambda: False)
    monkeypatch.setattr(winutil.macos_window, "set_level", lambda w, on: False)

    class _W:
        def setWindowFlag(self, *a):
            touched.append(a)

        def isVisible(self):
            return False           # short-circuits before the re-show

        def geometry(self):
            return None            # captured before the early return

    winutil.set_topmost(_W(), True)
    assert len(touched) == 1


def test_auto_hide_titlebar_is_forced_off_on_macos(monkeypatch):
    # macOS draws the traffic lights in the NSWindow titlebar region, not in our
    # TitleBar widget — collapsing the widget to 0 strands them over the content.
    # _apply_auto_hide is the single gate, so a value persisted on Windows and
    # synced to a Mac must still come up disabled.
    monkeypatch.setattr(dashboard, "AUTO_HIDE_SUPPORTED", False)
    applied = []

    class _D:
        _auto_hide_enabled = False

        def __getattr__(self, name):        # any collaborator it would touch
            applied.append(name)
            raise AssertionError(f"auto-hide ran on macOS (touched {name!r})")

    # on=True must be squashed to the current value and return before doing work.
    dashboard.Dashboard._apply_auto_hide(_D(), True)
    assert applied == []


def test_macos_set_level_noops_off_darwin(monkeypatch):
    import macos_window
    monkeypatch.setattr(macos_window.sys, "platform", "win32")
    assert macos_window.set_level(object(), True) is False


def test_dashboard_repaints_native_window_bg_on_theme_switch():
    # The NSWindow background is painted from theme.active().bg_deep at show
    # time; without this hook a live theme switch left the old colour behind
    # until the next minimize/zoom.
    assert hasattr(dashboard.Dashboard, "apply_theme_style")
    calls = []
    monkey = type("W", (), {"apply_theme_style": dashboard.Dashboard.apply_theme_style})()
    monkey_style = dashboard.macos_window.style
    dashboard.macos_window.style = lambda w, bg=None: calls.append(bg) or True
    old_platform = dashboard.sys.platform
    try:
        dashboard.sys.platform = "darwin"
        dashboard.apply_theme("Nord")
        monkey.apply_theme_style()
        assert calls == [theme.get("Nord").bg_deep]
    finally:
        dashboard.sys.platform = old_platform
        dashboard.macos_window.style = monkey_style
        _reset()


def test_settings_panel_theme_hook_reaches_the_dashboard():
    # The panel's hook must delegate to the window that actually owns the shelf
    # state; the old version silently did nothing when those attributes were
    # missing from `self`.
    assert hasattr(dashboard.Dashboard, "refresh_dynamic_theme_colors")
    panel = dashboard.SettingsPanel.__new__(dashboard.SettingsPanel)
    calls = []

    class _Win:
        def refresh_dynamic_theme_colors(self):
            calls.append(True)

    panel.window = lambda: _Win()
    dashboard.SettingsPanel._refresh_dynamic_theme_colors(panel)
    assert calls == [True]


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
