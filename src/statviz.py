"""Hand-drawn Stats visuals (QPainter, no charting lib — keeps the exe lean).

  * DailyBars — a slim per-day value bar strip (the "value this month" sparkline).
  * Heatmap   — a 7x24 weekday x hour activity grid ("when you Claude most").

Both are dumb views: feed them the aggregate slices from stats.build_aggregate
via set_data() and they repaint. Colours track the app's dark/salmon theme.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QToolTip, QWidget

_ACCENT = QColor("#CE7D6B")
_EMPTY = QColor("#161b22")     # an empty cell / zero bar track
_DIM = QColor("#6b7280")       # labels


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class DailyBars(QWidget):
    """Per-day value bars (oldest left -> today right). set_data([(date, usd)]).
    A footer strip carries first/mid/last date ticks so the axis reads without
    hovering."""

    _FOOT = 15   # date-tick strip below the bars

    def __init__(self, parent=None, height: int = 74) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self._data: list = []

    def set_data(self, series: list) -> None:
        self._data = list(series or [])
        self.update()

    def _bar_w(self) -> tuple[float, float]:
        n = max(1, len(self._data))
        gap = 2.0
        return max(1.0, (self.width() - gap * (n - 1)) / n), gap

    def mouseMoveEvent(self, e) -> None:
        if not self._data:
            return
        bw, gap = self._bar_w()
        i = int(e.position().x() // (bw + gap))
        if 0 <= i < len(self._data):
            d, v = self._data[i]
            QToolTip.showText(e.globalPosition().toPoint(), f"{d:%b %d} · ${v:,.2f}", self)

    def paintEvent(self, _e) -> None:
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bar_area = self.height() - self._FOOT
        vmax = max((v for _, v in self._data), default=0.0) or 1.0
        bw, gap = self._bar_w()
        for i, (_d, v) in enumerate(self._data):
            x = i * (bw + gap)
            bh = max(1.0, (v / vmax) * (bar_area - 2)) if v else 1.0
            track = v > 0
            p.fillRect(QRectF(x, bar_area - bh, bw, bh), _ACCENT if track else _EMPTY)

        # date ticks: first (left), middle (centre), last (right)
        n = len(self._data)
        font = p.font()
        font.setPixelSize(9)
        p.setFont(font)
        p.setPen(_DIM)
        w = self.width()
        yf = bar_area + 1

        def tick(d) -> str:
            return f"{d:%b} {d.day}"

        p.drawText(QRectF(0, yf, w / 2, self._FOOT),
                   Qt.AlignLeft | Qt.AlignTop, tick(self._data[0][0]))
        if n >= 2:
            p.drawText(QRectF(w / 2, yf, w / 2, self._FOOT),
                       Qt.AlignRight | Qt.AlignTop, tick(self._data[-1][0]))
        if n >= 7:
            p.drawText(QRectF(0, yf, w, self._FOOT),
                       Qt.AlignHCenter | Qt.AlignTop, tick(self._data[n // 2][0]))
        p.end()


class ModelBreakdown(QWidget):
    """Value-by-model: one row per model — label, a proportional bar, the $value.
    set_data([(label, value)]); sorted desc, zero-value models dropped."""

    _ROW_H = 24
    _LABEL_W = 104
    _VALUE_W = 72

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list = []
        self.setFixedHeight(self._ROW_H)

    def set_data(self, rows: list) -> None:
        self._rows = sorted((r for r in (rows or []) if r[1] > 0),
                            key=lambda r: r[1], reverse=True)[:8]
        self.setFixedHeight(max(1, len(self._rows)) * self._ROW_H)
        self.update()

    def paintEvent(self, _e) -> None:
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        w = self.width()
        vmax = max(v for _, v in self._rows) or 1
        bar_max = max(10.0, w - self._LABEL_W - self._VALUE_W - 8)
        for i, (label, value) in enumerate(self._rows):
            y = i * self._ROW_H
            cy = y + self._ROW_H / 2
            p.setPen(_DIM)
            p.drawText(QRectF(0, y, self._LABEL_W - 8, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
            bw = max(2.0, (value / vmax) * bar_max)
            p.fillRect(QRectF(self._LABEL_W, cy - 5, bw, 10), _ACCENT)
            p.setPen(QColor("#e6edf3"))
            p.drawText(QRectF(w - self._VALUE_W, y, self._VALUE_W, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"${value:,.0f}")
        p.end()


class PercentBars(QWidget):
    """Per-item percent bars (0-100): label · bar to 100% · "N%". The bar warms
    from accent toward red as it fills. Empty data -> a dim hint line."""

    _ROW_H = 24
    _LABEL_W = 104
    _VALUE_W = 46

    def __init__(self, parent=None, empty_text: str = "—") -> None:
        super().__init__(parent)
        self._rows: list = []
        self._empty = empty_text
        self.setFixedHeight(self._ROW_H)

    def set_data(self, rows: list, sort: bool = True) -> None:
        rows = list(rows or [])
        if sort:
            rows.sort(key=lambda r: r[1], reverse=True)
        self._rows = rows[:8]
        self.setFixedHeight(max(1, len(self._rows)) * self._ROW_H)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        w = self.width()
        if not self._rows:
            p.setPen(_DIM)
            p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, self._empty)
            p.end()
            return
        bar_max = max(10.0, w - self._LABEL_W - self._VALUE_W - 8)
        for i, (label, pct) in enumerate(self._rows):
            y = i * self._ROW_H
            cy = y + self._ROW_H / 2
            frac = max(0.0, min(1.0, pct / 100.0))
            p.setPen(_DIM)
            p.drawText(QRectF(0, y, self._LABEL_W - 8, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
            p.fillRect(QRectF(self._LABEL_W, cy - 5, max(2.0, frac * bar_max), 10),
                       _lerp(_ACCENT, QColor("#c13434"), frac))
            p.setPen(QColor("#e6edf3"))
            p.drawText(QRectF(w - self._VALUE_W, y, self._VALUE_W, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"{int(pct)}%")
        p.end()


class CategoryBars(QWidget):
    """Rows of (label, pct, color_hex): label · proportional bar · 'N%'. Each row
    carries its own colour and bars scale to the largest row, so small slices stay
    visible. Used for the activity breakdown (coding/reading/…)."""

    _ROW_H = 22
    _LABEL_W = 96
    _VALUE_W = 40

    def __init__(self, parent=None, empty_text: str = "—") -> None:
        super().__init__(parent)
        self._rows: list = []
        self._empty = empty_text
        self.setFixedHeight(self._ROW_H)

    def set_data(self, rows: list) -> None:
        self._rows = [r for r in (rows or []) if r[1] > 0][:8]
        self.setFixedHeight(max(1, len(self._rows)) * self._ROW_H)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        w = self.width()
        if not self._rows:
            p.setPen(_DIM)
            p.drawText(self.rect(), Qt.AlignVCenter | Qt.AlignLeft, self._empty)
            p.end()
            return
        vmax = max(r[1] for r in self._rows) or 1
        bar_max = max(10.0, w - self._LABEL_W - self._VALUE_W - 8)
        for i, (label, pct, color) in enumerate(self._rows):
            y = i * self._ROW_H
            cy = y + self._ROW_H / 2
            p.setPen(_DIM)
            p.drawText(QRectF(0, y, self._LABEL_W - 8, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
            bw = max(2.0, (pct / vmax) * bar_max)
            p.fillRect(QRectF(self._LABEL_W, cy - 5, bw, 10), QColor(color))
            p.setPen(QColor("#e6edf3"))
            p.drawText(QRectF(w - self._VALUE_W, y, self._VALUE_W, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"{pct:.0f}%")
        p.end()


class WeekBars(QWidget):
    """This-week vs last-week dollar value as two horizontal bars (this week in
    accent, last week dimmed), each labelled with its total. set_data(this, last)."""

    _ROW_H = 26
    _LABEL_W = 66
    _VALUE_W = 78

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._this = 0.0
        self._last = 0.0
        self.setFixedHeight(self._ROW_H * 2 + 6)

    def set_data(self, this_v: float, last_v: float) -> None:
        self._this = float(this_v or 0.0)
        self._last = float(last_v or 0.0)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        w = self.width()
        vmax = max(self._this, self._last, 1.0)
        bar_max = max(10.0, w - self._LABEL_W - self._VALUE_W - 8)
        rows = (("This week", self._this, _ACCENT),
                ("Last week", self._last, _lerp(_ACCENT, _EMPTY, 0.55)))
        for i, (label, val, color) in enumerate(rows):
            y = i * self._ROW_H + 2
            cy = y + self._ROW_H / 2
            p.setPen(_DIM)
            p.drawText(QRectF(0, y, self._LABEL_W - 6, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
            bw = max(2.0, (val / vmax) * bar_max)
            p.fillRect(QRectF(self._LABEL_W, cy - 6, bw, 12), color)
            p.setPen(QColor("#e6edf3"))
            p.drawText(QRectF(w - self._VALUE_W, y, self._VALUE_W, self._ROW_H),
                       Qt.AlignVCenter | Qt.AlignRight, f"${val:,.0f}")
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
        self.setMouseTracking(True)

    def set_data(self, grid: list) -> None:
        self._grid = grid or [[0] * 24 for _ in range(7)]
        self.update()

    def mouseMoveEvent(self, e) -> None:
        x, y = e.position().x(), e.position().y()
        if x < self._LABEL_W or y >= 7 * self._CELL_H:
            return
        cell_w = (self.width() - self._LABEL_W) / 24.0
        if cell_w <= 0:        # widget pinched to <= the label gutter
            return
        col = int((x - self._LABEL_W) / cell_w)
        row = int(y / self._CELL_H)
        if 0 <= row < 7 and 0 <= col < 24:
            n = self._grid[row][col]
            QToolTip.showText(
                e.globalPosition().toPoint(),
                f"{self._DOW[row]} {col:02d}:00 · {n} turn{'' if n == 1 else 's'}", self)

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
