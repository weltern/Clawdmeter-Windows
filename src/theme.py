"""Central colour palette + stylesheet builder for Clawdmeter.

Phase 1 of the v3 theming work. Every *chrome* colour the app draws — window
fills, surfaces, borders, text, the salmon accent and the semantic status
colours — now lives in one `Palette`. The main-window QSS and the
custom-painted widgets (statviz, session_shelf) build their colours from it.

This module is a *faithful* extraction: ``MIDNIGHT_SALMON`` holds the exact
hexes the app shipped with, and ``build_qss(MIDNIGHT_SALMON)`` reproduces the
old hardcoded stylesheet byte-for-byte, so wiring it in is a provable no-op.
Presets, light mode and custom themes build on top of this in later phases.

Deliberately NOT here (these encode *data*, not chrome, and stay fixed across
every theme): the per-language colours and per-activity mascot glows
(``transcript.ACTIVITY_COLORS`` / ``LANGUAGE_COLORS``), the usage heat ramp and
the overage red (``session_shelf._BAR_HEAT`` / ``_BAR_OVERAGE``), and the model
breakdown palette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace as _replace

__all__ = ["Palette", "MIDNIGHT_SALMON", "build_qss", "active"]


@dataclass(frozen=True)
class Palette:
    """The app's chrome colours, one field per role. Values are ``#rrggbb``.

    The custom-theme editor (Phase 4) will expose only the core roles and
    *derive* the shade variants (``bg_deep`` etc.), but the presets specify
    every field explicitly so they can be hand-tuned.
    """

    # Backgrounds — window fills, base to deepest.
    bg: str             # base window background
    bg_deep: str        # title bar / settings panel (a shade under bg)
    bg_deepest: str     # compact-view title bar
    # Surfaces — raised fills: cards, buttons, hovers, dividers.
    surface: str        # cards, buttons, button:hover, dividers, bar track
    surface_dim: str    # disabled fills, compact-row hover, empty cells
    surface_sunken: str # disabled-control border
    # Lines.
    border: str         # default 1px borders, input/checkbox outlines
    border_dim: str     # disabled text, scrollbar-handle hover
    # Text — three legibility levels.
    text: str           # primary text
    text_dim: str       # secondary labels
    text_muted: str     # faint captions / section labels
    # Accent — the brand salmon (locked on presets; editable only in custom).
    accent: str         # brand accent
    accent_hover: str   # accent hover/active (a lightened accent)
    # Status — semantic; always reinforced by an icon/label elsewhere so they
    # read even for colour-blind users.
    warn: str           # approaching-limit amber
    danger: str         # over-limit / error text
    danger_strong: str  # destructive-hover fill, negative delta
    positive: str       # good / positive delta

    def with_overrides(self, **changes: str) -> "Palette":
        """Return a copy with some fields replaced (used by later phases)."""
        return _replace(self, **changes)


# The default theme — the exact colours the app shipped with pre-v3.
MIDNIGHT_SALMON = Palette(
    bg="#0e1116",
    bg_deep="#0a0d12",
    bg_deepest="#0b0e13",
    surface="#1f2937",
    surface_dim="#161b22",
    surface_sunken="#21262d",
    border="#374151",
    border_dim="#4b5563",
    text="#e6edf3",
    text_dim="#9ca3af",
    text_muted="#6b7280",
    accent="#CE7D6B",
    accent_hover="#d98f7e",
    warn="#f59e0b",
    danger="#dc2626",
    danger_strong="#c13434",
    positive="#5FB3A1",
)


# Maps every hex that appears in the base stylesheet to the Palette field it
# plays. Pure white (#ffffff — text on the destructive-hover red) is left as a
# literal in the QSS: it's universal across light/dark and isn't a theme role.
_QSS_HEX_TO_FIELD = {
    "#0e1116": "bg",
    "#0a0d12": "bg_deep",
    "#1f2937": "surface",
    "#161b22": "surface_dim",
    "#21262d": "surface_sunken",
    "#374151": "border",
    "#4b5563": "border_dim",
    "#e6edf3": "text",
    "#9ca3af": "text_dim",
    "#6b7280": "text_muted",
    "#CE7D6B": "accent",
    "#d98f7e": "accent_hover",
    "#f59e0b": "warn",
    "#dc2626": "danger",
    "#c13434": "danger_strong",
}

# Case-sensitive, single-pass swap. One regex pass (not sequential .replace)
# so a role whose new value equals another role's original hex can't be
# double-substituted (aliasing).
_HEX_RE = re.compile("|".join(re.escape(h) for h in _QSS_HEX_TO_FIELD))


# The main-window stylesheet, verbatim as it shipped. build_qss() swaps the
# palette hexes; with MIDNIGHT_SALMON every swap is hex->same-hex, i.e. a no-op.
_BASE_QSS = """
QWidget#root {
    background-color: #0e1116;
    border: 1px solid #1f2937;
}

