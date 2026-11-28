# SPDX-License-Identifier: GPL-3.0-or-later
"""The window: tabs on top, the Test tab, the tip line at the bottom.

Each tab is its own widget added in MainWindow.TABS; a tab appears only when its piece works.
"""
import os

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
                               QMessageBox, QPushButton, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from disky import advice, tips

from . import drives as drives_mod
from .chrome import font as theme_font, stylesheet
from .drivepanel import LOOKING, DrivePanel
from .mapmodel import MapModel
from .mapwidget import BlockMap
from .runner import EngineRunner
from .adminsession import AdminSession
from .maintenancetab import MaintenanceTab
from .defragtab import DefragTab
from .smarttab import SmartTab
from .speedtab import SpeedTab
from .tabbase import TabBase
from .icons import dress
from .theme import DEFAULT_THEME, THEMES, default_theme

LEGEND = (("untouched", "Not touched"), ("written", "Written"), ("working", "Working now"), ("good", "Good"),
          ("changed", "Few bits changed"), ("bad", "Bad"), ("overwritten", "Wrong place"))
PHASES = {"write": "Writing", "verify": "Checking"}
RUNS = ("test", "verify")        # jobs that fill the map and can be stopped


def size_text(n):
    for unit, step in (("GiB", 1 << 30), ("MiB", 1 << 20)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


def time_text(seconds):
    if seconds is None:
        return ""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h left"
    return f"{round(seconds / 60)} min left" if seconds >= 90 else f"{int(seconds)} s left"


def test_plan(folder, statvfs=os.statvfs, files=None, system=None):
    """What pressing Start will do in this folder, in one plain paragraph. Nothing is written to find out."""
    from disky import engine, safety
    try:
        there = (files or engine.test_files)(folder)
        vfs = statvfs(folder)
    except OSError:
        return "Press \"Start test\" to test this drive."
    if there:
        n = len(there)
        return (f"This drive already holds {n} Disky test file{'s' if n != 1 else ''} from an earlier test. "
                "\"Check test files again\" reads them back and gives a verdict. \"Delete test files\" removes "
                "them. A new test can start once they are deleted.")
    free = vfs.f_bavail * vfs.f_frsize
    on_system = (system or safety.is_system_drive)(folder)
    keep = safety.system_reserve(vfs.f_blocks * vfs.f_frsize) if on_system else 0
    amount = max(0, free - keep)
    if amount < (1 << 20):
        return "There is no free space here to test."
    text = (f"\"Start test\" fills the free space here ({drives_mod.size_text(amount)}) with test files, reads all "
            "of it back, and says whether the drive can be trusted. Your own files are not touched. The test "
            "files stay until you press \"Delete test files\".")
    if on_system:
        text += f" Your system runs from this drive, so {drives_mod.size_text(keep)} is left free."
    return text + " It takes from minutes to hours, depending on the drive."


class TestTab(TabBase):
    title = "Test"
    NOTE = "Pick a drive above. The test fills the drive's free space, then reads it all back."

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.model = MapModel()
        self.map_of = ""                                # the folder the map was drawn for
        self.summary = None
        self.verdict_state = None
        self.drive_list, self.health, self.refresh = self.panel.drive_list, self.panel.health, self.panel.refresh
        self.folder_box, self.choose = self.panel.folder_box, self.panel.choose

        self.map = BlockMap(self.model, theme)
        frame = self.framed(self.map)
        legend = self.legend(LEGEND)
        self.paint_legend(theme.states)

        self.status = QLabel("Pick a drive, then press Start.")
        self.status.setWordWrap(True)                   # a long reason must never force the window wide
        self.status.setObjectName("status")
        self.verdict = QFrame()
        self.verdict.setObjectName("verdict")
        self.verdict_mark = QLabel()
        self.verdict_mark.setFixedSize(34, 34)
        self.verdict_title = QLabel()
        self.verdict_title.setWordWrap(True)
        self.verdict_title.setObjectName("verdict_title")
        self.verdict_detail = QLabel()
        self.verdict_detail.setWordWrap(True)
        words = QVBoxLayout()
        words.addWidget(self.verdict_title)
        words.addWidget(self.verdict_detail)
        vrow = QHBoxLayout(self.verdict)
        vrow.addWidget(self.verdict_mark)
        vrow.addLayout(words, 1)
        self.stack = QStackedWidget()
        self.plan = QLabel("")                          # what Start will do, in plain words, before it is pressed
        self.plan.setWordWrap(True)
        self.plan.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.stack.addWidget(self.status)
        self.stack.addWidget(self.verdict)
        self.stack.addWidget(self.plan)
        self.progress = self.progress_line()                  # a running test: bar, speed, time left
        self.stack.addWidget(self.progress)
        self.stack.setFixedHeight(84)

        self.plan_of = test_plan                        # replaced in tests
        self.go = QPushButton("Start test")
        self.go.setObjectName("primary")                # the main action of the tab
        self.go.setToolTip("Fill the drive's free space with test files, then read all of it back")
        self.go.clicked.connect(self.start_or_stop)
        self.remove = QPushButton("Delete test files")
        self.remove.setToolTip("Delete the test files Disky wrote here, after asking. Nothing else is deleted")
        self.remove.clicked.connect(self.remove_files)
        self.check = QPushButton("Check test files again")
        self.check.setToolTip("Read back test files from an earlier test, for example after plugging the drive "
                              "in again, and give a verdict")
        self.check.clicked.connect(self.check_existing)
        buttons = QVBoxLayout()
        buttons.addWidget(self.go)
        buttons.addWidget(self.check)
        buttons.addWidget(self.remove)
        bottom = QHBoxLayout()
        bottom.addWidget(self.stack, 1)
        bottom.addLayout(buttons)

        caption = QLabel("Test map: every part of the drive is written, then read back")
        caption.setObjectName("caption")
        right = QVBoxLayout()
        right.addWidget(caption)
        right.addWidget(frame, 1)
        right.addLayout(legend)
        right.addLayout(bottom)
        self.assemble(right)
        self.update_buttons()

    def set_theme(self, theme):
        self.theme = theme
        self.map.set_look(theme)
        self.paint_legend(theme.states)
        if self.verdict_state:
            self.verdict_mark.setStyleSheet(f"background: {theme.states[self.verdict_state]};")

    # -- actions -------------------------------------------------------------------------------------------
    def on_folder(self, _folder, hint):
        if self.mode is None and hint != LOOKING and _folder != self.map_of:
            self.map_of = _folder                       # the map belongs to the drive it was drawn for
            self.model.reset()
            self.model.version += 1
        if self.mode is None and _folder:
            self.plan.setText(LOOKING)                  # the drive may be slow: it answers off this thread
            self.stack.setCurrentWidget(self.plan)
            plan_of = self.plan_of
            self.plans = getattr(self, "plans", 0) + 1     # only the newest answer counts
            self.panel.later(lambda: plan_of(_folder), self.plan_read, _folder, self.plans)
        elif self.mode is None and hint:
            self.stack.setCurrentWidget(self.status)
            self.status.setText(hint)
        self.update_buttons()

    def plan_read(self, folder, number, result):
        if self.mode is None and self.folder == folder and number == self.plans:
            self.plan.setText(result if isinstance(result, str) else "Press \"Start test\" to test this drive.")

    def update_buttons(self):
        busy = self.mode is not None
        self.go.setText("Stop test" if self.mode in RUNS else "Start test")
        self.go.setEnabled(bool(self.folder) and self.mode != "clean")
        self.remove.setEnabled(bool(self.folder) and not busy)
        self.check.setEnabled(bool(self.folder) and not busy)
        self.panel.set_busy(busy)

    def start_or_stop(self):
        if self.mode in RUNS:
            self.status.setText("Stopping...")
            self.progress.show_stopping()
            self.runner.stop()
        elif self.mode is None and self.folder:
            self._begin("test")

    def check_existing(self):
        if self.mode is None and self.folder:
            self._begin("verify")

    def remove_files(self):
        if self.mode is None and self.folder and self.confirm(
                f"Delete the test files Disky Business wrote in\n{self.folder} ?\nNothing else is touched."):
            self._begin("clean", "--yes")

    def _begin(self, mode, *extra):
        self.summary, self.error = None, None
        self.phase = ""
        self.removed = 0
        self.stack.setCurrentWidget(self.status)
        self.status.setText("Deleting test files..." if mode == "clean" else "Starting...")
        if mode == "test" and os.path.basename(self.folder) == drives_mod.TEST_FOLDER:
            try:
                os.makedirs(self.folder, exist_ok=True)     # the app's own folder, on a drive with none to write to
            except OSError as e:
                self.status.setText(f"Could not make the test folder: {e.strerror}.")
                return
        self.set_busy(mode)
        self.runner.start(mode, self.folder, *extra)

    # -- from the engine -----------------------------------------------------------------------------------
    def on_event(self, ev):
        kind = ev.get("event")
        if self.mode in RUNS:
            self.model.apply(ev)
        if kind == "phase":
            self.phase = PHASES.get(ev.get("phase"), "Working")
            self.status.setText(self.phase + "...")
            if self.mode in RUNS:
                self.progress.show_progress(self.phase + "...", 0 if ev.get("total_bytes") else None)
                self.stack.setCurrentWidget(self.progress)
        elif kind == "progress" and self.mode in RUNS:
            parts = [self.phase or "Working"]
            if ev.get("total_bytes"):
                parts[0] += f" {min(100, 100 * ev['done_bytes'] // ev['total_bytes'])} %"
            parts.append(f"{ev.get('mib_per_s', 0):.0f} MiB/s")
            parts.append(time_text(ev.get("eta_s")))
            self.status.setText("     ".join(p for p in parts if p))
            total = ev.get("total_bytes")
            self.progress.show_progress(
                self.phase or "Working", 100 * ev["done_bytes"] / total if total else None,
                f"{ev.get('mib_per_s', 0):.0f} MiB/s", time_text(ev.get("eta_s")) or "")
            self.stack.setCurrentWidget(self.progress)
        elif kind == "warning":
            self.tip(ev.get("message", ""))
        elif kind == "error":
            self.error = ev.get("message") or "The engine reported an error."
            if ev.get("code") == "existing_files":
                self.error = ("This drive already holds Disky test files. Press \"Check test files again\" to "
                              "read them back, or \"Delete test files\" first.")
        elif kind == "summary":
            self.summary = ev
        elif kind == "done" and self.mode == "clean":
            self.summary = ev

    def on_finished(self, code, crash):
        mode = self.mode
        if crash:
            self._verdict("bad", "The test did not finish.", crash)
        elif self.error:
            self._verdict("bad", "Could not run.", self.error)
        elif mode == "clean":
            self.stack.setCurrentWidget(self.status)
            kept = (self.summary or {}).get("kept_files") or 0
            text = "Test files deleted." if code == 0 else "Nothing was deleted."
            if kept:
                text += (f" {kept} file{'s' if kept > 1 else ''} with a test file's name kept: no test data inside, "
                         "so it may not be ours. Delete by hand if you are sure.")
            self.status.setText(text)
        else:
            self._verdict(*self._judge(code))
        self.set_busy(None)

    def _judge(self, code):
        s = self.summary or {}
        verdict = s.get("verdict") or ("stopped" if code == 130 else None)
        read = size_text(s.get("bytes_read", 0))
        if verdict == "good":
            return "good", "Drive is good.", (f"{read} read back. Every block matched." if self.mode == "verify"
                                               else f"{read} written and read back. Every block matched.")
        if verdict == "bad":
            c = self.model.counts()
            n = c["bad"] + c["changed"] + c["overwritten"]
            first = s.get("first_bad_offset")
            title = "Drive is bad"
            if n:
                title += f": {n} block{'s' if n != 1 else ''}"
                if first is not None:
                    title += f", first at {size_text(first)}"
            detail = f"{read} read back."
            if s.get("missing_bytes"):
                detail += f" {size_text(s['missing_bytes'])} of test data is missing."
            return "bad", title + ".", detail + " Do not trust this drive with anything you like. " + advice.BAD_DRIVE
        if verdict == "not_verified":
            return "changed", "Not verified.", ("The data matched, but the computer answered from its own memory "
                                               "and not from the drive, so this proves nothing.")
        if verdict == "nothing_to_check":
            return "untouched", "Nothing to check.", "There are no test files in this folder."
        if verdict == "stopped":
            return "untouched", "Stopped.", "The test files are left in place."
        return "bad", "The test did not finish.", f"The engine ended without a result (exit {code})."

    def _verdict(self, state, title, detail):
        self.verdict_state = state
        self.verdict_mark.setStyleSheet(f"background: {self.theme.states[state]};")
        self.verdict_title.setText(title)
        self.verdict_detail.setText(detail)
        self.stack.setCurrentWidget(self.verdict)


def system_is_dark():
    """True when the desktop is set to a dark look (Qt 6.5 and later can tell)."""
    try:
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except (AttributeError, RuntimeError):
        return False


TIP_SECONDS = 30            # how long one tip stays; long enough to read it and get the joke


CLOSE_DRIVE = ("A drive is being mounted, unmounted or looked at right now. Closing the window now can leave that "
               "half done. Close anyway?")
CLOSE_REPAIR = ("A repair is running. Stopping it half way can leave the file system worse than before. Wait "
                "until it has finished. Close anyway?")


class MainWindow(QMainWindow):
    TABS = (TestTab, SpeedTab, SmartTab, DefragTab, MaintenanceTab)

    def __init__(self, theme=None, runner=None, drives=None, runners=None, admin=None):
        runners = dict(runners or {})
        if runner is not None:
            runners.setdefault("Test", runner)
        super().__init__()
        self.remember = theme is None           # a look passed in (tests) is neither loaded nor saved
        # A first start gets the modern look, light or dark as the system is; after that, the look last chosen.
        saved = QSettings("disky-business", "disky-business").value("look", default_theme(system_is_dark())) \
            if self.remember else None
        self.theme = theme or THEMES.get(saved, THEMES[DEFAULT_THEME])
        self.setWindowTitle("Disky Business")
        self._apply_font()
        self.setStyleSheet(stylesheet(self.theme))
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themebutton")
        self.theme_button.clicked.connect(self.next_theme)
        self.tip_line = QLabel()
        self.tip_line.setWordWrap(True)
        self.tip_line.setObjectName("tipline")
        self.tip_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.tabs = QTabWidget()
        self.pages = []
        # One admin session for the whole window: the password is asked once, not once per job.
        self.admin = admin or AdminSession(parent=self)
        self.admin.changed.connect(self.on_admin)
        self.admin_button = QPushButton("Admin session open: End")
        self.admin_button.setToolTip("The password was given once for this session. End it now; the next admin "
                                     "job asks again. It also ends when you close the app")
        self.admin_button.clicked.connect(self.end_admin)
        self.admin_button.hide()
        for cls in self.TABS:
            page = cls(self.theme, runner=runners.get(cls.title), tip=self.show_tip, drives=drives)
            page.runner.session = self.admin
            page.busy_changed.connect(self.on_busy)
            self.pages.append(page)
            self.tabs.addTab(page, cls.title)
        self.tabs.setCornerWidget(self.theme_button, Qt.TopRightCorner)
        self._tab = 0
        self.tabs.currentChanged.connect(self.on_tab)
        self._label_theme_button()
        body = QWidget()
        col = QVBoxLayout(body)
        col.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.tip_line, 1)
        bottom.addWidget(self.admin_button)
        col.addLayout(bottom)
        self.setCentralWidget(body)
        self.resize(1180, 760)
        if self.remember:                       # open at the size the user left it
            size = QSettings("disky-business", "disky-business").value("size")
            if size is not None and hasattr(size, "isValid") and size.isValid():
                self.resize(size)
        dress(self, self.theme)
        self._tip_no = int.from_bytes(os.urandom(2), "big") % len(tips.TIPS)
        self._held = False
        self.confirm_close = self.ask_close             # replaced in tests
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.next_tip)
        self._timer.start(TIP_SECONDS * 1000)
        self.next_tip()

    def _apply_font(self):
        f = theme_font(self.theme)
        app = QApplication.instance()
        if app is not None:
            app.setFont(f)          # reaches every widget, also ones a style rule gives another size
        self.setFont(f)

    def on_tab(self, index):
        """The drive picked on one tab is the drive on the next. While a tab works, no other can be opened,
        also not with the keyboard (Ctrl+Tab), so two jobs never run at once."""
        busy = next((i for i, p in enumerate(self.pages) if p.working), None)
        if busy is not None and index != busy:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(busy)
            self.tabs.blockSignals(False)
            return
        self.pages[index].panel.adopt(self.pages[self._tab].panel)
        if self._held and index != self._tab:           # a message from the last tab means nothing on this one
            self._held = False
            self.next_tip()
        self._tab = index

    def on_admin(self, is_open):
        self.admin_button.setVisible(is_open)

    def end_admin(self):
        if not any(p.working for p in self.pages):              # never under a running job
            self.admin.end()

    def on_busy(self, busy):
        """One job at a time: while a tab works, the others cannot be opened."""
        self.tabs.tabBar().setEnabled(not any(p.working for p in self.pages))
        dress(self, self.theme)                         # Start became Stop, or the other way round

    def _label_theme_button(self):
        self.theme_button.setText("Look: " + self.theme.label)
        self.theme_button.setToolTip("Change the colours")

    def set_theme(self, theme):
        self.theme = theme
        self._apply_font()
        self.setStyleSheet(stylesheet(theme))
        for page in self.pages:
            page.set_theme(theme)
        self._label_theme_button()
        dress(self, theme)
        if self.remember:
            QSettings("disky-business", "disky-business").setValue("look", theme.name)

    def next_theme(self):
        names = list(THEMES)
        self.set_theme(THEMES[names[(names.index(self.theme.name) + 1) % len(names)]])

    def next_tip(self):
        if self._held:              # a warning stays for one turn before the tips carry on
            self._held = False
            return
        self._tip_no = (self._tip_no + 1) % len(tips.TIPS)
        self.tip_line.setText(tips.TIPS[self._tip_no])

    def show_tip(self, text):
        if text:
            self._held = True
            self.tip_line.setText(text)

    def ask_close(self, text):
        return QMessageBox.question(self, "Close Disky Business?", text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) == QMessageBox.Yes

    def closeEvent(self, event):
        busy = next((p for p in self.pages if p.working), None)
        if busy is not None:
            if busy.mode == "repair":
                text = CLOSE_REPAIR
            elif busy.mode is not None:
                text = f"The {busy.title} tab is still working ({busy.mode}). Closing the window stops it. Close anyway?"
            else:
                text = CLOSE_DRIVE
            if not self.confirm_close(text):
                event.ignore()
                return
        if self.remember and not self.isMaximized():
            QSettings("disky-business", "disky-business").setValue("size", self.size())
        for page in self.pages:
            page.shutdown()
        self.admin.end()                                # the helper's input closes: it ends with the window
        super().closeEvent(event)
