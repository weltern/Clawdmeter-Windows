"""Render the drag-to-install DMG background art.

Produces ``packaging/dmg-background.png`` (600x400, 1x) and
``packaging/dmg-background@2x.png`` (1200x800). ``build-macos.sh`` combines the
two into a HiDPI ``dmg-background.tiff`` with ``tiffutil`` at build time.

Checked in as art, not generated during the build — this runs anywhere PySide6
is installed (including Windows), so the Mac builder needs no image toolchain.
Regenerate with::

    python packaging/make_dmg_background.py

Design (settled 2026-07-26 after a nine-round mockup pass): "Light THINKING" —
a flat warm-paper field in the industry style (Slack/Discord/Chrome all ship
light; Finder hard-codes dark filename labels over any custom background, so
light is the only field they stay readable on). Clawd's session-shelf treatment
is baked in around the real icon slot: the activity + status lines sit beneath
where Finder places the app icon, and four salmon chevrons walk toward
Applications. The icon slot itself is left bare — no glow or silhouette behind
it — so the real icon reads cleanly whatever shape macOS gives it (bare mascot
on macOS 15, rounded tile on macOS 26 / Tahoe).

Geometry MUST stay in sync with ``packaging/dmg_settings.py``: the icon slots
below are where dmgbuild is told to place Clawdmeter.app and the Applications
symlink, and the chevrons are drawn in the gap between them.
"""

from __future__ import annotations

import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF,  # noqa: E402
                           QImage, QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QApplication  # noqa: E402

# The offscreen platform plugin ships no fonts, so text renders as tofu boxes
# unless a real face is loaded by path. Try the system UI font of each OS.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/SFNS.ttf",                      # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",                       # Windows
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # Linux
)

W, H = 600, 400                # window size in points
APP_SLOT = (150, 190)          # centre of the Clawdmeter.app icon
APPS_SLOT = (450, 190)         # centre of the Applications symlink
ICON = 128                     # Finder icon size

PAPER = "#f6f1ea"              # warm paper field (light, brand-warmed)
ACCENT = "#CE7D6B"             # Midnight Salmon (the default theme's accent)
THINKING = "#5B8DEF"           # transcript.ACTIVITY_COLORS[Activity.THINKING]
STATUS_INK = "#8a8378"         # status line on paper
DRAG_INK = "#5c564d"           # instruction line on paper


def _load_ui_font() -> str:
    for path in _FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        fid = QFontDatabase.addApplicationFont(path)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            return fams[0]
    raise SystemExit("no usable UI font found — add one to _FONT_CANDIDATES")


def _grain(p: QPainter, scale: int) -> None:
    """Barely-there film grain (the Arc installer look). Deterministic so the
    checked-in art is reproducible; drawn in device pixels so it stays crisp
    in the @2x render instead of scaling into soft blobs."""
    rng = random.Random(0xC1A3D)
    w, h = W * scale, H * scale
    noise = QImage(rng.randbytes(w * h), w, h, w, QImage.Format_Grayscale8).copy()
    p.save()
    p.resetTransform()
    p.setCompositionMode(QPainter.CompositionMode_Screen)
    p.setOpacity(0.05)
    p.drawImage(0, 0, noise)
    p.setCompositionMode(QPainter.CompositionMode_Multiply)
    p.setOpacity(0.03)
    p.drawImage(0, 0, noise)
    p.restore()


def _centered_text(p: QPainter, cx: float, baseline: float, text: str,
                   font: QFont, color: QColor) -> QRectF:
    """Draw text centred on cx at the given baseline; returns its bounds."""
    p.setFont(font)
    p.setPen(color)
    fm = QFontMetricsF(font)
    wpx = fm.horizontalAdvance(text)
    x = cx - wpx / 2
    p.drawText(QPointF(x, baseline), text)
    return QRectF(x, baseline - fm.ascent(), wpx, fm.height())


def _draw(p: QPainter, scale: int) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)

    # Paper field + grain.
    p.fillRect(QRectF(0, 0, W, H), QColor(PAPER))
    _grain(p, scale)

    # The app-icon slot is left bare — no glow, no silhouette. Finder drops the
    # real Clawdmeter icon here, and with nothing painted behind it there is no
    # placeholder shape to clash with the icon: it works identically for the
    # bare mascot on macOS 15 and the rounded tile macOS 26 (Tahoe) wraps it in.
    # Only the session-shelf text below the slot and the chevrons remain.

    # --- chevrons: four, brightening toward Applications --------------------
    pen = QPen(QColor(ACCENT), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    for x, opacity in ((262, 0.35), (292, 0.55), (322, 0.75), (352, 0.95)):
        path = QPainterPath(QPointF(x, 176))
        path.lineTo(x + 16, 190)
        path.lineTo(x, 204)
        col = QColor(ACCENT)
        col.setAlphaF(opacity)
        pen.setColor(col)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    # --- session-shelf text under the app slot ------------------------------
    fam = _load_ui_font()

    activity_font = QFont(fam)
    activity_font.setPixelSize(12)
    activity_font.setWeight(QFont.Bold)
    activity_font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
    bounds = _centered_text(p, APP_SLOT[0] + 4, 296, "THINKING",
                            activity_font, QColor(THINKING))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(THINKING))
    p.drawEllipse(QPointF(bounds.left() - 11, 292), 3.5, 3.5)

    status_font = QFont(fam)
    status_font.setPixelSize(10)
    status_font.setWeight(QFont.DemiBold)
    status_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.4)
    _centered_text(p, APP_SLOT[0], 315, "PONDERING ITS NEW HOME",
                   status_font, QColor(STATUS_INK))

    # --- instruction line ---------------------------------------------------
    drag_font = QFont(fam)
    drag_font.setPixelSize(15)
    drag_font.setWeight(QFont.DemiBold)
    _centered_text(p, W / 2, 362, "Drag Clawdmeter to Applications",
                   drag_font, QColor(DRAG_INK))


def render(path: str, scale: int) -> None:
    # Raw pixel buffer — deliberately NO setDevicePixelRatio(): that would make
    # QPainter apply the scale a second time on top of p.scale() below, and
    # dmgbuild wants plain 1x/2x images anyway.
    img = QImage(W * scale, H * scale, QImage.Format_RGB32)
    p = QPainter(img)
    p.scale(scale, scale)
    _draw(p, scale)
    p.end()
    if not img.save(path, "PNG"):
        raise SystemExit(f"failed to write {path}")
    print(f"wrote {path} ({W * scale}x{H * scale})")


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    here = os.path.dirname(os.path.abspath(__file__))
    render(os.path.join(here, "dmg-background.png"), 1)
    render(os.path.join(here, "dmg-background@2x.png"), 2)
