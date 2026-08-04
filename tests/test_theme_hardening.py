"""Regression tests for the v3 theme-hardening fixes (2026-07-24 code review).

Themes are a shareable, importable feature, so a theme file / persisted value is
untrusted input. Each test here FAILS on the pre-fix code, so it actually guards
the fix rather than just asserting current behaviour:

  * parse_custom must never raise on a hostile file (deeply-nested JSON ->
    RecursionError; over-size file) -- it must return None.
  * derive_palette must floor the *primary* text role to WCAG AA, not only the
    derived secondary roles, so an imported theme can't make titles invisible.
  * set_custom_base must sanitize a malformed hex value so a corrupt persisted
    base can't crash derive_palette's colour math at startup.

No Qt needed -- theme.py is pure Python.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import theme  # noqa: E402
from theme import CUSTOM_ROLES, MIDNIGHT_SALMON  # noqa: E402


def _base(**overrides) -> dict:
    """A complete, valid custom base with optional per-role overrides."""
    base = {r: getattr(MIDNIGHT_SALMON, r) for r in CUSTOM_ROLES}
    base.update(overrides)
    return base


# --- parse_custom: untrusted input must never raise --------------------------

def test_parse_custom_rejects_deeply_nested_json_without_raising():
    # Pre-fix: json.loads raises RecursionError (a RuntimeError subclass) that
    # parse_custom's (ValueError, TypeError) except did NOT catch -> crash.
    hostile = "[" * 20000 + "]" * 20000
    assert len(hostile) < theme.MAX_THEME_BYTES      # exercises the except, not the size guard
    assert theme.parse_custom(hostile) is None


def test_parse_custom_rejects_oversize_input():
    assert theme.parse_custom("a" * (theme.MAX_THEME_BYTES + 1)) is None


def test_parse_custom_still_accepts_a_valid_theme():
    good = theme.serialize_custom(_base())
    parsed = theme.parse_custom(good)
    assert parsed is not None
    assert all(parsed[r] == getattr(MIDNIGHT_SALMON, r) for r in CUSTOM_ROLES)


# --- derive_palette: primary text has a hard contrast floor ------------------

def test_derive_palette_floors_primary_text_contrast():
    # text == bg would be invisible (contrast 1.0) pre-fix; must be clamped to AA.
    pal = theme.derive_palette(_base(text="#101010", bg="#101010"))
    assert theme.contrast(pal.text, pal.bg) >= 4.5 - 1e-9


def test_derive_palette_leaves_a_readable_text_untouched():
    pal = theme.derive_palette(_base(text="#ffffff", bg="#101010"))
    assert pal.text == "#ffffff"      # already AA -> ensure_contrast returns it as-is


# --- set_custom_base: a corrupt persisted base can't crash colour math -------

def test_set_custom_base_sanitizes_a_malformed_hex():
    theme.set_custom_base(_base(bg="red", text="not-a-colour"))
    stored = theme.custom_base()
    assert stored["bg"] == getattr(MIDNIGHT_SALMON, "bg")       # fell back to default
    assert stored["text"] == getattr(MIDNIGHT_SALMON, "text")
    theme.custom_palette()            # must not raise on the sanitized base


def test_set_custom_base_ignores_a_non_dict():
    theme.set_custom_base("garbage")  # type: ignore[arg-type]
    stored = theme.custom_base()
    assert all(stored[r] == getattr(MIDNIGHT_SALMON, r) for r in CUSTOM_ROLES)
