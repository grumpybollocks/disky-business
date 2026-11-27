# SPDX-License-Identifier: GPL-3.0-or-later
"""The Speed tab: runs `disky speed`, draws the two sequential runs as a graph, lists the four results."""
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .tabbase import TabBase

ROWS = (("seq_write", "Writing in one go"), ("seq_read", "Reading in one go"),
        ("rand_read", "Small reads, random places"), ("rand_write", "Small writes, random places"))
WORDS = dict(ROWS)
LINES = {"seq_write": "written", "seq_read": "good"}      # graph line -> theme state colour
BAR_COLOUR = {"seq_write": "written", "seq_read": "good", "rand_read": "good", "rand_write": "written"}
BAR_TOP = 1000                                            # MiB/s at the right end of a bar, unless one is faster


def result_text(r):
    text = f"{r['mib_per_s']:g} MiB/s"
    if r.get("iops") is not None:
        text += f"   ({r['iops']:,} per second".replace(",", " ") + (")" if r.get("direct") else ", through the system's cache)")
    if r.get("read_from_drive") is False:
        text += "   (partly from memory: not the drive's real speed)"
    return text


class _Value:
    """One result as text (kept apart from the drawing, so it can be read without a screen)."""

    def __init__(self, owner):
        self._text, self._owner = "-", owner

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text
        self._owner.update()


