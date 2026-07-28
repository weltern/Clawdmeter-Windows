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
        # Overage is danger_strong, not danger: the sub-100% "hot" band is
        # already danger, so sharing it made 95% and 105% paint identically.
        assert session_shelf._BAR_OVERAGE == gruv.danger_strong
        assert session_shelf._BAR_OVERAGE != session_shelf._BAR_HEAT["hot"]
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


def test_popup_menus_are_styled_and_follow_the_theme():
    # Regression (macOS): an unstyled QMenu has no background of its own and
    # renders translucent — the "+ Add a channel" list was see-through over the
    # button behind it. Every QMenu in the app relies on this one rule.
    for name in ("Midnight Salmon", "Nord", "Daybreak"):
        p = theme.get(name)
        qss = theme.build_qss(p)
        assert "QMenu {" in qss, f"{name}: QMenu unstyled — it will be transparent"
        block = qss.split("QMenu {", 1)[1].split("}", 1)[0]
        assert p.surface.lower() in block.lower(), (
            f"{name}: QMenu background is not the palette's surface, so it "
            "will not re-theme"
        )
        assert "QMenu::item:selected" in qss, f"{name}: no hover state"


def test_the_popup_replacement_is_macos_only(monkeypatch):
    # The custom popup exists solely because macOS paints a QMenu's panel with a
    # vibrancy material that ignores the stylesheet. Windows and Linux render
    # menus correctly, and swapping the native widget there would trade working
    # arrow-key navigation and accessibility for nothing.
    import uiutil
    monkeypatch.setattr(uiutil.sys, "platform", "darwin")
    assert type(uiutil.make_popup()).__name__ == "ThemedPopup"
    for plat in ("win32", "linux"):
        monkeypatch.setattr(uiutil.sys, "platform", plat)
        assert type(uiutil.make_popup()).__name__ == "_MenuPopup", plat


def test_both_popup_kinds_share_the_same_api(monkeypatch):
    # The call sites must not care which they got.
    import uiutil
    picked = []
    for plat in ("darwin", "win32"):
        monkeypatch.setattr(uiutil.sys, "platform", plat)
        popup = uiutil.make_popup()
        for meth in ("set_items", "popup_at", "popup_under", "hide"):
            assert hasattr(popup, meth), f"{plat}: missing {meth}"
        popup.set_items([("Quit", lambda: picked.append(plat))])


def test_macos_combo_popup_is_substituted_but_selection_still_works(monkeypatch):
    # macOS draws a combo's popup container with the same vibrancy material as a
    # menu panel, so it renders translucent. The combo itself is untouched — only
    # the popup is swapped — so currentText/currentIndexChanged keep working.
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    c = dashboard._ThemedCombo()
    try:
        c.addItems(["Nord", "Dracula", "Gruvbox"])
        changed = []
        c.currentIndexChanged.connect(lambda i: changed.append(i))
        c.showPopup()
        assert type(c._mac_popup).__name__ == "ThemedPopup"
        assert [b.text() for b in c._mac_popup._buttons] == [
            "Nord", "Dracula", "Gruvbox"]
        c._mac_popup._buttons[1].click()
        assert c.currentText() == "Dracula"
        assert changed == [1], "the combo must still emit its signal"
    finally:
        c.deleteLater()


def test_off_macos_the_combo_keeps_its_native_popup(monkeypatch):
    monkeypatch.setattr(dashboard.sys, "platform", "win32")
    c = dashboard._ThemedCombo()
    try:
        c.addItems(["Nord"])
        c.showPopup()
        assert getattr(c, "_mac_popup", None) is None, (
            "Windows/Linux combo popups render fine and must not be replaced"
        )
        c.hidePopup()
    finally:
        c.deleteLater()


def test_the_macos_combo_popup_keeps_the_preset_swatches(monkeypatch):
    # The swatches live as QIcons on the combo's items; the substituted popup
    # must carry them through or the theme picker loses its previews.
    from PySide6.QtCore import QSize
    monkeypatch.setattr(dashboard.sys, "platform", "darwin")
    c = dashboard._ThemedCombo()
    try:
        c.setIconSize(QSize(38, 14))
        for n in ("Nord", "Dracula"):
            c.addItem(dashboard._PresetRow._swatch_icon(n), n)
        c.showPopup()
        for b in c._mac_popup._buttons:
            assert not b.icon().isNull(), f"{b.text()} lost its swatch"
            assert b.iconSize() == QSize(38, 14)
    finally:
        c.deleteLater()


