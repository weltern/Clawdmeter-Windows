"""Hand-drawn Stats visuals (QPainter, no charting lib — keeps the exe lean).

  * DailyBars — a slim per-day value bar strip (the "value this month" sparkline).
  * Heatmap   — a 7x24 weekday x hour activity grid ("when you Claude most").

Both are dumb views: feed them the aggregate slices from stats.build_aggregate
via set_data() and they repaint. Colours track the app's dark/salmon theme.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

_ACCENT = QColor("#CE7D6B")
_EMPTY = QColor("#161b22")     # an empty cell / zero bar track
_DIM = QColor("#6b7280")       # labels


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class DailyBars(QWidget):
    """Per-day value bars (oldest left -> today right). set_data([(date, usd)])."""

    def __init__(self, parent=None, height: int = 60) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self._data: list = []

    def set_data(self, series: list) -> None:
        self._data = list(series or [])
        self.setToolTip("")
        self.update()

    def paintEvent(self, _e) -> None:
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        n = len(self._data)
        vmax = max((v for _, v in self._data), default=0.0) or 1.0
        gap = 2.0
        bw = max(1.0, (w - gap * (n - 1)) / n)
        for i, (_d, v) in enumerate(self._data):
            x = i * (bw + gap)
            bh = max(1.0, (v / vmax) * (h - 2)) if v else 1.0
            track = v > 0
            p.fillRect(QRectF(x, h - bh, bw, bh), _ACCENT if track else _EMPTY)
        p.end()


class Heatmap(QWidget):
    """7x24 weekday(row) x hour(col) activity grid. set_data(grid[7][24])."""

    _DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    _LABEL_W = 30
    _CELL_H = 13
    _FOOT = 14   # hour-tick strip

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grid = [[0] * 24 for _ in range(7)]
        self.setMinimumHeight(7 * self._CELL_H + self._FOOT + 4)

    def set_data(self, grid: list) -> None:
        self._grid = grid or [[0] * 24 for _ in range(7)]
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)

        vmax = max((c for row in self._grid for c in row), default=0) or 1
        cell_w = (self.width() - self._LABEL_W) / 24.0
        ch = self._CELL_H
        for r in range(7):
            y = r * ch
            p.setPen(_DIM)
            p.drawText(QRectF(0, y, self._LABEL_W - 4, ch),
                       Qt.AlignVCenter | Qt.AlignRight, self._DOW[r])
            for c in range(24):
                x = self._LABEL_W + c * cell_w
                cnt = self._grid[r][c]
                col = _lerp(_EMPTY, _ACCENT, cnt / vmax) if cnt else _EMPTY
                p.fillRect(QRectF(x + 0.5, y + 0.5, cell_w - 1, ch - 1), col)

        # hour ticks at 0 / 6 / 12 / 18
        p.setPen(_DIM)
        yfoot = 7 * ch + 2
        for hr in (0, 6, 12, 18):
            x = self._LABEL_W + hr * cell_w
            p.drawText(QRectF(x, yfoot, 24, self._FOOT), Qt.AlignLeft | Qt.AlignTop, f"{hr:02d}")
        p.end()