class SpeedBars(QWidget):
    """The four results as big numbers, each with a bar. Bars grow on a ten-fold scale, because small random
    writes and reading in one go lie a thousand times apart and both must stay visible."""
    ROW = 46

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.speeds = {}                                # test -> MiB/s
        self.values = {test: _Value(self) for test, _ in ROWS}
        self.setMinimumHeight(self.ROW * len(ROWS) + 8)
        self.setMinimumWidth(360)

    def clear(self):
        self.speeds = {}
        for v in self.values.values():
            v.setText("-")

    def set_result(self, test, speed, text):
        if test in self.values:
            self.speeds[test] = max(0.0, float(speed or 0))
            self.values[test].setText(text)

    def fraction(self, test):
        """How much of its track a bar fills, 0 to 1."""
        speed = self.speeds.get(test)
        if not speed:
            return 0.0
        top = max([BAR_TOP] + list(self.speeds.values()))
        return min(1.0, math.log10(1 + speed) / math.log10(1 + top))

    def paintEvent(self, _event):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ink, faint = QColor(t.text), QColor(t.text)
        faint.setAlpha(40)
        big = QFont(self.font())
        big.setPixelSize(max(15, self.font().pixelSize() + 7))
        big.setBold(True)
        label_w = min(210, self.width() // 3)
        number_w = min(300, self.width() // 3)
        bar_x, bar_w = label_w + 8, max(40, self.width() - label_w - number_w - 24)
        for row, (test, words) in enumerate(ROWS):
            y = 4 + row * self.ROW
            p.setFont(self.font())
            p.setPen(ink)
            p.drawText(0, y, label_w, self.ROW - 6, Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, words)
            track_y, track_h = y + self.ROW // 2 - 9, 14
            p.setPen(Qt.NoPen)
            p.setBrush(faint)
            p.drawRoundedRect(bar_x, track_y, bar_w, track_h, 4, 4)
            filled = int(bar_w * self.fraction(test))
            if filled > 0:
                p.setBrush(QColor(t.states[BAR_COLOUR[test]]))
                p.drawRoundedRect(bar_x, track_y, max(8, filled), track_h, 4, 4)
            number, _, rest = self.values[test].text().partition("   ")
            p.setPen(ink)
            p.setFont(big)
            p.drawText(bar_x + bar_w + 12, y, number_w, 24, Qt.AlignLeft | Qt.AlignVCenter, number)
            p.setFont(self.font())
            p.drawText(bar_x + bar_w + 12, y + 22, number_w, self.ROW - 26, Qt.AlignLeft | Qt.AlignTop,
                       rest.strip().strip("()"))
        p.end()


class SpeedGraph(QWidget):
    """Speed along the file, one line per sequential run."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.samples = {k: [] for k in LINES}       # test -> [(offset_bytes, mib_per_s)]
        self.total = 0
        self.setMinimumHeight(140)

    def clear(self):
        self.samples = {k: [] for k in LINES}
        self.total = 0
        self.update()

    def add(self, test, offset, speed):
        if test in self.samples:
            self.samples[test].append((offset, speed))
            self.update()

    def top(self):
        """The speed at the top of the graph: a round number above the fastest sample."""
        peak = max([s for pts in self.samples.values() for _, s in pts], default=0)
        if peak <= 0:
            return 100
        unit = 10 ** math.floor(math.log10(peak / 4))
        for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
            if 4 * m * unit >= peak:
                return 4 * m * unit
        return 40 * unit

    def points(self, test):
        """Pixel positions of one line (used by the drawing and by the tests)."""
        left, top, w, h = 64, 14, max(1, self.width() - 84), max(1, self.height() - 44)
        span, ceil = max(self.total, 1), self.top()
        return [QPointF(left + w * off / span, top + h * (1 - s / ceil)) for off, s in self.samples[test]]

    def smooth(self, test):
        """The line as drawn: each point averaged with its neighbours, so single jumps do not make it jagged."""
        pts = self.points(test)
        if len(pts) < 3:
            return pts
        ys = [p.y() for p in pts]
        avg = [ys[0]] + [(ys[i - 1] + ys[i] + ys[i + 1]) / 3 for i in range(1, len(ys) - 1)] + [ys[-1]]
        return [QPointF(pt.x(), y) for pt, y in zip(pts, avg)]

    def paintEvent(self, _event):
        t = self.theme
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.map_bg))
        left, top, w, h = 64, 14, max(1, self.width() - 84), max(1, self.height() - 44)
        ink, ceil = QColor(t.field_text), self.top()
        faint = QColor(ink)
        faint.setAlpha(50)
        for i in range(5):
            y = top + h * i / 4
            p.setPen(QPen(faint, 1))
            p.drawLine(left, int(y), left + w, int(y))
            p.setPen(ink)
            p.drawText(0, int(y) - 9, left - 8, 18, Qt.AlignRight | Qt.AlignVCenter, f"{ceil * (4 - i) / 4:g}")
        p.drawText(left, top + h + 6, w, 20, Qt.AlignLeft, "start of the file")
        p.drawText(left, top + h + 6, w, 20, Qt.AlignRight, "end   (MiB/s up the side)")
        p.setRenderHint(QPainter.Antialiasing)
        base = top + h
        for test, state in LINES.items():
            pts = self.smooth(test)
            colour = QColor(t.states[state])
            if len(pts) > 1:
                area = QPainterPath(QPointF(pts[0].x(), base))      # the filled shape under the line
                for pt in pts:
                    area.lineTo(pt)
                area.lineTo(pts[-1].x(), base)
                area.closeSubpath()
                fill = QColor(colour)
                fill.setAlpha(45)
                p.fillPath(area, fill)
                p.setPen(QPen(colour, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPolyline(QPolygonF(pts))
            p.setPen(QPen(colour, 2))
            p.setBrush(colour)
            for pt in pts[-1:]:
                p.drawEllipse(pt, 3.5, 3.5)
        p.end()


class SpeedTab(TabBase):
    title = "Speed"
    NOTE = "Pick a drive above. The speed test writes one 1 GiB file there, measures, and removes it again."

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.results = {}
        self.graph = SpeedGraph(theme)
        frame = self.framed(self.graph)
        key = self.legend([(state, WORDS[test]) for test, state in LINES.items()])

        self.bars = SpeedBars(theme)                    # the four results, big, each with a bar
        self.values = self.bars.values
        caption = QLabel("Speed along the file, for the two runs in one go")
        caption.setObjectName("heading")

        self.status = QLabel("Pick a drive, then press Start.")
        self.status.setWordWrap(True)                   # a long reason must never force the window wide
        self.status.setObjectName("status")
        self.go = QPushButton("Start")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.start_or_stop)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.go)

        right = QVBoxLayout()
        right.addWidget(self.bars)
        right.addSpacing(6)
        right.addWidget(caption)
        right.addWidget(frame, 1)
        right.addLayout(key)
        right.addLayout(bottom)
        self.assemble(right)
        self.set_theme(theme)
        self.update_buttons()

    def set_theme(self, theme):
        self.theme = self.graph.theme = self.bars.theme = theme
        self.bars.update()
        self.paint_legend(theme.states)
        self.graph.update()

    def on_folder(self, _folder, hint):
        if self.mode is None and hint:
            self.status.setText(hint)
        self.update_buttons()

    def update_buttons(self):
        self.go.setText("Stop" if self.mode else "Start")
        self.go.setEnabled(bool(self.folder))
        self.panel.set_busy(self.mode is not None)

    def start_or_stop(self):
        if self.mode:
            self.status.setText("Stopping...")
            self.runner.stop()
        elif self.folder:
            self.begin()

    def begin(self, *extra):
        self.error, self.results = None, {}
        self.graph.clear()
        self.bars.clear()
        self.status.setText("Starting...")
        self.set_busy("speed")
        self.runner.start("speed", self.folder, *extra)

    def on_event(self, ev):
        kind = ev.get("event")
        if kind == "speed_phase":
            if ev.get("total_bytes"):
                self.graph.total = ev["total_bytes"]
            self.status.setText("Measuring: " + WORDS.get(ev.get("test"), "working").lower() + "...")
        elif kind == "speed_sample":
            self.graph.add(ev.get("test"), ev.get("offset_bytes", 0), ev.get("mib_per_s", 0))
        elif kind == "speed_result" and ev.get("test") in self.values:
            self.results[ev["test"]] = ev
            self.bars.set_result(ev["test"], ev.get("mib_per_s"), result_text(ev))
        elif kind == "warning":
            self.tip(ev.get("message", ""))
        elif kind == "error":
            self.error = ev.get("message") or "The engine reported an error."

    def on_finished(self, code, crash):
        if crash:
            self.status.setText("The speed test did not finish. " + crash)
        elif self.error:
            self.status.setText(self.error)
        elif code == 130:
            self.status.setText("Stopped. The temporary file was removed.")
        elif len(self.results) == len(ROWS):
            self.status.setText("Done. The temporary file was removed.")
        else:
            self.status.setText(f"The speed test ended without all results (exit {code}).")
        self.set_busy(None)

