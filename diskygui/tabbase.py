# SPDX-License-Identifier: GPL-3.0-or-later
"""What every tab has: the drive panel on the left, an engine runner, a busy signal, a theme.

A new tab subclasses TabBase, sets `title` and `NOTE`, builds its right-hand side, and
fills in the hooks at the bottom. Put the class in window.MainWindow.TABS and it appears.
"""
import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QVBoxLayout,
                               QWidget)

from .drivepanel import DrivePanel
from .drives import TEST_FOLDER
from .runner import EngineRunner


class ProgressLine(QWidget):
    """A running job, shown properly: what it is doing and how far, a bar, and two facts under it (for
    example the speed and the time left)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.title = QLabel("")
        self.title.setObjectName("status")
        self.percent = QLabel("")
        self.percent.setObjectName("status")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.left, self.right = QLabel(""), QLabel("")
        self.left.setObjectName("progressnote")
        self.right.setObjectName("progressnote")
        top, under = QHBoxLayout(), QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.percent)
        under.addWidget(self.left, 1)
        under.addWidget(self.right)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        col.addLayout(top)
        col.addWidget(self.bar)
        col.addLayout(under)
        col.addStretch(1)

    stopping = ""                       # while set, the title says so (Stop was pressed; the job ends soon)

    def show_stopping(self, words="Stopping..."):
        self.stopping = words
        self.title.setText(words)

    def show_progress(self, title, percent=None, left="", right=""):
        """percent None: how far is not known yet (the bar runs to and fro)."""
        self.title.setText(self.stopping or title)
        if percent is None:
            self.bar.setRange(0, 0)
            self.percent.setText("")
        else:
            value = max(0, min(100, int(percent)))
            self.bar.setRange(0, 100)
            self.bar.setValue(value)
            self.percent.setText(f"{value} %")
        self.left.setText(left)
        self.right.setText(right)


class TabBase(QWidget):
    title = ""
    NOTE = ""                           # the hint under the folder box
    FOLDER_HEADING = "Works in"         # above the line that shows where on the drive the work is done
    USES_FOLDER = True                  # False: the tab works on the drive itself; no folder is shown
    busy_changed = Signal(bool)

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.tip = tip or (lambda text: None)           # shows a line in the window's tip bar
        self.runner = runner or EngineRunner(parent=self)
        self.runner.event.connect(self._safe_event)
        self.runner.finished.connect(self._safe_finished)
        self.problems_seen = []
        self.runner.problem.connect(self.problems_seen.append)
        self.mode = None                                # None when idle, else the name of the job
        self.continuing = False                         # set by a tab whose job goes on with another engine run
        self.error = None
        self.swatches = []                              # (label widget, colour key) of the legend
        # The tab's progress bars, made with progress_line(). Kept in a list on purpose: walking Qt's children
        # from Python (findChildren) made the tests crash at clean-up (2026-11-27).
        self.progress_lines = []
        self.confirm = lambda text: QMessageBox.question(self, "Disky Business", text) == QMessageBox.Yes
        self.panel = DrivePanel(drives, self.NOTE, self.FOLDER_HEADING, uses_folder=self.USES_FOLDER)
        self.panel.changed.connect(self._show_working_folder)
        self.panel.changed.connect(self.on_folder)

    # -- helpers for building the right-hand side ------------------------------------------------------------
    def progress_line(self):
        """A new progress bar for this tab; Stop notes on it are cleared when the job ends."""
        line = ProgressLine()
        self.progress_lines.append(line)
        return line

    def framed(self, widget):
        """The sunken frame around a map or a graph."""
        frame = QFrame()
        frame.setObjectName("mapframe")
        box = QVBoxLayout(frame)
        box.setContentsMargins(2, 2, 2, 2)
        box.addWidget(widget)
        return frame

    def legend(self, pairs, per_row=4):
        """Colour squares with labels, `per_row` to a line so a long legend does not force the window wide;
        pairs = (colour key, label). Colours are set in set_theme."""
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        for i, (key, label) in enumerate(pairs):
            sw = QLabel()
            sw.setFixedSize(13, 13)
            self.swatches.append((sw, key))
            grid.addWidget(sw, i // per_row, 3 * (i % per_row))
            grid.addWidget(QLabel(label), i // per_row, 3 * (i % per_row) + 1)
            grid.setColumnMinimumWidth(3 * (i % per_row) + 2, 10)
        grid.setColumnStretch(3 * per_row, 1)
        return grid

    def paint_legend(self, colours):
        for sw, key in self.swatches:
            sw.setStyleSheet(f"background: {colours[key]};")

    def assemble(self, right):
        row = QHBoxLayout(self)
        row.addWidget(self.panel)
        row.addLayout(right, 1)

    # -- shared behaviour ------------------------------------------------------------------------------------
    OWN_TEST_FOLDER = False     # True for the Test tab only: it writes into the app's own folder in the home folder

    def _show_working_folder(self, folder, _hint):
        """The "Works in" box shows the folder this tab really works in (see `folder`)."""
        if folder and self.folder != folder:
            self.panel.folder_box.setText(self.folder)

    @property
    def folder(self):
        """The folder this tab works in. On the system drive the panel names the Test tab's own folder in the
        home folder, which exists only once a test made it: the other tabs work in the home folder itself
        (Defrag scans it, Speed puts its temporary file there and removes it). Found 2026-11-27: Defrag on the
        system drive said "Not a folder"."""
        f = self.panel.folder
        if f and not self.OWN_TEST_FOLDER and os.path.basename(f.rstrip("/")) == TEST_FOLDER:
            return os.path.dirname(f.rstrip("/"))
        return f

    @property
    def drives(self):
        return self.panel.drives

    def _safe_event(self, ev):
        try:
            self.on_event(ev)
        except Exception as e:          # a malformed event must not break the tab
            self.problems_seen.append(f"{ev.get('event')}: could not be shown ({e.__class__.__name__})")

    def _safe_finished(self, code, crash):
        """The tab is always freed at the end, even when showing the result fails."""
        try:
            self.on_finished(code, crash)
        except Exception as e:
            self.problems_seen.append(f"end: could not be shown ({e.__class__.__name__})")
            self.error = "The result could not be shown."
            for name in ("status", "care_status", "test_status", "detail"):
                label = getattr(self, name, None)       # say so where this tab shows its results
                if label is not None:
                    label.setText(self.error)
                    break
        finally:
            if self.mode is not None and not self.continuing:
                self.set_busy(None)
            self.continuing = False

    def password_words(self):
        """What a question says about the password: asked once per session, or not at all any more."""
        s = self.runner.session
        if s is not None and s.is_open():
            return "The admin session is open, so no password is asked."
        return "The system will ask for the admin password, once for this session."

    def set_folder(self, path):
        self.panel.set_folder(path)

    def set_busy(self, mode):
        """Enter a job (a name) or leave it (None). Tells the window, which locks the other tabs."""
        was = self.mode
        self.mode = mode
        if mode is None:
            for line in self.progress_lines:
                line.stopping = ""      # the job is over; the next one starts clean
        self.update_buttons()
        if (was is None) != (mode is None):
            self.busy_changed.emit(mode is not None)

    def shutdown(self):
        try:
            if self.runner.running():
                self.runner.stop()
                if self.runner._proc is not None and not self.runner._proc.waitForFinished(3000):
                    self.runner.kill()
        except Exception:
            pass                        # closing must go on for every tab

    # -- hooks a tab fills in --------------------------------------------------------------------------------
    def set_theme(self, theme):
        self.theme = theme

    def update_buttons(self):
        self.panel.set_busy(self.mode is not None)

    def on_folder(self, folder, hint):
        self.update_buttons()

    def on_event(self, ev):
        pass

    def on_finished(self, code, crash):
        self.set_busy(None)
