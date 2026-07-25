"""Render the drag-to-install DMG background art.

Produces ``packaging/dmg-background.png`` (600x400, 1x) and
``packaging/dmg-background@2x.png`` (1200x800). ``build-macos.sh`` combines the
two into a HiDPI ``dmg-background.tiff`` with ``tiffutil`` at build time.

Checked in as art, not generated during the build — this runs anywhere PySide6
is installed (including Windows), so the Mac builder needs no image toolchain.
Regenerate with::

    python packaging/make_dmg_background.py

Geometry MUST stay in sync with ``packaging/dmg_settings.py``: the icon slots
below are where dmgbuild is told to place Clawdmeter.app and the Applications
symlink, and the arrow is drawn in the gap between them.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QImage,  # noqa: E402
                           QLinearGradient, QPainter, QPainterPath, QPen,
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

BG_TOP = "#161b23"             # Clawdmeter dark, slightly lifted at the top
BG_BOTTOM = "#0b0e13"
ACCENT = "#CE7D6B"             # Midnight Salmon (the default theme's accent)
HINT = "#7c8798"


def _load_ui_font() -> str:
    for path in _FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        fid = QFontDatabase.addApplicationFont(path)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            return fams[0]
    raise SystemExit("no usable UI font found — add one to _FONT_CANDIDATES")


def _draw(p: QPainter) -> None:
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # Base gradient.
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0.0, QColor(BG_TOP))
    g.setColorAt(1.0, QColor(BG_BOTTOM))
    p.fillRect(QRectF(0, 0, W, H), g)

    # Soft salmon glow behind each icon slot so the icons sit on light, not on
    # flat black — reads as depth rather than decoration.
    for cx, cy, strength in ((APP_SLOT[0], APP_SLOT[1], 34),
                             (APPS_SLOT[0], APPS_SLOT[1], 16)):
        halo = QRadialGradient(QPointF(cx, cy), 150)
        halo.setColorAt(0.0, QColor(206, 125, 107, strength))
        halo.setColorAt(1.0, QColor(206, 125, 107, 0))
        p.fillRect(QRectF(0, 0, W, H), halo)

    # Hairline top edge — the same 1px lift the app's title bar has.
    p.setPen(QPen(QColor(255, 255, 255, 18), 1))
    p.drawLine(0, 0, W, 0)

    # --- the arrow: dotted trail + solid head, app slot -> Applications ------
    y = APP_SLOT[1]
    x0, x1 = APP_SLOT[0] + ICON // 2 + 26, APPS_SLOT[0] - ICON // 2 - 26
    head_w = 22
    p.setPen(Qt.NoPen)
    n_dots = 7
    span = (x1 - head_w - 6) - x0
    for i in range(n_dots):
        t = i / (n_dots - 1)
        x = x0 + span * t
        r = 2.0 + 1.6 * t                       # dots grow toward the target
        a = int(70 + 150 * t)                   # ...and brighten
        p.setBrush(QColor(206, 125, 107, a))
        p.drawEllipse(QPointF(x, y), r, r)

    head = QPainterPath()
    head.moveTo(x1, y)
    head.lineTo(x1 - head_w, y - 13)
    head.lineTo(x1 - head_w, y + 13)
    head.closeSubpath()
    p.setBrush(QColor(ACCENT))
    p.drawPath(head)

    # --- hint line ----------------------------------------------------------
    f = QFont(_load_ui_font())
    f.setPixelSize(15)
    f.setWeight(QFont.Medium)
    p.setFont(f)
    p.setPen(QColor(HINT))
    p.drawText(QRectF(0, H - 74, W, 24), Qt.AlignHCenter | Qt.AlignVCenter,
               "Drag Clawdmeter into Applications to install")


def render(path: str, scale: int) -> None:
    # Raw pixel buffer — deliberately NO setDevicePixelRatio(): that would make
    # QPainter apply the scale a second time on top of p.scale() below, and
    # dmgbuild wants plain 1x/2x images anyway.
    img = QImage(W * scale, H * scale, QImage.Format_RGB32)
    p = QPainter(img)
    p.scale(scale, scale)
    _draw(p)
    p.end()
    if not img.save(path, "PNG"):
        raise SystemExit(f"failed to write {path}")
    print(f"wrote {path} ({W * scale}x{H * scale})")


if __name__ == "__main__":
    _app = QApplication.instance() or QApplication(sys.argv[:1])
    here = os.path.dirname(os.path.abspath(__file__))
    render(os.path.join(here, "dmg-background.png"), 1)
    render(os.path.join(here, "dmg-background@2x.png"), 2)
