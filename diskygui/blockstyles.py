# SPDX-License-Identifier: GPL-3.0-or-later
"""How one square is drawn. To add a style: write a draw function and put a BlockStyle in STYLES."""
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap


@dataclass(frozen=True)
class BlockStyle:
    name: str
    label: str
    size: int       # side of one square, pixels
    gap: int        # space between squares, pixels
    draw: Callable  # draw(painter, size, colour) paints one square at 0,0

    @property
    def cell(self):
        return self.size + self.gap

    def tile(self, colour, background):
        """One ready-drawn square as a picture; the map stamps these."""
        pix = QPixmap(self.size, self.size)
        pix.fill(QColor(background))
        p = QPainter(pix)
        self.draw(p, self.size, QColor(colour))
        p.end()
        return pix


def _bevel(p, s, c):
    p.fillRect(0, 0, s, s, c)
    lit, shade = c.lighter(150), c.darker(180)
    p.fillRect(0, 0, s, 2, lit)
    p.fillRect(0, 0, 2, s, lit)
    p.fillRect(0, s - 2, s, 2, shade)
    p.fillRect(s - 2, 0, 2, s, shade)


def _flat(p, s, c):
    p.fillRect(0, 0, s, s, c)


def _soft(p, s, c):
    """A flat square with rounded corners, for the modern looks."""
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(0, 0, s, s, 3, 3)


STYLES = {s.name: s for s in (
    BlockStyle("soft", "Rounded squares", 13, 2, _soft),
    BlockStyle("bevel", "Bevelled squares", 13, 1, _bevel),
    BlockStyle("fine", "Small fine squares", 6, 1, _flat),
)}
