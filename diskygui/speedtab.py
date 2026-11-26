# SPDX-License-Identifier: GPL-3.0-or-later
"""The Speed tab: runs `disky speed`, draws the two sequential runs as a graph, lists the four results."""
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .tabbase import TabBase

ROWS = (("seq_write", "Writing in one go"), ("seq_read", "Reading in one go"),
        ("rand_read", "Small reads, random places"), ("rand_write", "Small writes, random places"))
WORDS = dict(ROWS)
LINES = {"seq_write": "written", "seq_read": "good"}      # graph line -> theme state colour


def result_text(r):
    text = f"{r['mib_per_s']:g} MiB/s"
    if r.get("iops") is not None:
        text += f"   ({r['iops']:,} per second".replace(",", " ") + (")" if r.get("direct") else ", through the system's cache)")
    if r.get("read_from_drive") is False:
        text += "   (partly from memory: not the drive's real speed)"
    return text


class SpeedGraph(QWidget):
    """Speed along the file, one line per sequential run."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.samples = {k: [] for k in LINES}       # test -> [(offset_bytes, mib_per_s)]
        self.total = 0
        self.setMinimumHeight(200)

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
        for test, state in LINES.items():
            pts = self.points(test)
            p.setPen(QPen(QColor(t.states[state]), 3))
            if len(pts) > 1:
                p.drawPolyline(QPolygonF(pts))
            for pt in pts[-1:] if len(pts) > 1 else pts:
                p.drawEllipse(pt, 3, 3)
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

        table = QFrame()
        table.setObjectName("verdict")
        grid = QGridLayout(table)
        self.values = {}
        for row, (test, words) in enumerate(ROWS):
            name = QLabel(words)
            value = QLabel("-")
            value.setObjectName("speedvalue")
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self.values[test] = value
        grid.setColumnStretch(1, 1)

        self.status = QLabel("Choose a folder, then press Start.")
        self.status.setObjectName("status")
        self.go = QPushButton("Start")
        self.go.clicked.connect(self.start_or_stop)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.go)

        right = QVBoxLayout()
        right.addWidget(frame, 1)
        right.addLayout(key)
        right.addWidget(table)
        right.addLayout(bottom)
        self.assemble(right)
        self.set_theme(theme)
        self.update_buttons()

    def set_theme(self, theme):
        self.theme = self.graph.theme = theme
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
        for value in self.values.values():
            value.setText("-")
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
            self.values[ev["test"]].setText(result_text(ev))
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