QWidget#titleBar { background-color: #0a0d12; }
QLabel#titleAppName {
    font-size: 12px; color: #e6edf3; font-weight: 600; letter-spacing: 1px;
}
QToolButton#titleBtn, QToolButton#closeBtn, QToolButton#settingsBtn {
    background: transparent; color: #CE7D6B; border: 0;
    min-width: 38px; min-height: 30px;
    font-family: "Font Awesome 6 Free"; font-weight: 900;
}
QToolButton#titleBtn, QToolButton#closeBtn { font-size: 13px; }
QToolButton#settingsBtn { font-size: 15px; }
QToolButton#titleBtn:hover, QToolButton#settingsBtn:hover { background-color: #1f2937; color: #CE7D6B; }
QToolButton#closeBtn:hover { background-color: #c13434; color: #ffffff; }

QLabel#title { font-size: 22px; font-weight: 700; letter-spacing: 1px; color: #e6edf3; }
QLabel#group { font-size: 13px; font-weight: 600; color: #9ca3af; letter-spacing: 2px; }
QLabel#rowLabel { font-size: 14px; color: #9ca3af; }
QLabel#pct { font-size: 40px; font-weight: 700; color: #e6edf3; }
QLabel#reset { font-size: 12px; color: #9ca3af; }
QLabel#statusText { font-size: 12px; font-weight: 600; }
QLabel#statusText[level="warn"] { color: #f59e0b; }
QLabel#statusText[level="block"] { color: #dc2626; }
QLabel#statusIcon { font-size: 14px; font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif; }

QPushButton {
    background-color: #1f2937; color: #e6edf3; border: 1px solid #374151;
    padding: 6px 12px; border-radius: 6px;
}
QPushButton:hover { background-color: #374151; }
QPushButton:disabled { background-color: #161b22; color: #4b5563; border-color: #21262d; }

QWidget#settingsPanel {
    background-color: #0a0d12;
}
/* Left tab rail in the settings page (sits right of the app nav rail). */
QWidget#settingsNav {
    background-color: #0e1116;
    border-right: 1px solid #1f2937;
}
/* QPushButton (not QToolButton) so QSS text-align actually left-aligns the
   glyph+label. Segoe UI is primary so the Latin label stays crisp — FA Free
   ships its own (ugly) Latin, so listing it first would hijack the words. The
   leading FA glyph isn't in Segoe UI, so Qt falls back to Font Awesome for it.
   FA is registered at startup in main.py via QFontDatabase. */
