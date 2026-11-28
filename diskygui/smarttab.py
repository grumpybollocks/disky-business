# SPDX-License-Identifier: GPL-3.0-or-later
"""The SMART tab: what the drive says about its own health, read through the system's drive service.

Reading needs no password and changes nothing on the drive.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import smart
from .deepview import DeepView
from .defragtab import admin_wrap
from .tabbase import TabBase

ABOUT = ("SMART is the drive's own record of its health: errors, wear, hours and temperature. Reading it changes "
         "nothing on the drive. Score is the drive's own mark for an item (higher is better); the item fails "
         "when the score drops to \"Fails at\".")
COLUMNS = ("Item", "Value", "Status", "Score", "Worst", "Fails at", "What it means")
NUMBERS = (3, 4, 5)                     # the drive's own score columns; hidden for drives that give none


def verdict(rows):
    """One line that says what to do."""
    if not rows:
        return "No SMART details for this drive."
    if any(r.status == "FAILING" for r in rows):
        return "The drive says it is failing. Copy your files off it now."
    bad = sum(r.bad for r in rows)
    if bad:
        return f"{bad} thing{'s' if bad > 1 else ''} to watch. Keep a backup of this drive."
    return "No warnings from the drive itself."


class SmartTab(TabBase):
    title = "SMART"
    NOTE = ""                           # the right-hand side already says "Choose a drive."
    USES_FOLDER = False

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.read_smart = smart.read                    # replaced in tests
        self.wrap = admin_wrap                          # the password prompt; replaced in tests
        self.by_password = {}                           # drive -> rows read with smartctl in this session
        self.reading = None                             # the drive whose SMART record is being read
        self.asked_drive = self.usb_data = self.deep_data = None
        self.deep_view = None
        self.rows = []
        about = QLabel(ABOUT)
        about.setWordWrap(True)
        box = QFrame()
        box.setObjectName("verdict")
        col = QVBoxLayout(box)
        self.answer = QLabel("Choose a drive.")
        self.answer.setObjectName("verdict_title")
        self.answer.setWordWrap(True)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        col.addWidget(self.answer)
        col.addWidget(self.detail)
        self.glance = QHBoxLayout()                     # the big figures: life, temperature, hours
        self.glance_boxes = []
        for _ in range(4):
            tile = QFrame()
            tile.setObjectName("verdict")
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(8, 4, 8, 4)
            value, name = QLabel(""), QLabel("")
            value.setObjectName("verdict_title")
            inner.addWidget(value)
            inner.addWidget(name)
            self.glance.addWidget(tile, 1)
            self.glance_boxes.append((tile, value, name))
            tile.hide()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().height() + 6)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        head = self.table.horizontalHeader()
        for i in range(len(COLUMNS) - 1):
            head.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.Stretch)
        self.table.setWordWrap(False)
        self.show_all = QCheckBox("Show all items")
        self.show_all.toggled.connect(self.fill)
        self.again = QPushButton("Read again")
        self.again.clicked.connect(self.read_again)
        self.short_button = QPushButton("Short self-test")
        self.short_button.setToolTip("The drive tests itself for a few minutes. Only reads")
        self.short_button.clicked.connect(self.short_test)
        self.long_button = QPushButton("Long self-test")
        self.long_button.setToolTip("The drive reads its whole surface. Can take hours. Only reads")
        self.long_button.clicked.connect(self.long_test)
        self.stop_button = QPushButton("Stop self-test")
        self.stop_button.clicked.connect(self.stop_test)
        self.test_status = QLabel("")
        self.test_status.setObjectName("status")
        self.test_status.setWordWrap(True)
        self.test_progress = self.progress_line()             # shown while a self-test runs
        self.test_progress.hide()
        self.test_result = None
        self.usb_button = QPushButton("Read with admin password")
        self.usb_button.setToolTip("For drives in USB cases: reads the SMART record with smartctl. Only reads")
        self.usb_button.clicked.connect(self.read_with_password)
        self.deep_button = QPushButton("Deep scan")
        self.deep_button.setToolTip("Everything the drive knows about itself: maker, firmware, past errors, "
                                    "self-test history, lifetime statistics. Only reads")
        self.deep_button.clicked.connect(self.deep_scan)
        bottom = QHBoxLayout()
        bottom.addWidget(self.show_all)
        words = QVBoxLayout()
        words.addWidget(self.test_status)
        words.addWidget(self.test_progress)
        bottom.addLayout(words, 1)
        for b in (self.short_button, self.long_button, self.stop_button):
            bottom.addWidget(b)
        bottom.addWidget(self.usb_button)
        bottom.addWidget(self.deep_button)
        bottom.addWidget(self.again)
        right = QVBoxLayout()
        right.addWidget(about)
        right.addWidget(box)
        right.addLayout(self.glance)
        right.addWidget(self.table, 1)
        right.addLayout(bottom)
        self.assemble(right)
        self.update_buttons()

    def on_folder(self, _folder, _hint):
        d, asked = self.panel.current_drive(), self.asked_drive
        if self.mode is None and (d is None or asked is None or d.path != asked.path):
            self.test_status.setText("")                # a self-test result belongs to the drive it ran on
        self.load()

    def read_again(self):
        d = self.panel.current_drive()
        if d is not None and not d.smart and d.path in self.by_password:
            self.read_with_password()                   # fresh values need smartctl, and the password, again
        else:
            self.load()

    def load(self):
        d = self.panel.current_drive()
        if d is not None and d.smart:
            if self.reading == d.path:
                return                                  # already being read
            self.reading = d.path
            self.rows = []
            self.answer.setText("Reading the drive...")
            self.detail.setText("A drive that is asleep or struggling can take a while to answer.")
            self.fill()
            self.update_buttons()
            read = self.read_smart                      # off the window's thread: a slow drive must not hold it up
            self.panel.later(lambda: read(d), self.smart_read, d)
            return
        self.reading = None
        self.show(d, *self.offline(d))

    def smart_read(self, d, result):
        if self.reading != d.path:
            return
        self.reading = None
        now = self.panel.current_drive()
        if now is None or now.path != d.path:
            return                                      # another drive was picked meanwhile
        self.show(d, *(result if isinstance(result, tuple) else ([], f"The drive did not answer: {result}")))

    def offline(self, d):
        """(rows, message) for a drive udisks gives no SMART for."""
        if d is None:
            return [], ""
        if d.path in self.by_password:
            rows = self.by_password[d.path]
            # The health box said "no health data"; now there is some.
            self.panel.health.setText("\n".join(["Read with the admin password:"]
                                                + (smart.brief(rows) or ["No warnings."])))
            return rows, ""
        if d.flash:
            return [], smart.NO_SMART_STICK + (
                " If this is really a drive in a USB case, \"Read with admin password\" tries smartctl."
                if d.device else "")
        return [], smart.NO_SMART + (
            " \"Read with admin password\" reads it with smartctl instead." if d.device else "")

    def show(self, d, rows, msg):
        self.rows = rows
        if d is None:
            self.answer.setText("Choose a drive.")
        else:
            self.answer.setText(verdict(self.rows))
            if not msg:
                main = sum(r.main for r in self.rows)
                msg = f"{d.model}: the {main} items that matter are shown, problems first."
                if main < len(self.rows):
                    msg += f" Tick \"Show all items\" for the other {len(self.rows) - main}."
        self.detail.setText(msg)
        self.fill()
        self.update_buttons()

    def fill(self):
        rows = sorted((r for r in self.rows if r.main or self.show_all.isChecked()), key=lambda r: not r.bad)
        self.table.setRowCount(len(rows))
        red = QColor(self.theme.states["bad"])
        figures = smart.glance(self.rows)
        for i, (tile, value, name) in enumerate(self.glance_boxes):
            tile.setVisible(i < len(figures))
            if i < len(figures):
                name.setText(figures[i][0])
                value.setText(figures[i][1])
        scored = any(r.score is not None or r.limit is not None for r in rows)
        for col in NUMBERS:
            self.table.setColumnHidden(col, not scored)
        for i, r in enumerate(rows):
            numbers = ["" if x is None else str(x) for x in (r.score, r.worst, r.limit)]
            for j, text in enumerate((r.name, r.value, r.status, *numbers, r.about)):
                item = QTableWidgetItem(text)
                if j in (1, *NUMBERS):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setToolTip(r.about + ("\n" if r.about else "") + (f"The drive's {r.detail}." if r.detail else ""))
                if r.bad:
                    item.setForeground(red)
                self.table.setItem(i, j, item)

    def read_with_password(self):
        d = self.panel.current_drive()
        if d is None or d.smart or not d.device or self.mode is not None:
            return
        if self.confirm(f"Read the SMART record of {d.model} with smartctl?\nIt only reads. Talking to a drive "
                        "through a USB case needs admin rights. " + self.password_words()):
            self.error = self.usb_data = None
            self.asked_drive = d
            self.detail.setText("Reading...")
            self.set_busy("smart")
            self.runner.start("smart", d.device, wrap=self.wrap())

    def deep_scan(self):
        d = self.panel.current_drive()
        if d is None or not d.device or self.mode is not None:
            return
        if self.confirm(f"Deep scan of {d.model}?\nIt reads everything the drive knows about itself: identity, "
                        "firmware, past errors, self-test history and lifetime statistics. It only reads and "
                        "takes a few seconds. " + self.password_words()):
            self.error = self.deep_data = None
            self.asked_drive = d
            self.detail.setText("Deep scan...")
            self.set_busy("deep")
            self.runner.start("smart", d.device, "--deep", "--serial", wrap=self.wrap())

    def open_deep(self, drive, data):
        """Show the result in its own window (replaced in tests)."""
        self.deep_view = DeepView(drive.model, data, drive.device, self)
        self.deep_view.show()

    def short_test(self):
        self.start_test("short", "It takes a few minutes.")

    def long_test(self):
        self.start_test("long", "The drive reads its whole surface: this can take hours.")

    def start_test(self, kind, how_long):
        d = self.panel.current_drive()
        if d is None or not d.smart or not d.device or self.mode is not None:
            return
        if self.confirm(f"Run a {kind} self-test on {d.model}?\n\nThe drive tests itself. {how_long} It only "
                        "reads, and the drive stays usable, a little slower.\nDisky stays busy until it ends; "
                        "\"Stop self-test\" ends it early.\n\n" + self.password_words()):
            self.error = self.test_result = None
            self.asked_drive = d                        # its result belongs to this drive
            self.test_status.setText(f"Starting the {kind} self-test...")
            self.test_progress.show_progress(f"Starting the {kind} self-test", None, "", "")
            self.set_busy("selftest")
            self.runner.start("selftest", d.device, "--kind", kind, wrap=self.wrap())

    def stop_test(self):
        if self.mode == "selftest":
            self.test_status.setText("Stopping the self-test...")
            self.test_progress.show_stopping("Stopping the self-test...")
            self.runner.stop()

    def on_event(self, ev):
        if ev.get("event") == "selftest_progress":
            self.test_status.setText(f"Self-test running: {ev.get('percent', 0)} %")
            self.test_progress.show_progress("Self-test running", ev.get("percent", 0),
                                             "the drive is testing itself", "")
        elif ev.get("event") == "selftest_result":
            self.test_result = ev
        elif ev.get("event") == "smart_deep":
            self.deep_data = ev.get("data") or {}
        elif ev.get("event") == "smart_data":
            self.usb_data = ev.get("data") or {}
        elif ev.get("event") == "error":
            self.error = ev.get("message") or "The engine reported an error."

    def on_finished(self, code, crash):
        if self.mode == "selftest":
            if crash:
                words = "The self-test was not followed to its end. " + crash
            elif self.error:
                words = self.error
            elif self.test_result:
                words = "Self-test: " + str(self.test_result.get("message", ""))
            else:
                words = f"Ended without a result (exit {code})."
            self.set_busy(None)
            self.load()                                 # fresh values after the test
            self.test_status.setText(words)
            return
        d, problem = self.asked_drive, ""
        if self.mode == "deep":
            if crash:
                problem = "It did not finish. " + crash
            elif self.error:
                problem = self.error
            elif code in (126, 127):
                problem = "Not read: the admin password was not given."
            elif self.deep_data is None or d is None:
                problem = f"Ended without a result (exit {code})."
            self.set_busy(None)
            self.load()
            if problem:
                self.detail.setText("Deep scan: " + problem)
            else:
                self.open_deep(d, self.deep_data)
            return
        if crash:
            problem = "It did not finish. " + crash
        elif self.error:
            problem = self.error
        elif code in (126, 127):
            problem = "Not read: the admin password was not given."
        elif self.usb_data is not None and d is not None:
            self.by_password[d.path] = smart.smartctl_rows(self.usb_data)
        else:
            problem = f"Ended without a result (exit {code})."
        self.set_busy(None)
        self.load()
        if problem:
            self.detail.setText(problem)

    def update_buttons(self):
        d = self.panel.current_drive()
        self.usb_button.setVisible(bool(d and not d.smart and d.device))
        testable = bool(d and d.smart and d.device)
        self.short_button.setVisible(testable and self.mode != "selftest")
        self.long_button.setVisible(testable and self.mode != "selftest")
        self.stop_button.setVisible(self.mode == "selftest")
        self.test_progress.setVisible(self.mode == "selftest")
        self.test_status.setVisible(self.mode != "selftest")
        self.short_button.setEnabled(self.mode is None)
        self.long_button.setEnabled(self.mode is None)
        self.usb_button.setEnabled(self.mode is None)
        self.deep_button.setVisible(bool(d and d.device))
        self.deep_button.setEnabled(self.mode is None)
        self.again.setEnabled(self.mode is None and self.panel.current_drive() is not None)
        self.panel.set_busy(self.mode is not None)

    def set_theme(self, theme):
        self.theme = theme
        self.fill()
