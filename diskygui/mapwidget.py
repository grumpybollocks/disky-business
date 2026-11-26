# SPDX-License-Identifier: GPL-3.0-or-later
"""The block map on screen. Draws what MapModel holds, in the chosen theme and block style.

Squares are kept in an off-screen picture; only squares whose state changed are redrawn, so a repaint is one
blit. Room for later layers: square_at() and blocks_of() already answer "what is under the mouse".
"""
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .blockstyles import STYLES
from .mapmodel import NAMES
from .theme import DEFAULT_THEME, THEMES

MARGIN = 4


class BlockMap(QWidget):
    def __init__(self, model, theme=None, style=None, parent=None):
        super().__init__(parent)
        self.model = model
        self._pix = None
        self._shown = np.zeros(0, np.uint8)
        self._bounds = np.zeros(1, np.int64)
        self._seen = -1
        self.cols = self.rows = 0
        self.placeholder = True
        self.set_look(theme or THEMES[DEFAULT_THEME], style)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(33)

    # -- look ----------------------------------------------------------------------------------------------
    def set_look(self, theme, style=None, states=None):
        """Change colour scheme and / or block style. `style` is a name from blockstyles.STYLES;
        `states` replaces the theme's state colours (the Defrag map uses its own)."""
        self.theme = theme
        self.style = STYLES[style or theme.block_style]
        self.colours = dict(theme.states, **(states or {}))
        # One ready-drawn cell (square plus gap) per state, as pixel arrays; the last one is empty background.
        self._cells = np.stack([self._cell(self.colours[n]) for n in NAMES] + [self._cell(None)])
        self._pix = None
        self.refresh()

    def _cell(self, colour):
        c = self.style.cell
        img = QImage(c, c, QImage.Format_ARGB32)
        img.fill(QColor(self.theme.map_bg))
        if colour is not None:
            p = QPainter(img)
            p.drawPixmap(0, 0, self.style.tile(colour, self.theme.map_bg))
            p.end()
        return np.frombuffer(img.constBits(), np.uint8).reshape(c, c, 4).copy()

    # -- drawing -------------------------------------------------------------------------------------------
    def _xy(self, i):
        c = self.style.cell
        return MARGIN + (i % self.cols) * c, MARGIN + (i // self.cols) * c

    def refresh(self):
        """Bring the picture up to date with the model. Cheap when nothing changed.

        The whole map is put together as one pixel array and painted with a single call. (Painting it square
        by square, thousands of calls, crashed the Qt binding on Python 3.11.)
        """
        if self._pix is not None and self._seen == self.model.version and self._pix.size() == self.size():
            return
        c = self.style.cell
        self.cols = max(0, (self.width() - 2 * MARGIN + self.style.gap) // c)
        self.rows = max(0, (self.height() - 2 * MARGIN + self.style.gap) // c)
        total = self.cols * self.rows
        values, self._bounds = self.model.squares(total)
        self.placeholder = len(values) == 0          # nothing known yet: show the empty grid
        if self.placeholder:
            values = np.zeros(total, np.uint8)
        self._pix = QPixmap(self.size())
        self._pix.fill(QColor(self.theme.map_bg))
        if total:
            index = np.full(total, len(NAMES), np.intp)          # background where there is no square
            index[:len(values)] = values
            grid = self._cells[index.reshape(self.rows, self.cols)]             # rows, cols, cell, cell, 4
            pixels = np.ascontiguousarray(grid.transpose(0, 2, 1, 3, 4).reshape(self.rows * c, self.cols * c, 4))
            image = QImage(pixels.data, self.cols * c, self.rows * c, self.cols * c * 4, QImage.Format_ARGB32)
            p = QPainter(self._pix)
            p.drawImage(MARGIN, MARGIN, image)
            p.end()
        self._shown = values
        self._seen = self.model.version
        self.update()

    def paintEvent(self, _event):
        if self._pix is None or self._pix.size() != self.size():
            self.refresh()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pix)
        p.end()

    def resizeEvent(self, _event):
        self.refresh()

    # -- for later layers (clicking a square) ----------------------------------------------------------------
    def square_at(self, x, y):
        """Index of the square at a widget position, or None."""
        c = self.style.cell
        col, row = (x - MARGIN) // c, (y - MARGIN) // c
        if x < MARGIN or y < MARGIN or col >= self.cols or (x - MARGIN) % c >= self.style.size \
                or (y - MARGIN) % c >= self.style.size:
            return None
        i = row * self.cols + col
        return i if i < len(self._shown) and not self.placeholder else None

    def blocks_of(self, i):
        """(first block, count) that square i stands for."""
        return int(self._bounds[i]), max(1, int(self._bounds[i + 1] - self._bounds[i]))