QPushButton#navBtn {
    background: transparent; color: #9ca3af; border: 0;
    border-radius: 6px; padding: 9px 14px;
    text-align: left; font-size: 13px; font-weight: 600;
    font-family: "Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "Font Awesome 6 Free", sans-serif;
}
QPushButton#navBtn:hover { background-color: #1f2937; color: #e6edf3; }
QPushButton#navBtn:checked { background-color: #1f2937; color: #CE7D6B; }
QScrollArea#settingsScroll, QWidget#settingsBody { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #374151; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4b5563; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QLabel#settingsTitle {
    font-size: 16px; font-weight: 700; color: #e6edf3; letter-spacing: 2px;
}
QLabel#sectionLabel {
    font-size: 10px; color: #6b7280; letter-spacing: 2px; font-weight: 600;
}
QLabel#pathDisplay {
    font-size: 10px; color: #9ca3af;
    background: #0e1116; border: 1px solid #1f2937; border-radius: 4px;
    padding: 8px;
}
QLabel#credStatus { font-size: 10px; color: #6b7280; }
QLabel#sectionHint { font-size: 10px; color: #6b7280; }
QLabel#pollNote { font-size: 10px; color: #f59e0b; font-weight: 600; }

/* Stats page cards */
QFrame#statCard {
    background-color: #0e1116; border: 1px solid #1f2937; border-radius: 8px;
}
QLabel#statLabel { font-size: 10px; color: #6b7280; letter-spacing: 2px; font-weight: 600; }
QLabel#statBig { font-size: 32px; font-weight: 700; color: #e6edf3; }
QLabel#statMid { font-size: 18px; font-weight: 700; color: #e6edf3; }
QLabel#statPlan { font-size: 12px; color: #CE7D6B; font-weight: 600; letter-spacing: 1px; }
QLabel#statDelta { font-size: 12px; font-weight: 700; }
QLabel#statCount { font-size: 11px; color: #6b7280; font-weight: 600; }
QFrame#statDivider { background: #1f2937; max-height: 1px; min-height: 1px; border: 0; }
QPushButton#resetLink {
    background: transparent; color: #9ca3af; border: 0; padding: 2px 4px;
    text-decoration: underline; font-size: 10px;
}
QPushButton#resetLink:hover { color: #e6edf3; }
QCheckBox { color: #e6edf3; font-size: 12px; spacing: 8px; padding: 3px 0; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #374151;
    background-color: #1f2937; border-radius: 2px;
}
QCheckBox::indicator:hover { border-color: #6b7280; }
QCheckBox::indicator:checked {
    background-color: #CE7D6B; border-color: #CE7D6B;
    image: none;
}

/* Text/number inputs — the app had no input styling, so these fell back to the
   native light Windows look. Theme them to match the dark surface: poll-interval
   field, push-channel editors, and the threshold / idle spinners. */
QLineEdit, QSpinBox {
    background-color: #0e1116; color: #e6edf3;
    border: 1px solid #374151; border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #CE7D6B; selection-color: #0a0d12;
}
QLineEdit:focus, QSpinBox:focus { border-color: #CE7D6B; }
QLineEdit:disabled, QSpinBox:disabled {
    color: #4b5563; background-color: #161b22; border-color: #21262d;
}
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 15px; background: #1f2937;
    border-left: 1px solid #374151;
}
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #374151; }
QSpinBox::up-arrow {
    width: 0; height: 0; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #9ca3af;
}
QSpinBox::down-arrow {
    width: 0; height: 0; image: none;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #9ca3af;
}
QSpinBox::up-arrow:hover { border-bottom-color: #e6edf3; }
QSpinBox::down-arrow:hover { border-top-color: #e6edf3; }

/* Approaching-limit threshold sliders: dark groove, salmon fill up to the
   handle, salmon handle, with a value pill beside it. */
QSlider#threshold::groove:horizontal { height: 4px; border-radius: 2px; background: #1f2937; }
QSlider#threshold::add-page:horizontal { background: #1f2937; border-radius: 2px; }
QSlider#threshold::sub-page:horizontal { background: #CE7D6B; border-radius: 2px; }
QSlider#threshold::handle:horizontal {
    width: 13px; height: 13px; margin: -5px 0; border-radius: 7px;
    background: #CE7D6B; border: 2px solid #0a0d12;
}
QSlider#threshold::handle:horizontal:hover { background: #d98f7e; }
/* Editable value field beside the slider — looks like a pill, but click + type.
   It lights up with the salmon focus border both when focused and while its
   slider is being dragged ([sliding="true"]). */
QSpinBox#thresholdField { padding: 3px 4px; font-weight: 600; }
QSpinBox#thresholdField[sliding="true"] { border-color: #CE7D6B; }

/* Slim left nav rail (overlay). Same icon+label language as the settings tabs:
   Segoe UI primary so labels stay crisp; the leading FA glyph falls back to FA.
   Labels are clipped while the rail is collapsed and revealed as it expands. */
QWidget#navRail {
    background-color: #0e1116;
    border-right: 1px solid #1f2937;
}
QPushButton#railBtn {
    background: transparent; color: #9ca3af; border: 0;
    border-radius: 6px; padding: 9px 0px 9px 8px;  /* no right pad: icon never clips,
                                                       and stays put as the rail widens */
    text-align: left; font-size: 15px; font-weight: 600;
    font-family: "Segoe UI", "Helvetica Neue", "Noto Sans", "DejaVu Sans", "Font Awesome 6 Free", sans-serif;
}
QPushButton#railBtn:hover { background-color: #1f2937; color: #e6edf3; }
QPushButton#railBtn:checked { background-color: #1f2937; color: #CE7D6B; }

