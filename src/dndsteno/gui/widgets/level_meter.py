"""Horizontal peak meter on a dB scale with a 'too quiet' marker and peak hold."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from dndsteno.fmt import FLOOR_DB, QUIET_DB, peak_db
from dndsteno.gui import theme

HOLD_SECONDS = 1.5
DECAY_DB_PER_SEC = 30


class LevelMeter(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.db = FLOOR_DB
        self.hold_db = FLOOR_DB
        self._hold_at = 0.0
        self._last = time.monotonic()

    def set_peak(self, peak: float) -> None:
        now = time.monotonic()
        db = peak_db(peak)
        # Fall smoothly instead of flickering between blocks.
        self.db = max(db, self.db - DECAY_DB_PER_SEC * (now - self._last))
        self._last = now
        if db >= self.hold_db or now - self._hold_at > HOLD_SECONDS:
            self.hold_db, self._hold_at = db, now
        self.update()

    def reset(self) -> None:
        self.db = self.hold_db = FLOOR_DB
        self.update()

    def _x(self, db: float, width: float) -> float:
        return width * (db - FLOOR_DB) / -FLOOR_DB

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 14
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(QRectF(0, 0, w, h), 4, 4)

        x = self._x(self.db, w)
        color = theme.WARNING if self.db < QUIET_DB else theme.SUCCESS if self.db < -6 else theme.DANGER
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0, 0, x, h), 4, 4)

        p.setPen(QPen(QColor(theme.TEXT), 2))
        hx = self._x(self.hold_db, w)
        p.drawLine(int(hx), 2, int(hx), h - 2)

        # Scale: quiet threshold marker and labels
        qx = self._x(QUIET_DB, w)
        p.setPen(QPen(QColor(theme.MUTED), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(qx), 0, int(qx), h)
        p.setPen(QColor(theme.MUTED))
        font = p.font()
        font.setPointSizeF(7.5)
        p.setFont(font)
        for db in range(int(FLOOR_DB), 1, 10):
            tx = self._x(db, w)
            if db == FLOOR_DB:
                rect, align = QRectF(0, h, 60, 14), Qt.AlignmentFlag.AlignLeft
            elif db == 0:
                rect, align = QRectF(w - 60, h, 60, 14), Qt.AlignmentFlag.AlignRight
            else:
                rect, align = QRectF(tx - 30, h, 60, 14), Qt.AlignmentFlag.AlignHCenter
            p.drawText(rect, align | Qt.AlignmentFlag.AlignBottom, f"{db} дБ" if db == 0 else str(db))
        p.end()
