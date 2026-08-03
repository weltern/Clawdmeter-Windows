"""A themed in-app HSV colour picker — a saturation/value square + a hue slider
+ a hex field — so the custom theme editor never falls back to the native OS
colour dialog (which is unthemed and breaks the app's look).

ColorPicker composes the two painted controls and a hex input, keeps them in
sync, and emits colorChanged(hex) on every edit. set_color(hex) loads a colour
in (e.g. when a different theme role is selected to edit).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QRect, QRectF
from PySide6.QtGui import (
    QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QToolTip, QVBoxLayout, QWidget,
)


class _SVSquare(QWidget):
    """Saturation (x) × value (y) plane for a fixed hue. Drag to set S/V."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._h = 0.0    # hue 0..1 (set externally)
        self._s = 0.0
        self._v = 0.0
        self.setMinimumSize(150, 104)
        self.setCursor(Qt.CrossCursor)

    def set_hue(self, h: float) -> None:
        self._h = h
        self.update()

    def set_sv(self, s: float, v: float) -> None:
        self._s, self._v = s, v
        self.update()

    def sv(self) -> tuple:
        return self._s, self._v

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        base = QColor.fromHsvF(max(0.0, self._h), 1.0, 1.0)
        gh = QLinearGradient(r.left(), 0, r.right(), 0)
        gh.setColorAt(0.0, QColor("#ffffff"))
        gh.setColorAt(1.0, base)
        p.fillRect(r, gh)
        gv = QLinearGradient(0, r.top(), 0, r.bottom())
        gv.setColorAt(0.0, QColor(0, 0, 0, 0))
        gv.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(r, gv)
        # 1px inner border so the square reads as a control
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        # cursor ring
        cx = self._s * r.width()
        cy = (1.0 - self._v) * r.height()
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawEllipse(QPointF(cx, cy), 6, 6)
        p.setPen(QPen(QColor(0, 0, 0, 150), 1))
        p.drawEllipse(QPointF(cx, cy), 7.5, 7.5)

    def mousePressEvent(self, e) -> None:
        self._apply(e)

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._apply(e)

    def _apply(self, e) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        self._s = min(1.0, max(0.0, e.position().x() / w))
        self._v = min(1.0, max(0.0, 1.0 - e.position().y() / h))
        self.update()
        self.changed.emit()


class _HueBar(QWidget):
    """Horizontal hue spectrum. Drag to set the hue."""

    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._h = 0.0
        self.setFixedHeight(14)
        self.setCursor(Qt.PointingHandCursor)

    def set_hue(self, h: float) -> None:
        self._h = h
        self.update()

    def hue(self) -> float:
        return self._h

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        for i in range(7):
            g.setColorAt(i / 6.0, QColor.fromHsvF(i / 6.0, 1.0, 1.0))
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, rad, rad)
        cx = r.left() + self._h * r.width()
        cy = r.center().y()
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(cx, cy), 6, 6)

    def mousePressEvent(self, e) -> None:
        self._apply(e)

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() & Qt.LeftButton:
            self._apply(e)

    def _apply(self, e) -> None:
        self._h = min(1.0, max(0.0, e.position().x() / max(1, self.width())))
        self.update()
        self.changed.emit()


