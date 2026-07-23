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
    # State — the idle-session indicator (its label, status dot and mascot
    # glow). A theme role, NOT a fixed activity hue, so it stays readable on
    # every background. Other activity colours (reading/writing/…) are true
    # semantic hues and remain fixed in transcript.ACTIVITY_COLORS.
    idle: str

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
    idle="#3a3f4b",
)


# ── Preset catalogue ─────────────────────────────────────────────────────────
# Every preset is a full Palette. `accent` is an ordinary per-preset field, so
# nothing is hard-locked to salmon: to pull any preset's accent back to the
# salmon default (or push a neutral one to its own colour) is a ONE-LINE edit
# here. The only truly fixed salmon in the app is the mascot sprite (a PNG).
#
# Status colours (warn/danger/danger_strong/positive) are kept semantic and
# only lightly tuned per palette — they're always reinforced by an icon/label
# so they read for colour-blind users regardless of theme.

# Near-black OLED-friendly dark; keeps the salmon accent.
OBSIDIAN = Palette(
    bg="#0a0a0c", bg_deep="#060608", bg_deepest="#050506",
    surface="#17181c", surface_dim="#121317", surface_sunken="#1c1e23",
    border="#2c2e35", border_dim="#45474f",
    text="#eceef2", text_dim="#9a9da6", text_muted="#777a82",
    accent="#CE7D6B", accent_hover="#d98f7e",
    warn="#f59e0b", danger="#dc2626", danger_strong="#c13434", positive="#5FB3A1",
    idle="#7b7d85",
)

# Accessibility-first: pure-black canvas, high-luma text, stronger borders,
# brightened salmon so the accent clears AA on black.
HIGH_CONTRAST = Palette(
    bg="#000000", bg_deep="#000000", bg_deepest="#000000",
    surface="#161616", surface_dim="#0e0e0e", surface_sunken="#1e1e1e",
    border="#4a4a4a", border_dim="#6a6a6a",
    text="#ffffff", text_dim="#d4d4d4", text_muted="#a6a6a6",
    accent="#f2a58f", accent_hover="#ffb8a3",
    warn="#ffb020", danger="#ff5c5c", danger_strong="#ff3b3b", positive="#4ad991",
    idle="#a6a6a6",
)

# Nord — cool polar-night slate. Signature frost accent.
NORD = Palette(
    bg="#2e3440", bg_deep="#272c36", bg_deepest="#242933",
    surface="#3b4252", surface_dim="#353b48", surface_sunken="#414a5c",
    border="#4c566a", border_dim="#616e88",
    text="#eceff4", text_dim="#d8dee9", text_muted="#9aa2b1",
    accent="#88c0d0", accent_hover="#8fbcbb",
    warn="#ebcb8b", danger="#bf616a", danger_strong="#a5545c", positive="#a3be8c",
    idle="#9aa2b1",
)

# Dracula — signature purple accent.
DRACULA = Palette(
    bg="#282a36", bg_deep="#21222c", bg_deepest="#1e1f28",
    surface="#343746", surface_dim="#2b2e3b", surface_sunken="#3c4052",
    border="#44475a", border_dim="#565a71",
    text="#f8f8f2", text_dim="#c8c9da", text_muted="#8692bf",
    accent="#bd93f9", accent_hover="#cba6fa",
    warn="#ffb86c", danger="#ff5555", danger_strong="#e04a4a", positive="#50fa7b",
    idle="#8995c1",
)

# Gruvbox — warm retro. Signature orange accent.
GRUVBOX = Palette(
    bg="#282828", bg_deep="#1d2021", bg_deepest="#1b1b1b",
    surface="#3c3836", surface_dim="#32302f", surface_sunken="#45403d",
    border="#504945", border_dim="#665c54",
    text="#ebdbb2", text_dim="#d5c4a1", text_muted="#a89984",
    accent="#fe8019", accent_hover="#ff9642",
    warn="#fabd2f", danger="#fb4934", danger_strong="#cc2f26", positive="#b8bb26",
    idle="#a89984",
)