/* Push-notification channel cards (Settings -> Notifications). */
QWidget#pushCard {
    background-color: #0e1116; border: 1px solid #1f2937; border-radius: 6px;
}
QLabel#pushSummary { font-size: 12px; }
QToolButton#pushEditBtn {
    background: transparent; color: #9ca3af; border: 0;
    padding: 2px 6px; border-radius: 4px; font-size: 11px;
}
QToolButton#pushEditBtn:hover { color: #CE7D6B; background-color: #1f2937; }
QToolButton#pushRemoveBtn {
    background: transparent; color: #6b7280; border: 0;
    padding: 2px 7px; border-radius: 4px; font-size: 12px;
}
QToolButton#pushRemoveBtn:hover { color: #ffffff; background-color: #c13434; }
QToolButton#addChannelBtn {
    background: transparent; color: #CE7D6B; border: 1px dashed #374151;
    padding: 5px 12px; border-radius: 6px; font-size: 11px;
}
QToolButton#addChannelBtn:hover { background-color: #1f2937; border-color: #CE7D6B; }
QToolButton#addChannelBtn:disabled { color: #4b5563; border-color: #21262d; }
QToolButton#addChannelBtn::menu-indicator { image: none; width: 0; }

QWidget#miniRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#miniPct { font-size: 17px; font-weight: 700; color: #e6edf3; }
QLabel#miniPctSub { font-size: 13px; font-weight: 700; color: #9ca3af; }
QLabel#miniReset { font-size: 12px; color: #9ca3af; }

QWidget#toastRoot {
    background-color: #0e1116;
    border: 1px solid #CE7D6B;
}
QLabel#toastTitle {
    font-size: 14px; font-weight: 700; color: #e6edf3; letter-spacing: 0.5px;
}
QLabel#toastBody { font-size: 12px; color: #9ca3af; }
"""


def build_qss(p: Palette) -> str:
    """Return the main-window stylesheet rendered from palette ``p``.

    With ``MIDNIGHT_SALMON`` the output is byte-identical to the historical
    hardcoded stylesheet (guarded by ``tests/test_theme.py``)."""
    return _HEX_RE.sub(lambda m: getattr(p, _QSS_HEX_TO_FIELD[m.group(0)]), _BASE_QSS)


def active() -> Palette:
    """The palette currently in force.

    Phase 1: always the default. Phase 2 wires this to the saved theme setting
    and adds a change signal so the running app can restyle live.
    """
    return MIDNIGHT_SALMON