class ColorPicker(QWidget):
    """SV square + hue bar + hex field. Emits colorChanged(hex) on every edit."""

    colorChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor("#000000")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        self.square = _SVSquare()
        self.hue = _HueBar()
        lay.addWidget(self.square, 1)
        lay.addWidget(self.hue)
        row = QHBoxLayout()
        row.setSpacing(7)
        row.addWidget(QLabel("HEX", objectName="sectionLabel"))
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        row.addWidget(self.hex, 1)
        # Eyedropper: grab a colour from anywhere on screen (FA eye-dropper glyph).
        self.eyedropper = QPushButton("")
        self.eyedropper.setObjectName("eyedropBtn")
        self.eyedropper.setFont(QFont("Font Awesome 6 Free"))
        self.eyedropper.setToolTip("Pick a colour from anywhere on screen")
        self.eyedropper.setCursor(Qt.PointingHandCursor)
        self.eyedropper.setFocusPolicy(Qt.NoFocus)
        self.eyedropper.setFixedWidth(34)
        self.eyedropper.clicked.connect(self._launch_eyedropper)
        row.addWidget(self.eyedropper)
        lay.addLayout(row)
        self._eyedrop = None
        self._sampler = None   # retains the macOS NSColorSampler during a pick
        self.square.changed.connect(self._from_square)
        self.hue.changed.connect(self._from_hue)
        self.hex.editingFinished.connect(self._from_hex)

    def _launch_eyedropper(self) -> None:
        if sys.platform == "darwin":
            # macOS: use Apple's native system eyedropper (NSColorSampler). The
            # system service does the sampling, so it captures EVERY window and
            # needs no Screen Recording permission -- unlike a DIY screenshot
            # overlay, which macOS sandboxing strips down to just the wallpaper.
            self._launch_macos_sampler()
            return
        # Windows/Linux: freeze the desktop into a full-screen overlay and pick.
        self._eyedrop = EyedropperOverlay(self)
        self._eyedrop.picked.connect(self._on_eyedropped)
        self._eyedrop.show()
        self._eyedrop.raise_()
        self._eyedrop.activateWindow()

    def _launch_macos_sampler(self) -> None:
        try:
            from AppKit import NSColorSampler, NSColorSpace
        except Exception:   # noqa: BLE001 - pyobjc missing from the build
            self._eyedropper_note(
                "The native colour sampler isn't available in this build.")
            return

        def _handler(nscolor) -> None:
            self._sampler = None   # release the retained sampler
            if nscolor is None:    # user pressed Escape / cancelled
                return
            srgb = nscolor.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) or nscolor
            try:
                r = int(round(srgb.redComponent() * 255))
                g = int(round(srgb.greenComponent() * 255))
                b = int(round(srgb.blueComponent() * 255))
            except Exception:   # noqa: BLE001 - non-RGB colour -> ignore
                return
            hexv = f"#{r:02x}{g:02x}{b:02x}"
            self.set_color(hexv)
            self.colorChanged.emit(hexv)

        # Keep a reference so the sampler isn't GC'd before the async pick fires.
        self._sampler = NSColorSampler.alloc().init()
        self._sampler.showSamplerWithSelectionHandler_(_handler)

    def _eyedropper_note(self, msg: str) -> None:
        self.eyedropper.setToolTip(msg)
        QToolTip.showText(
            self.eyedropper.mapToGlobal(QPoint(0, self.eyedropper.height())),
            msg, self.eyedropper)

    def _on_eyedropped(self, hexv: str) -> None:
        self._eyedrop = None
        if hexv:
            self.set_color(hexv)
            self.colorChanged.emit(hexv)

    def color(self) -> str:
        return self._color.name()

    def set_color(self, hexv: str) -> None:
        """Load a colour into the picker without emitting (external set)."""
        c = QColor(hexv)
        if not c.isValid():
            return
        self._color = c
        h, s, v, _ = c.getHsvF()
        h = max(0.0, h)   # -1 for achromatic greys
        self.square.set_hue(h)
        self.square.set_sv(s, v)
        self.hue.set_hue(h)
        self.hex.setText(c.name().upper())

    def _emit(self) -> None:
        self.hex.setText(self._color.name().upper())
        self.colorChanged.emit(self._color.name())

    def _from_square(self) -> None:
        s, v = self.square.sv()
        self._color = QColor.fromHsvF(self.hue.hue(), s, v)
        self._emit()

    def _from_hue(self) -> None:
        h = self.hue.hue()
        self.square.set_hue(h)
        s, v = self.square.sv()
        self._color = QColor.fromHsvF(h, s, v)
        self._emit()

    def _from_hex(self) -> None:
        c = QColor(self.hex.text().strip())
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(c.name())
        else:
            # Unparseable text: snap the field back to the colour actually in
            # effect. Without this the readout keeps showing the bad text and
            # disagrees with the swatch until the user retypes a valid hex.
            self.hex.setText(self._color.name().upper())