# Terminal Green — phosphor CRT. Signature green accent.
TERMINAL_GREEN = Palette(
    bg="#0c0f0c", bg_deep="#080a08", bg_deepest="#060806",
    surface="#141814", surface_dim="#101410", surface_sunken="#1a1f1a",
    border="#253025", border_dim="#3a463a",
    text="#d6f5d6", text_dim="#86c586", text_muted="#608560",
    accent="#3fdd6a", accent_hover="#5fe986",
    warn="#e0c040", danger="#ff6a5a", danger_strong="#d84545", positive="#3fdd6a",
    idle="#648864",
)

# Amber CRT — amber monochrome. Signature amber accent.
AMBER_CRT = Palette(
    bg="#100b04", bg_deep="#0b0803", bg_deepest="#090602",
    surface="#1a1206", surface_dim="#150e05", surface_sunken="#201708",
    border="#3a2a10", border_dim="#55401a",
    text="#ffcf8f", text_dim="#d99a55", text_muted="#9f723e",
    accent="#ffb000", accent_hover="#ffc233",
    warn="#ffd24d", danger="#ff6a44", danger_strong="#d84428", positive="#c0c04a",
    idle="#a27540",
)


# Ordered catalogue. First entry is the default. To reorder or add a preset,
# edit this dict — the Appearance picker and persistence read it directly.
PRESETS = {
    "Midnight Salmon": MIDNIGHT_SALMON,
    "Obsidian": OBSIDIAN,
    "High Contrast": HIGH_CONTRAST,
    "Nord": NORD,
    "Dracula": DRACULA,
    "Gruvbox": GRUVBOX,
    "Terminal Green": TERMINAL_GREEN,
    "Amber CRT": AMBER_CRT,
}
DEFAULT_NAME = "Midnight Salmon"

# Module-level "which theme is live" state. Consumers call active(); the app
# calls set_active() (via apply_theme in dashboard) on startup and on a switch.
_active_name = DEFAULT_NAME
_active_palette = MIDNIGHT_SALMON


def names() -> list:
    """Preset display names, in catalogue order."""
    return list(PRESETS)


def get(name: str) -> Palette:
    """The palette for `name`, or the default if unknown."""
    return PRESETS.get(name, MIDNIGHT_SALMON)


def active_name() -> str:
    """Name of the palette currently in force."""
    return _active_name


def set_active(name: str) -> Palette:
    """Make `name` the active palette (falls back to default if unknown).

    Pure state — emits no signal and restyles nothing. The app's apply_theme()
    orchestrates the refresh/restyle after calling this.
    """
    global _active_name, _active_palette
    if name in PRESETS:
        _active_name, _active_palette = name, PRESETS[name]
    else:
        _active_name, _active_palette = DEFAULT_NAME, MIDNIGHT_SALMON
    return _active_palette


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

/* Appearance settings page — theme picker. Rows use palette hexes so the
   picker itself re-themes with the active theme; the per-row swatch chips are
   styled inline with each preset's own colours (they must NOT re-theme). */
QFrame#themeOption {
    background-color: #0e1116; border: 1px solid #1f2937; border-radius: 8px;
}
QFrame#themeOption:hover { border-color: #374151; }
QFrame#themeOption[selected="true"] { border-color: #CE7D6B; }
QLabel#themeName { font-size: 13px; font-weight: 600; color: #e6edf3; }
QLabel#themeTag { font-size: 10px; font-weight: 700; color: #CE7D6B; letter-spacing: 1px; }
"""


def build_qss(p: Palette) -> str:
    """Return the main-window stylesheet rendered from palette ``p``.

    With ``MIDNIGHT_SALMON`` the output is byte-identical to the historical
    hardcoded stylesheet (guarded by ``tests/test_theme.py``)."""
    return _HEX_RE.sub(lambda m: getattr(p, _QSS_HEX_TO_FIELD[m.group(0)]), _BASE_QSS)


def active() -> Palette:
    """The palette currently in force (see set_active())."""
    return _active_palette
