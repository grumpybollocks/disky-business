# SPDX-License-Identifier: GPL-3.0-or-later
"""The left side of every tab: the drive list, the health box, the folder to work in.

A tab owns one panel and listens to `changed(folder, hint)`. No drive is ever picked for the user.
"""
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from . import driveactions
from . import drives as drives_mod
from . import smart


class _Job:
    """One piece of slow work (asking the system about a drive) done off the window's thread."""

    def __init__(self, work, then):
        self.then, self.result, self.done = then, None, False

        def run():
            try:
                self.result = work()
            except Exception as e:                      # reported to `then`, never lost in the thread
                self.result = e
            self.done = True
        threading.Thread(target=run, daemon=True).start()


class DrivePanel(QWidget):
    changed = Signal(str, str)      # folder ("" when there is none), a line to show the user ("" when none)

    def __init__(self, read_drives=None, note="", folder_heading="Works in", parent=None, uses_folder=True):
        super().__init__(parent)
        self.read_drives = read_drives or drives_mod.read
        self.drives = []
        self.folder = ""
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        dhead = QLabel("Drives")
        dhead.setObjectName("heading")
        self.drive_list = QListWidget()
        self.drive_list.setFixedHeight(200)
        self.drive_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drive_list.setTextElideMode(Qt.ElideRight)
        # Selection, not the "current" row: Qt makes row 0 current when the list merely gets keyboard focus,
        # and a drive must never be picked for the user.
        self.drive_list.itemSelectionChanged.connect(self.on_drive)
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.load_drives)
        self.actions = driveactions                     # replaced in tests
        self.read_smart = smart.read                    # replaced in tests
        self.busy = False
        self.acting = False
        self.jobs = []                                  # slow work under way; the window stays usable meanwhile
        self.collector = QTimer(self)
        self.collector.setInterval(40)
        self.collector.timeout.connect(self.collect)
        self.mount_button = QPushButton("Mount")
        self.mount_button.setToolTip("Make the drive's files reachable")
        self.unmount_button = QPushButton("Unmount")
        self.unmount_button.setToolTip("Close the drive's file system; needed before a repair")
        self.remove_button = QPushButton("Safely remove")
        self.remove_button.setToolTip("Unmount the drive and switch it off, ready to unplug")
        # Plain methods, not lambdas: a lambda that holds the panel makes a loop Python frees too late (a crash
        # on exit when a panel is thrown away).
        self.mount_button.clicked.connect(self.do_mount)
        self.unmount_button.clicked.connect(self.do_unmount)
        self.remove_button.clicked.connect(self.do_remove)
        self.action_note = QLabel()
        self.action_note.setWordWrap(True)
        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        buttons.addWidget(self.refresh, 0, 0)
        buttons.addWidget(self.remove_button, 0, 1)
        buttons.addWidget(self.mount_button, 1, 0)
        buttons.addWidget(self.unmount_button, 1, 1)
        self.health = QLabel()
        self.health.setObjectName("health")
        self.health.setWordWrap(True)
        self.health.setMinimumHeight(84)
        self.health.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        head = QLabel(folder_heading)
        head.setObjectName("heading")
        self.folder_box = QLineEdit()
        self.folder_box.setReadOnly(True)
        self.folder_box.setFocusPolicy(Qt.NoFocus)      # it only shows where the work is done
        self.folder_box.setPlaceholderText("nothing yet")
        self.folder_box.setToolTip("Where on the drive the work is done. Picking a drive fills this in.")
        self.choose = QPushButton("Other folder...")
        self.choose.setToolTip("For special cases: work in a folder you choose yourself")
        self.choose.clicked.connect(self.choose_folder)
        self.note = QLabel(note)
        self.note.setWordWrap(True)
        col.addWidget(dhead)
        col.addWidget(self.drive_list)
        col.addLayout(buttons)
        col.addWidget(self.action_note)
        col.addWidget(self.health)
        col.addSpacing(8)
        for w in (head, self.folder_box, self.choose, self.note):
            col.addWidget(w)
        for w in (head, self.folder_box, self.choose):
            w.setVisible(uses_folder)       # a tab that works on the drive itself (SMART) shows no folder
        col.addStretch(1)
        self.setFixedWidth(250)
        self.load_drives()

    def load_drives(self):
        picked = self.row() >= 0
        self.drive_list.blockSignals(True)
        self.drives = self.read_drives()
        self.drive_list.clear()
        self.drive_list.addItems([d.label for d in self.drives])
        self.drive_list.blockSignals(False)
        self.health.setText("" if self.drives else "Drive list not available. Choose a folder below.")
        if picked:                      # the list was read again: the drive picked before is no longer picked,
            self.set_folder("", "")     # and every tab is told, also when it had no folder (an unmounted drive)
        self.update_actions()

    def update_actions(self):
        """Mount, Unmount and Safely remove are offered only where they can do something."""
        d = None if self.busy or self.acting else self.current_drive()
        self.mount_button.setEnabled(bool(d and self.actions.can_mount(d)))
        self.unmount_button.setEnabled(bool(d and self.actions.can_unmount(d)))
        self.remove_button.setEnabled(bool(d and self.actions.can_remove(d)))

    def fresh(self, drive):
        """The same drive read again from the system, or None when it is gone. Device names like sdb are
        handed out again to other drives, so nothing is done on the strength of an old list."""
        return next((x for x in self.read_drives() if x.path and x.path == drive.path), None)

    def do_mount(self):
        self.act("mount")

    def do_unmount(self):
        self.act("unmount")

    def do_remove(self):
        self.act("remove")

    def act(self, job):
        """Run one drive action, say what happened, and show the drive's new state."""
        d = self.current_drive()
        if d is None or self.busy or self.acting:
            return
        self.acting = True                              # a second click while this one works does nothing
        self.action_note.setText({"mount": "Mounting...", "unmount": "Unmounting...",
                                  "remove": "Switching off..."}[job])
        self.update_actions()
        action = getattr(self.actions, job)

        def work():
            now = self.fresh(d)                         # the drive as it is now, not as it was listed
            return (False, "That drive is no longer there.") if now is None else action(now)
        self.later(work, self.acted, d)

    def acted(self, d, result):
        self.acting = False
        message = result[1] if isinstance(result, tuple) else f"It did not work: {result}"
        self.load_drives()
        same = next((i for i, x in enumerate(self.drives) if x.path and x.path == d.path), -1)
        if same >= 0:
            self.drive_list.setCurrentRow(same)         # picked by the user before; shows its new state
        self.update_actions()
        self.action_note.setText(message)

    # -- slow work off the window's thread ---------------------------------------------------------------------
    def later(self, work, then, *first):
        """Run `work()` on another thread; call `then(*first, result)` back here when it is done."""
        self.jobs.append((_Job(work, then), first))
        self.collector.start()

    def collect(self):
        ready = [j for j in self.jobs if j[0].done]
        self.jobs = [j for j in self.jobs if not j[0].done]
        if not self.jobs:
            self.collector.stop()
        for job, first in ready:
            job.then(*first, job.result)

    def settle(self, seconds=15.0):
        """Wait until the slow work under way has finished and been shown (used by tests and scripts)."""
        end = time.monotonic() + seconds
        while self.jobs and time.monotonic() < end:
            self.collect()
            QApplication.processEvents()
            time.sleep(0.002)
        return not self.jobs

    def row(self):
        rows = self.drive_list.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def current_drive(self):
        row = self.row()
        return self.drives[row] if 0 <= row < len(self.drives) else None

    def on_drive(self):
        d = self.current_drive()
        self.action_note.setText("")
        self.update_actions()
        if d is None:
            return
        folder, why = d.target()
        self.health.setText("\n".join(self.health_lines(d)))
        if d.smart:                                     # wear and warnings follow when the drive has answered
            self.later(lambda: self.read_smart(d), self.health_read, d)
        self.set_folder(folder or "", why or "Press Start.")

    def health_lines(self, d):
        """Health, temperature and hours, then wear and warnings from the drive's SMART record."""
        if not d.smart and d.kind == "stick" and d.health.state == "unknown":
            return ["No health data: sticks and memory cards keep none."]
        if not d.smart and d.usb and d.health.state == "unknown":
            return ["No health data: this USB case does not pass it on."]
        return d.health.lines()

    def health_read(self, d, result):
        now = self.current_drive()
        if now is None or now.path != d.path or not isinstance(result, tuple):
            return                                      # another drive was picked meanwhile, or no answer
        self.health.setText("\n".join(d.health.lines() + smart.brief(result[0])))

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Folder on the drive", self.folder or "/")
        if path:
            self.set_folder(path)

    def set_folder(self, path, hint=""):
        self.folder = path
        self.folder_box.setText(path)
        self.changed.emit(path, hint)

    def set_busy(self, busy):
        self.busy = busy
        for w in (self.choose, self.drive_list, self.refresh):
            w.setEnabled(not busy)
        self.update_actions()

    def adopt(self, other):
        """Take over what another tab's panel shows, so the user picks a drive once."""
        if other is self or (other.folder == self.folder and other.row() == self.row()):
            return
        self.drives = other.drives
        self.drive_list.blockSignals(True)
        self.drive_list.clear()
        self.drive_list.addItems([d.label for d in self.drives])
        if other.row() >= 0:
            self.drive_list.setCurrentRow(other.row())
        self.drive_list.blockSignals(False)
        self.health.setText(other.health.text())
        self.action_note.setText("")
        self.update_actions()
        self.set_folder(other.folder, "Press Start." if other.folder else "")
