"""Small high-DPI vector icon system for the NuCytoSeg desktop shell."""
from __future__ import annotations

from qtpy.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from qtpy.QtGui import (
    QColor,
    QIcon,
    QIconEngine,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)


def _polyline(painter: QPainter, *coordinates: float) -> None:
    points = [
        QPointF(coordinates[index], coordinates[index + 1])
        for index in range(0, len(coordinates), 2)
    ]
    painter.drawPolyline(QPolygonF(points))


def _line(painter: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
    painter.drawLine(QLineF(x1, y1, x2, y2))


class _IconEngine(QIconEngine):
    """Paint simple line icons at the size requested by Qt."""

    def __init__(self, name: str, accent: bool = False):
        super().__init__()
        self.name = name
        self.accent = accent

    def clone(self):
        return _IconEngine(self.name, self.accent)

    def pixmap(self, size, mode, state):
        """Return a transparent raster at Qt's requested device-pixel size."""
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap

    def paint(self, painter: QPainter, rect: QRect, mode, state) -> None:
        if mode == QIcon.Mode.Disabled:
            color = QColor("#526171")
        elif self.accent or state == QIcon.State.On:
            color = QColor("#63d9d0")
        elif mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            color = QColor("#c5f5f1")
        else:
            color = QColor("#a8bbcc")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(rect.width(), rect.height())
        painter.translate(
            rect.x() + (rect.width() - side) / 2,
            rect.y() + (rect.height() - side) / 2,
        )
        painter.scale(side / 24.0, side / 24.0)
        pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._draw(painter)
        painter.restore()

    def _draw(self, painter: QPainter) -> None:
        name = self.name
        if name in {"eye", "eye_off"}:
            path = QPainterPath()
            path.moveTo(2.5, 12)
            path.cubicTo(6, 6.5, 9, 5, 12, 5)
            path.cubicTo(15, 5, 18, 6.5, 21.5, 12)
            path.cubicTo(18, 17.5, 15, 19, 12, 19)
            path.cubicTo(9, 19, 6, 17.5, 2.5, 12)
            painter.drawPath(path)
            painter.drawEllipse(QRectF(9, 9, 6, 6))
            if name == "eye_off":
                _line(painter, 3.5, 3.5, 20.5, 20.5)
            return
        if name in {"folder", "open"}:
            path = QPainterPath()
            path.moveTo(3, 7)
            path.lineTo(9, 7)
            path.lineTo(11, 9)
            path.lineTo(21, 9)
            path.lineTo(19, 19)
            path.lineTo(3, 19)
            path.closeSubpath()
            painter.drawPath(path)
            if name == "open":
                _line(painter, 12, 14, 18, 14)
                _line(painter, 15, 11, 18, 14)
                _line(painter, 15, 17, 18, 14)
            return
        if name == "sliders":
            for y, x in ((6, 8), (12, 15), (18, 10)):
                _line(painter, 3, y, 21, y)
                painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
            return
        if name == "segment":
            painter.drawRoundedRect(QRectF(3, 3, 18, 18), 4, 4)
            painter.drawEllipse(QRectF(6, 7, 5, 5))
            painter.drawEllipse(QRectF(13, 11, 5, 5))
            return
        if name == "review":
            _line(painter, 5, 18, 15.5, 7.5)
            _line(painter, 14, 6, 18, 10)
            _line(painter, 4, 20, 8, 19)
            _line(painter, 16, 18, 18, 20)
            _line(painter, 18, 20, 22, 15)
            return
        if name == "link":
            painter.drawRoundedRect(QRectF(3, 8, 10, 7), 3, 3)
            painter.drawRoundedRect(QRectF(11, 8, 10, 7), 3, 3)
            _line(painter, 9, 11.5, 15, 11.5)
            return
        if name in {"bars", "quality"}:
            _line(painter, 3, 20, 21, 20)
            if name == "bars":
                painter.drawRect(QRectF(5, 12, 3, 8))
                painter.drawRect(QRectF(11, 8, 3, 12))
                painter.drawRect(QRectF(17, 4, 3, 16))
            else:
                _polyline(painter, 3, 16, 8, 11, 12, 14, 18, 6, 21, 8)
            return
        if name == "export":
            painter.drawRoundedRect(QRectF(4, 7, 13, 13), 2, 2)
            painter.drawLine(12, 13, 21, 4)
            painter.drawLine(15, 4, 21, 4)
            painter.drawLine(21, 4, 21, 10)
            return
        if name == "view2d":
            painter.drawRect(QRectF(4, 5, 16, 14))
            painter.drawLine(8, 15, 12, 11)
            painter.drawLine(12, 11, 17, 16)
            return
        if name == "view3d":
            _polyline(painter, 12, 3, 21, 8, 12, 13, 3, 8, 12, 3)
            _polyline(painter, 3, 8, 3, 17, 12, 22, 21, 17, 21, 8)
            _line(painter, 12, 13, 12, 22)
            return
        if name == "pan":
            _line(painter, 12, 3, 12, 21)
            _line(painter, 3, 12, 21, 12)
            _polyline(painter, 9, 6, 12, 3, 15, 6)
            _polyline(painter, 9, 18, 12, 21, 15, 18)
            _polyline(painter, 6, 9, 3, 12, 6, 15)
            _polyline(painter, 18, 9, 21, 12, 18, 15)
            return
        if name in {"select", "pick"}:
            painter.drawEllipse(QRectF(5, 5, 14, 14))
            _line(painter, 12, 2, 12, 8)
            _line(painter, 12, 16, 12, 22)
            _line(painter, 2, 12, 8, 12)
            _line(painter, 16, 12, 22, 12)
            if name == "pick":
                painter.drawEllipse(QRectF(10, 10, 4, 4))
            return
        if name == "paint":
            _line(painter, 6, 18, 16, 8)
            _polyline(painter, 14, 6, 18, 10, 20, 6, 18, 4, 14, 6)
            _line(painter, 4, 20, 8, 19)
            return
        if name == "erase":
            _polyline(painter, 4, 16, 13, 5, 21, 12, 13, 20, 7, 20, 4, 16)
            _line(painter, 10, 20, 18, 20)
            return
        if name == "fit":
            for x1, y1, x2, y2, x3, y3 in (
                (9, 4, 4, 4, 4, 9),
                (15, 4, 20, 4, 20, 9),
                (4, 15, 4, 20, 9, 20),
                (20, 15, 20, 20, 15, 20),
            ):
                _polyline(painter, x1, y1, x2, y2, x3, y3)
            return
        if name in {"collapse", "expand"}:
            points = (
                (15, 5, 8, 12, 15, 19)
                if name == "collapse"
                else (9, 5, 16, 12, 9, 19)
            )
            _polyline(painter, *points)
            return
        if name == "focus":
            for points in (
                (9, 4, 4, 4, 4, 9),
                (15, 4, 20, 4, 20, 9),
                (4, 15, 4, 20, 9, 20),
                (20, 15, 20, 20, 15, 20),
            ):
                _polyline(painter, *points)
            return
        if name in {"restore", "reset"}:
            painter.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 290 * 16)
            _polyline(painter, 4, 7, 4, 13, 9, 10)
            if name == "reset":
                _line(painter, 12, 8, 12, 13)
            return
        if name == "image":
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
            painter.drawEllipse(QRectF(6, 7, 3, 3))
            _polyline(painter, 4, 18, 10, 12, 14, 16, 17, 13, 21, 18)
            return
        if name == "labels":
            painter.drawEllipse(QRectF(3, 5, 8, 8))
            painter.drawEllipse(QRectF(12, 11, 8, 8))
            return
        painter.drawEllipse(QRectF(5, 5, 14, 14))


def icon(name: str, *, accent: bool = False) -> QIcon:
    """Return a reusable vector QIcon with dark-theme-aware states."""
    return QIcon(_IconEngine(name, accent))