class EyedropperOverlay(QWidget):
    """Full-screen overlay that freezes the desktop and lets the user click any
    pixel to pick its colour. Emits picked(hex) on a left click, picked("") on
    cancel (Escape / right-click). A crosshair + a hex readout track the cursor."""

    picked = Signal(str)

    def __init__(self, parent=None) -> None:
        # Owned by `parent` (the picker) so a modal editor dialog doesn't block
        # its input; a plain frameless top-level Window (not a Tool, which can
        # auto-hide when the app loses focus).
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # Freeze the desktop BEFORE we show, so the overlay never appears in its
        # own capture. (macOS uses the native NSColorSampler instead and never
        # reaches this overlay.)
        #
        # Grab each screen SEPARATELY: on a mixed-DPI desktop every screen has
        # its own device-pixel ratio, so one whole-desktop grab — which carries a
        # single ratio — mis-maps logical->pixel on any screen scaled differently
        # from the primary, and the error grows with distance from that screen's
        # origin. Each shot keeps the ratio it was actually taken at.
        vg = QRect()
        self._shots: list[tuple[QRect, object, object, float]] = []
        for s in QGuiApplication.screens():
            geo = s.geometry()
            vg = vg.united(geo)
            pm = s.grabWindow(0)
            # Derive the ratio from the capture itself rather than trusting the
            # pixmap's own devicePixelRatio: what grabWindow() stamps on it has
            # varied across Qt versions, and pixels/logical-size is ground truth.
            dpr = (pm.width() / geo.width()) if geo.width() else 1.0
            pm.setDevicePixelRatio(dpr or 1.0)
            self._shots.append((geo, pm, pm.toImage(), dpr or 1.0))
        self._vg = vg
        self.setGeometry(vg)
        self._gpos = vg.topLeft()
        self._hex = "#000000"

    def _shot_at(self, gpos: QPoint):
        """The (geometry, pixmap, image, dpr) shot whose screen holds `gpos`."""
        for shot in self._shots:
            if shot[0].contains(gpos):
                return shot
        return self._shots[0] if self._shots else None

    def _sample(self, gpos: QPoint) -> QColor:
        shot = self._shot_at(gpos)
        if shot is None:
            return QColor(0, 0, 0)
        geo, _pm, img, dpr = shot
        x = int((gpos.x() - geo.x()) * dpr)
        y = int((gpos.y() - geo.y()) * dpr)
        x = min(img.width() - 1, max(0, x))
        y = min(img.height() - 1, max(0, y))
        return QColor(img.pixelColor(x, y))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        # Grab so we get mouse/keyboard even under a modal dialog and even when
        # the pointer is over another application's window.
        self.grabMouse()
        self.grabKeyboard()

    def _finish(self, hexv: str) -> None:
        self.releaseMouse()
        self.releaseKeyboard()
        self.picked.emit(hexv)
        self.close()

    def mouseMoveEvent(self, e) -> None:
        self._gpos = e.globalPosition().toPoint()
        self._hex = self._sample(self._gpos).name()
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._finish(self._sample(e.globalPosition().toPoint()).name())
        else:
            self._finish("")

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self._finish("")

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        # One blit per screen; each pixmap carries its own ratio so a
        # differently-scaled monitor draws at its true logical size.
        for geo, pm, _img, _dpr in self._shots:
            o = geo.topLeft() - self._vg.topLeft()
            p.drawPixmap(o.x(), o.y(), pm)
        lp = self._gpos - self._vg.topLeft()
        # Crosshair — dark stroke under a light one so it reads on any colour.
        for pen in (QPen(QColor(0, 0, 0, 150), 3), QPen(QColor(255, 255, 255, 235), 1)):
            p.setPen(pen)
            p.drawLine(lp.x() - 12, lp.y(), lp.x() + 12, lp.y())
            p.drawLine(lp.x(), lp.y() - 12, lp.x(), lp.y() + 12)
        # Readout chip (swatch + hex), kept on-screen near the cursor.
        p.setRenderHint(QPainter.Antialiasing)
        chip = QRect(lp.x() + 16, lp.y() + 16, 108, 30)
        if chip.right() > self.width():
            chip.moveLeft(lp.x() - 16 - chip.width())
        if chip.bottom() > self.height():
            chip.moveTop(lp.y() - 16 - chip.height())
        p.setPen(QPen(QColor(0, 0, 0, 80), 1))
        p.setBrush(QColor(18, 20, 26, 242))
        p.drawRoundedRect(chip, 7, 7)
        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.setBrush(QColor(self._hex))
        p.drawRoundedRect(QRect(chip.x() + 7, chip.y() + 8, 14, 14), 3, 3)
        p.setPen(QColor("#e6edf3"))
        f = QFont("Consolas")
        f.setPixelSize(12)
        p.setFont(f)
        p.drawText(chip.adjusted(28, 0, -6, 0), Qt.AlignVCenter, self._hex.upper())