# --- the three theming gaps found on Linux/Windows ---------------------------

def test_a_menu_popup_restyles_itself_before_showing():
    """A QMenu inherits its look from an ancestor's stylesheet, and Qt does not
    repolish a popup when that sheet is swapped. Built under a dark theme, the
    "+ Add a channel" list kept a dark panel after switching to a light one —
    only the text followed, because that is redrawn from the palette."""
    import uiutil
    m = uiutil._MenuPopup()
    try:
        dashboard.apply_theme("Midnight Salmon")
        m._restyle()
        dark = m._menu.styleSheet()
        dashboard.apply_theme("Riptide Light")
        m._restyle()
        light = m._menu.styleSheet()
        assert dark and light and dark != light, "the menu kept its old sheet"
        assert theme.get("Riptide Light").surface in light
    finally:
        m._menu.deleteLater()


def test_both_popup_kinds_take_icons_and_an_icon_size():
    """They have to be interchangeable: the combo passes three-tuples plus an
    icon_size, and the QMenu wrapper used to accept neither — so substituting
    it on any platform would have raised on the first call."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon
    import uiutil
    for kind in (uiutil._MenuPopup, uiutil.ThemedPopup):
        obj = kind()
        obj.set_items([("plain", lambda: None),
                       ("with icon", lambda: None, QIcon())],
                      icon_size=QSize(38, 14))
        for method in ("set_items", "popup_at", "popup_under", "hide"):
            assert hasattr(obj, method), f"{kind.__name__} lacks {method}"


def test_the_push_channel_row_follows_a_theme_switch():
    """Its colours are inline in rich text, so they freeze at whatever palette
    was current when the row was last refreshed. On a light theme that left
    dark-theme greys on a light background and the channel name unreadable."""
    import re
    dashboard.apply_theme("Midnight Salmon")
    row = dashboard._PushChannelRow("discord", "Discord")
    try:
        before = re.findall(r"color:(#[0-9a-fA-F]{6})", row._summary.text())
        dashboard.apply_theme("Riptide Light")
        after = re.findall(r"color:(#[0-9a-fA-F]{6})", row._summary.text())
        assert before and after and before != after, (
            f"summary colours did not change with the theme: {before}")
        assert theme.get("Riptide Light").text in after, (
            "the channel name is not using the light theme's text colour")
    finally:
        row.deleteLater()


def test_the_combo_substitution_is_off_on_windows_only(monkeypatch):
    """macOS and Linux both need the replacement — macOS for the vibrancy
    panel, Linux because the platform style eats the hover highlight and leaves
    square corners behind a rounded view. Windows renders both correctly and
    keeps the native widget with its keyboard navigation."""
    import inspect
    src = inspect.getsource(dashboard._ThemedCombo.showPopup)
    assert 'sys.platform != "win32"' in src, (
        "the combo substitution should be gated off Windows, not onto darwin")
    assert "ThemedPopup(self)" in src, (
        "must construct ThemedPopup directly — make_popup returns the QMenu "
        "wrapper on Linux, which is the thing being replaced")


def test_the_channel_card_stays_visible_against_the_panel():
    """It used to be `bg` on a `bg_deep` panel — a 1.03-1.08 contrast ratio in
    every theme, so a configured channel had nothing separating it from the
    background. No fill fixes that: in the light presets bg_deep/bg/surface all
    sit within 1.07 of each other, so the border has to carry it."""
    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)))
        return (lb + 0.05) / (la + 0.05)

    import re
    for name in ("Midnight Salmon", "Daybreak", "Riptide Light", "Nord Light"):
        p = theme.get(name)
        qss = theme.build_qss(p)
        m = re.search(r"QWidget#pushCard \{([^}]*)\}", qss)
        assert m, "the pushCard rule moved"
        block = m.group(1)
        border = re.search(r"border:\s*1px solid (#[0-9a-fA-F]{6})", block).group(1)
        fill = re.search(r"background-color:\s*(#[0-9a-fA-F]{6})", block).group(1)
        assert ratio(border, fill) >= 1.5, (
            f"{name}: the card's border is {ratio(border, fill):.2f} against its "
            f"own fill — it will not read as a card")
        assert ratio(border, p.bg_deep) >= 1.4, (
            f"{name}: the card's border is {ratio(border, p.bg_deep):.2f} "
            f"against the panel behind it")


# --- ThemedPopup corner rendering on Linux -----------------------------------
# Regression guard for a real, user-reported Linux bug, confirmed from a native
# X framebuffer capture (scrot on Mint 22, so no remote-display encoding in the
# path): the drop-down's rounded corners were filled with pure (0,0,0) against a
# (238,241,245) settings panel. round_window() -- which is what makes the window
# non-opaque on macOS -- is a no-op off macOS, and nothing else cleared the
# popup's own window, so the card's border-radius simply revealed opaque black.
# It looked correct wherever the backdrop happened to be dark, which is why it
# read as "some corners square, some rounded".

def _fresh_popup(monkeypatch, *, platform, compositing):
    import uiutil
    monkeypatch.setattr(uiutil.sys, "platform", platform)
    monkeypatch.setattr(uiutil, "linux_compositing", lambda: compositing)
    return uiutil, uiutil.ThemedPopup()


def test_linux_popup_is_translucent_so_its_corners_are_not_black(monkeypatch):
    from PySide6.QtCore import Qt
    uiutil, popup = _fresh_popup(monkeypatch, platform="linux", compositing=True)
    try:
        assert popup.testAttribute(Qt.WA_TranslucentBackground), (
            "the popup rounds its card, so the window behind that radius must be "
            "transparent — opaque leaves black notches at the corners")
    finally:
        popup.deleteLater()


def test_without_a_compositor_the_popup_squares_its_corners(monkeypatch):
    from PySide6.QtCore import Qt
    uiutil, popup = _fresh_popup(monkeypatch, platform="linux", compositing=False)
    try:
        # Translucency needs a compositing manager; asking for it without one
        # brings the black corners straight back.
        assert not popup.testAttribute(Qt.WA_TranslucentBackground)
        assert "QWidget#popupRoot{border-radius:0}" in popup._card.styleSheet(), (
            "with no way to make the corners transparent they must be squared "
            "off, not left rounded over an opaque fill")
    finally:
        popup.deleteLater()


def test_the_other_platforms_are_untouched(monkeypatch):
    from PySide6.QtCore import Qt
    for platform in ("darwin", "win32"):
        uiutil, popup = _fresh_popup(
            monkeypatch, platform=platform, compositing=False)
        try:
            # macOS gets its transparency from round_window() on the native
            # window instead, and Windows never uses ThemedPopup for the combo.
            assert not popup.testAttribute(Qt.WA_TranslucentBackground), platform
            assert "border-radius:0}" not in popup._card.styleSheet(), platform
        finally:
            popup.deleteLater()


def test_compositing_probe_defaults_to_the_prettier_branch(monkeypatch):
    import uiutil
    uiutil.linux_compositing.cache_clear()
    try:
        # Wayland always composites, so it must not be probed via X11 at all.
        #
        # Asserting `is True` alone would prove nothing here: the Wayland
        # branch and the give-up fallback BOTH return True, so the test could
        # not tell which ran. Instead spy on find_library -- if the Wayland
        # short-circuit works, the X11 probe is never even looked up.
        monkeypatch.setattr(uiutil.sys, "platform", "linux")
        monkeypatch.setattr(uiutil, "is_wayland", lambda: True)
        probed = []
        monkeypatch.setattr("ctypes.util.find_library",
                            lambda n: probed.append(n))
        assert uiutil.linux_compositing() is True
        assert probed == [], \
            "Wayland short-circuit failed: it went looking for libX11 anyway"
        uiutil.linux_compositing.cache_clear()
        # And a probe that cannot run answers True rather than squaring corners
        # on every desktop that mainstream users actually have.
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.setattr(
            "ctypes.util.find_library", lambda _n: (_ for _ in ()).throw(OSError()))
        assert uiutil.linux_compositing() is True
    finally:
        uiutil.linux_compositing.cache_clear()
