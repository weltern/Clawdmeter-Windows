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
is baked in around the real icon slot: the THINKING activity glow painted where
Finder places the app icon, the activity + status lines beneath, and four
salmon chevrons walking toward Applications.

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
                           QImage, QPainter, QPainterPath, QPen,
                           QRadialGradient)
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


def _icon_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "assets", "icon.png")


def _tinted_mascot(color: QColor) -> QImage:
    """The mascot's silhouette filled with a flat colour, on a padded canvas so
    the blur passes below have room to spill past the sprite's edges."""
    src = QImage(_icon_path())
    if src.isNull():
        raise SystemExit(f"cannot read {_icon_path()}")
    pad = src.width() // 2
    canvas = QImage(src.width() + 2 * pad, src.height() + 2 * pad,
                    QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    p.drawImage(pad, pad, src)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(canvas.rect(), color)
    p.end()
    return canvas


def _blurred(img: QImage, factor: int) -> QImage:
    """Cheap gaussian-ish blur: downscale smooth, upscale smooth back."""
    small = img.scaled(max(1, img.width() // factor),
                       max(1, img.height() // factor),
                       Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(img.width(), img.height(),
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


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

    # Soft radial wash behind the app slot — the widest ring of the glow.
    halo = QRadialGradient(QPointF(*APP_SLOT), 130)
    c = QColor(THINKING)
    c.setAlphaF(0.22)
    halo.setColorAt(0.0, c)
    halo.setColorAt(1.0, QColor(91, 141, 239, 0))
    p.fillRect(QRectF(0, 0, W, H), halo)

    # The THINKING glow: blurred blue silhouettes of the mascot, painted where
    # Finder will place the real icon so the icon appears to glow. Padded
    # canvas is 2x the sprite, so draw it at 2x the icon box.
    sil = _tinted_mascot(QColor(THINKING))
    box = QRectF(APP_SLOT[0] - ICON, APP_SLOT[1] - ICON, ICON * 2, ICON * 2)
    for factor, opacity in ((48, 0.5), (24, 0.6), (14, 0.7)):
        p.setOpacity(opacity)
        p.drawImage(box, _blurred(sil, factor))
    p.setOpacity(1.0)

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
