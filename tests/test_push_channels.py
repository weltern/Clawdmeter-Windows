"""Unit tests for the multi-channel push model: the added-channels list +
migration (app_settings) and the send-to-all dispatch (dashboard).

Importing dashboard pulls in PySide6, so run headless via QT_QPA_PLATFORM=
offscreen. Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QSettings  # noqa: E402

import app_settings  # noqa: E402
import dashboard  # noqa: E402
import remote_notify  # noqa: E402


def _store(tmp_path, monkeypatch):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    monkeypatch.setattr(app_settings, "_settings", lambda: s)
    return s


def test_channels_round_trip_validates_and_dedupes(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    assert app_settings.get_reset_notify_push_channels() == []  # fresh, none configured
    app_settings.set_reset_notify_push_channels(["discord", "ntfy", "bogus", "discord"])
    assert app_settings.get_reset_notify_push_channels() == ["discord", "ntfy"]


def test_channels_migration_seeds_from_configured(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    # A value saved before the channels list existed -> seeded as an added channel.
    app_settings.set_reset_notify_push_discord("https://discord.com/api/webhooks/1/x")
    assert app_settings.get_reset_notify_push_channels() == ["discord"]


def test_push_channel_configured(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    assert not app_settings.push_channel_configured("telegram")
    app_settings.set_reset_notify_push_tg_token("tok")
    assert not app_settings.push_channel_configured("telegram")  # needs chat too
    app_settings.set_reset_notify_push_tg_chat("chat")
    assert app_settings.push_channel_configured("telegram")


def test_new_channels_configured(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    # single-field channels
    assert not app_settings.push_channel_configured("slack")
    app_settings.set_reset_notify_push_slack("https://hooks.slack.com/x")
    assert app_settings.push_channel_configured("slack")
    app_settings.set_reset_notify_push_webhook("https://example.com/h")
    assert app_settings.push_channel_configured("webhook")
    # two-field channels need both
    app_settings.set_reset_notify_push_po_token("tok")
    assert not app_settings.push_channel_configured("pushover")
    app_settings.set_reset_notify_push_po_user("usr")
    assert app_settings.push_channel_configured("pushover")
    app_settings.set_reset_notify_push_gotify_url("https://g.example.com")
    assert not app_settings.push_channel_configured("gotify")
    app_settings.set_reset_notify_push_gotify_token("tok")
    assert app_settings.push_channel_configured("gotify")


def test_dispatch_sends_to_all_configured(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    app_settings.set_reset_notify_push_channels(["ntfy", "discord", "telegram"])
    app_settings.set_reset_notify_push_topic("topic")
    app_settings.set_reset_notify_push_discord("https://discord.com/api/webhooks/1/x")
    # telegram is added but NOT configured -> skipped.
    calls = []
    monkeypatch.setattr(remote_notify, "send_ntfy",
                        lambda *a, **k: (calls.append("ntfy") or (True, "sent")))
    monkeypatch.setattr(remote_notify, "send_discord",
                        lambda *a, **k: (calls.append("discord") or (True, "sent")))
    ok, msg = dashboard._dispatch_push("T", "B")
    assert ok and calls == ["ntfy", "discord"]
    assert "ntfy" in msg and "Discord" in msg


def test_dispatch_reports_partial_failure(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    app_settings.set_reset_notify_push_channels(["ntfy", "discord"])
    app_settings.set_reset_notify_push_topic("topic")
    app_settings.set_reset_notify_push_discord("https://discord.com/api/webhooks/1/x")
    monkeypatch.setattr(remote_notify, "send_ntfy", lambda *a, **k: (True, "sent"))
    monkeypatch.setattr(remote_notify, "send_discord", lambda *a, **k: (False, "boom"))
    ok, msg = dashboard._dispatch_push("T", "B")
    assert not ok
    assert "ntfy" in msg and "Discord" in msg and "boom" in msg


def test_dispatch_no_channels_is_safe(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    ok, msg = dashboard._dispatch_push("T", "B")
    assert not ok and "no push channels" in msg


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
