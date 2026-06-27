"""Offscreen tests for the tabbed settings panel.

Builds a SettingsPanel and asserts the full-width tab refactor: five tabs in
the expected order, every section routed onto the tab that owns its concern,
and the prominent close affordance present. Runs headless via
QT_QPA_PLATFORM=offscreen so it works in CI with no display.

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_settings_tabs.py`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from dashboard import SettingsPanel  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _panel() -> SettingsPanel:
    # Top-level (parent=None) so the returned panel owns itself and survives the
    # call. Settings is now a stacked page (no overlay/close), so the two
    # required callbacks are all it needs.
    noop = lambda *a, **k: None
    return SettingsPanel(None, noop, noop)


def test_five_tabs_in_order():
    sp = _panel()
    assert sp._stack.count() == 5
    assert len(sp._nav_group.buttons()) == 5
    labels = [sp._nav_group.button(i).text() for i in range(5)]
    assert [t.split()[-1] for t in labels] == [
        "General", "Display", "Connection", "Notifications", "About",
    ]
    # First tab is selected by default.
    assert sp._nav_group.button(0).isChecked()


def test_every_page_is_populated():
    sp = _panel()
    for i in range(sp._stack.count()):
        body = sp._stack.widget(i).widget()
        assert body.findChildren(QWidget), f"tab {i} has no widgets"


def test_sections_routed_to_expected_tabs():
    sp = _panel()
    page = {  # tab index by concern
        "general": sp._stack.widget(0),
        "display": sp._stack.widget(1),
        "connection": sp._stack.widget(2),
        "notifications": sp._stack.widget(3),
    }
    expected = {
        "general": [sp.aot_check, sp.auto_hide_check, sp.quit_on_close_check,
                    sp.startup_check, sp.auto_check_updates_check, sp.start_btn],
        "display": [sp.multi_sessions_check, sp.subagents_check, sp.token_usage_check],
        "connection": [sp.cred_btn, sp.auto_refresh_check, sp.refresh_token_btn,
                       sp.poll_interval_edit, sp.idle_backoff_check,
                       sp.idle_after_spin, sp.idle_interval_spin],
        "notifications": [sp.notify_check, sp.approaching_check,
                          sp.session_pct_slider, sp.session_pct_field,
                          sp.weekly_pct_slider, sp.weekly_pct_field, sp.overage_check,
                          sp.notify_toast_check, sp.notify_push_check,
                          sp.notify_push_add_btn],
    }
    for tab, widgets in expected.items():
        for w in widgets:
            assert page[tab].isAncestorOf(w), f"{w.objectName() or w} not on {tab} tab"


def _set_silently(checkbox, on):
    # Flip a checkbox WITHOUT firing its toggled handler, so the test never
    # persists a setting (e.g. enabling approaching alerts) into HKCU.
    checkbox.blockSignals(True)
    checkbox.setChecked(on)
    checkbox.blockSignals(False)


def test_approaching_threshold_box_follows_its_master():
    sp = _panel()
    _set_silently(sp.approaching_check, True)
    sp._sync_notify_subtoggles()
    assert not sp.approaching_box.isHidden()
    _set_silently(sp.approaching_check, False)
    sp._sync_notify_subtoggles()
    assert sp.approaching_box.isHidden()


def test_shared_channels_show_for_any_alert_and_hide_when_all_off():
    sp = _panel()
    # Reset on, approaching off -> channels shown.
    _set_silently(sp.notify_check, True)
    _set_silently(sp.approaching_check, False)
    sp._sync_notify_subtoggles()
    assert not sp.notify_how_box.isHidden()
    # Only approaching on -> still shown (channels are shared).
    _set_silently(sp.notify_check, False)
    _set_silently(sp.approaching_check, True)
    sp._sync_notify_subtoggles()
    assert not sp.notify_how_box.isHidden()
    # Everything off -> channels hidden as a unit.
    _set_silently(sp.approaching_check, False)
    sp._sync_notify_subtoggles()
    assert sp.notify_how_box.isHidden()


def test_threshold_slider_and_field_stay_in_sync():
    import app_settings
    # Swap the setter to a no-op so driving the widgets never writes real settings.
    real = app_settings.set_approaching_session_pct
    app_settings.set_approaching_session_pct = lambda v: None
    try:
        sp = _panel()
        sp.session_pct_slider.setValue(72)
        assert sp.session_pct_field.value() == 72   # slider -> field
        sp.session_pct_field.setValue(64)
        assert sp.session_pct_slider.value() == 64   # field -> slider (no loop)
    finally:
        app_settings.set_approaching_session_pct = real


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
