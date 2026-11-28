# SPDX-License-Identifier: GPL-3.0-or-later
"""Small icons for the main buttons, drawn here so they take any look's colour. Used by the modern looks.

To give a button an icon, add its text to BUTTON_ICONS. To add a shape, write a function that draws it in a
24 x 24 box and add it to SHAPES.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton


def _play(p):
    p.drawPolygon(QPolygonF([QPointF(8, 5), QPointF(19, 12), QPointF(8, 19)]))


def _stop(p):
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)


def _refresh(p):
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 290 * 16)
    p.setBrush(p.pen().color())
    p.drawPolygon(QPolygonF([QPointF(14.5, 3), QPointF(20.5, 6.5), QPointF(14.5, 10)]))


def _eject(p):
    p.drawPolygon(QPolygonF([QPointF(12, 5), QPointF(19, 14), QPointF(5, 14)]))
    p.drawRect(QRectF(5, 17, 14, 2))


def _bin(p):
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(5, 7), QPointF(19, 7))
    p.drawLine(QPointF(10, 4.5), QPointF(14, 4.5))
    p.drawRoundedRect(QRectF(7, 7, 10, 13), 1.5, 1.5)
    p.drawLine(QPointF(10.5, 10), QPointF(10.5, 17))
    p.drawLine(QPointF(13.5, 10), QPointF(13.5, 17))


def _search(p):
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(5, 5, 10, 10))
    p.drawLine(QPointF(13.5, 13.5), QPointF(19, 19))


def _tick(p):
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(5, 12.5))
    path.lineTo(10, 17.5)
    path.lineTo(19, 7)
    p.drawPath(path)


def _plus(p):
    p.drawLine(QPointF(12, 5.5), QPointF(12, 18.5))
    p.drawLine(QPointF(5.5, 12), QPointF(18.5, 12))


def _down(p):
    p.drawLine(QPointF(12, 4.5), QPointF(12, 15))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(7, 10.5))
    path.lineTo(12, 15.5)
    path.lineTo(17, 10.5)
    p.drawPath(path)
    p.drawLine(QPointF(5, 19.5), QPointF(19, 19.5))


def _up(p):
    p.drawLine(QPointF(12, 15.5), QPointF(12, 5))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(7, 9.5))
    path.lineTo(12, 4.5)
    path.lineTo(17, 9.5)
    p.drawPath(path)
    p.drawLine(QPointF(5, 19.5), QPointF(19, 19.5))


def _folder(p):
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(4, 7))
    path.lineTo(9.5, 7)
    path.lineTo(11.5, 9.5)
    path.lineTo(20, 9.5)
    path.lineTo(20, 18.5)
    path.lineTo(4, 18.5)
    path.closeSubpath()
    p.drawPath(path)


SHAPES = {"play": _play, "stop": _stop, "refresh": _refresh, "eject": _eject, "bin": _bin, "search": _search,
          "tick": _tick, "plus": _plus, "down": _down, "up": _up, "folder": _folder}

# What a button says -> its icon. A button whose text changes (Start / Stop) gets the icon that fits.
BUTTON_ICONS = {
    "Start test": "play", "Stop test": "stop", "Start": "play", "Start speed test": "play", "Stop": "stop",
    "Check test files again": "tick", "Delete test files": "bin",
    "Scan": "search", "Defragment all": "play",
    "Check": "tick", "Repair": "plus", "Trim now": "play",
    "Read with admin password": "search", "Deep scan": "search", "Short self-test": "play", "Long self-test": "play", "Stop self-test": "stop", "Read again": "refresh",
    "Refresh": "refresh", "Mount": "down", "Unmount": "up", "Safely remove": "eject", "Other folder...": "folder",
}


def _picture(shape, colour, size):
    pix = QPixmap(size * 2, size * 2)                   # drawn at twice the size, so it stays sharp
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size * 2 / 24, size * 2 / 24)
    c = QColor(colour)
    p.setPen(QPen(c, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(c)
    shape(p)
    p.end()
    return pix


def icon(name, colour, off_colour=None, size=18):
    """One icon in one colour, with a dimmer one for a button that cannot be pressed now. Unknown names give
    an empty icon."""
    shape = SHAPES.get(name)
    if shape is None:
        return QIcon()
    out = QIcon(_picture(shape, colour, size))
    if off_colour:
        out.addPixmap(_picture(shape, off_colour, size), QIcon.Disabled)
    return out


def dress(root, theme):
    """Give every button under `root` the icon its text calls for, in the look's colour; none in the looks
    that have no icons. Called after a look change and whenever buttons change what they say."""
    for b in root.findChildren(QPushButton):
        name = BUTTON_ICONS.get(b.text()) if theme.flat else None
        colour = theme.title_text if b.objectName() == "primary" else theme.text
        b.setIcon(icon(name, colour, theme.faint or None) if name else QIcon())
