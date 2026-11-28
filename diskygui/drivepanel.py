# SPDX-License-Identifier: GPL-3.0-or-later
"""The left side of every tab: the drive list, the health box, the folder to work in.

A tab owns one panel and listens to `changed(folder, hint)`. No drive is ever picked for the user.
"""
import gc
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from . import driveactions
from . import drives as drives_mod
from . import smart


class _Box:
    """What the worker thread fills in. It holds no widget: a widget freed off the window's thread crashes."""

    def __init__(self):
        self.result, self.done = None, False


def _run(work, box):
    try:
        box.result = work()
    except Exception as e:                              # reported to `then`, never lost in the thread
        box.result = e
    box.done = True


# Python's garbage collector must never run on a worker thread. When it did (2026-11-27), PySide handed the
# freeing of collected widgets to the window's thread and Qt freed a label twice: Segmentation fault, now and
# then. So collection is paused, on the window's thread, before a job starts, and resumed there once no job is
# left running. Jobs last seconds (a question to the drive service, or a password prompt), so nothing piles up.
_running = []                                           # boxes of the jobs still running
_reaper = None
_was_on = True                                          # collection was on before the first job paused it


def _pause_collection(box):
    global _reaper, _was_on
    if not _running:
        _was_on = gc.isenabled()
        gc.disable()
    _running.append(box)
    if _reaper is None:
        app = QApplication.instance()
        _reaper = QTimer(app)                           # lives with the application, not with one panel
        _reaper.setInterval(50)
        _reaper.timeout.connect(_resume_collection)
    _reaper.start()


def _resume_collection():
    _running[:] = [b for b in _running if not b.done]
    if not _running:
        if _reaper is not None:
            _reaper.stop()
        if _was_on:
            gc.enable()


class _QuietLabel(QLabel):
    """A label that takes room only while it has something to say, so the drive list gets the rest."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setVisible(bool(self.text()))

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class _Job:
    """One piece of slow work (asking the system about a drive) done off the window's thread."""

    def __init__(self, work, then):
        self.then, self.box = then, _Box()
        _pause_collection(self.box)
        threading.Thread(target=_run, args=(work, self.box), daemon=True).start()

    @property
    def done(self):
        return self.box.done

    @property
    def result(self):
        return self.box.result


def fresh(read_drives, drive):
    """The same drive read again from the system, or None when it is gone. Device names like sdb are
    handed out again to other drives, so nothing is done on the strength of an old list."""
    return next((x for x in read_drives() if x.path and x.path == drive.path), None)


LOOKING = "Looking at the drive..."
READING = "Reading the drive list..."


class DrivePanel(QWidget):
    changed = Signal(str, str)      # folder ("" when there is none), a line to show the user ("" when none)
    working_changed = Signal()      # a drive action began or ended: tabs lock or free what depends on the drive

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
        self.drive_list.setMinimumHeight(140)           # it takes the room the panel has: ten drives, ten rows
        self.drive_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drive_list.setTextElideMode(Qt.ElideRight)
        # Selection, not the "current" row: Qt makes row 0 current when the list merely gets keyboard focus,
        # and a drive must never be picked for the user.
        self.drive_list.itemSelectionChanged.connect(self.on_drive)
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.refresh_clicked)
        self.actions = driveactions                     # replaced in tests
        self.read_smart = smart.read                    # replaced in tests
        self.busy = False
        self.acting = False
        self.jobs = []                                  # slow work under way; the window stays usable meanwhile
        self.looking = None                             # the drive whose folder is being looked for
        self.looks = 0
        self.lists = 0                                  # only the newest drive list counts
        self.loading = False                            # a drive list is being read now
        self.after_list = []                            # what waits for the next list; never dropped
        self.failed = []                                # callbacks that raised (shown by tests, not by the window)
        self.uses_folder = uses_folder
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
        self.action_note = _QuietLabel()
        self.action_note.setWordWrap(True)
        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        buttons.addWidget(self.refresh, 0, 0)
        buttons.addWidget(self.remove_button, 0, 1)
        buttons.addWidget(self.mount_button, 1, 0)
        buttons.addWidget(self.unmount_button, 1, 1)
        self.health = _QuietLabel()
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
        self.note_text = note
        self.note = QLabel(note)
        self.note.setWordWrap(True)
        col.addWidget(dhead)
        col.addWidget(self.drive_list, 1)
        col.addLayout(buttons)
        col.addWidget(self.action_note)
        col.addWidget(self.health)
        col.addSpacing(8)
        for w in (head, self.folder_box, self.choose, self.note):
            col.addWidget(w)
        for w in (head, self.folder_box, self.choose):
            w.setVisible(uses_folder)       # a tab that works on the drive itself (SMART) shows no folder
        col.addStretch(0)                               # spare room goes to the drive list, not to a gap below
        self.setFixedWidth(250)
        self.load_drives()

    def refresh_clicked(self):
        self.load_drives()                              # not connected directly: clicked() passes a bool

    def load_drives(self, then=None):
        """Read the drive list off the window's thread (the drive service can stall behind a failing drive),
        then call `then()` if given."""
        picked = self.row() >= 0
        self.drive_list.blockSignals(True)
        self.drives = []
        self.drive_list.clear()
        self.drive_list.blockSignals(False)
        self.health.setText(READING)
        if picked:                      # the list is read again: the drive picked before is no longer picked,
            self.set_folder("", "")     # and every tab is told, also when it had no folder (an unmounted drive)
        self.update_actions()
        if then is not None:
            self.after_list.append(then)                # kept across a newer read: it runs after the newest list
        self.lists += 1
        self.loading = True
        self.later(self.read_drives, self.drives_read, self.lists)

    def drives_read(self, number, result):
        if number != self.lists:
            return                                      # Refresh was pressed again meanwhile
        self.loading = False
        self.drives = result if isinstance(result, list) else []
        self.drive_list.blockSignals(True)
        self.drive_list.clear()
        self.drive_list.addItems([d.label for d in self.drives])
        self.drive_list.blockSignals(False)
        self.health.setText("" if self.drives else "Drive list not available." + (
            " Choose a folder below." if self.uses_folder else ""))
        self.update_actions()
        waiting, self.after_list = self.after_list, []
        for then in waiting:
            then()

    def update_actions(self):
        """Mount, Unmount and Safely remove are offered only where they can do something."""
        d = None if self.busy or self.acting else self.current_drive()
        self.mount_button.setEnabled(bool(d and self.actions.can_mount(d)))
        self.unmount_button.setEnabled(bool(d and self.actions.can_unmount(d)))
        self.remove_button.setEnabled(bool(d and self.actions.can_remove(d)))

    def fresh(self, drive):
        return fresh(self.read_drives, drive)

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
        self.lock()                                     # and nothing else starts on a drive being changed
        self.working_changed.emit()
        self.set_folder("", "")
        self.action_note.setText({"mount": "Mounting...", "unmount": "Unmounting...",
                                  "remove": "Switching off..."}[job])
        self.update_actions()
        action, read_drives = getattr(self.actions, job), self.read_drives

        def work():                                     # holds no widget: one freed off the window's thread crashes
            now = fresh(read_drives, d)                 # the drive as it is now, not as it was listed
            return (False, "That drive is no longer there.") if now is None else action(now)
        self.later(work, self.acted, d)

    def acted(self, d, result):
        message = result[1] if isinstance(result, tuple) else f"It did not work: {result}"
        self.action_note.setText(message)

        def again():
            self.acting = False
            self.lock()
            same = next((i for i, x in enumerate(self.drives) if x.path and x.path == d.path), -1)
            if same >= 0:
                self.drive_list.setCurrentRow(same)     # picked by the user before; shows its new state
            self.update_actions()
            self.action_note.setText(message)
            self.working_changed.emit()
        self.load_drives(again)

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
            try:
                job.then(*first, job.result)
            except Exception as e:                      # one answer that cannot be shown must not hold the rest,
                self.failed.append(repr(e))             # nor leave the panel locked for good
                if self.acting:
                    self.acting = False
                    self.lock()
                    self.update_actions()
                    self.working_changed.emit()

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

    def show_note(self):
        """The hint under the folder box, without "Pick a drive above" once a drive is picked."""
        text = self.note_text
        if self.current_drive() is not None:
            if text.startswith("Pick a drive above, then "):
                text = "Pick " + text[len("Pick a drive above, then "):]
            else:
                text = text.removeprefix("Pick a drive above.").strip()
        self.note.setText(text)

    def on_drive(self):
        d = self.current_drive()
        self.action_note.setText("")
        self.show_note()
        self.update_actions()
        if d is None:
            self.looking = None
            self.health.setText("")
            if self.folder:
                self.set_folder("", "")                 # nothing picked: nothing to start
            return
        self.health.setText("\n".join(self.health_lines(d)))
        if d.smart:                                     # wear and warnings follow when the drive has answered
            read = self.read_smart                      # the job holds no widget (see `act`)
            self.later(lambda: read(d), self.health_read, d)
        self.look(d)

    def look(self, d):
        """Find the drive's folder off the window's thread: a slow or failing drive must not hold up the
        window (a USB disk with a read error hung it for a minute)."""
        self.set_folder("", LOOKING)
        self.looks += 1                                 # only the newest look counts: answers can come late
        self.looking = d.path
        self.later(d.target, self.target_read, d, self.looks)

    def target_read(self, d, number, result):
        now = self.current_drive()
        if now is None or now.path != d.path or self.looking != d.path or number != self.looks:
            return                                      # another drive or folder was picked meanwhile
        if isinstance(result, tuple):
            folder, why = result
            self.set_folder(folder or "", why or "Press Start.")
        else:
            self.set_folder("", f"The drive did not answer: {result}")

    def health_lines(self, d):
        """Health, temperature and hours, then wear and warnings from the drive's SMART record."""
        if not d.smart and d.flash and d.health.state == "unknown":
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
        if not path:
            return
        d = self.current_drive()
        if d is not None and not any(path == m or path.startswith(m.rstrip("/") + "/")
                                     for v in d.volumes for m in v.mounts):
            self.drive_list.blockSignals(True)          # the folder is elsewhere: the listed drive is not it
            self.drive_list.clearSelection()
            self.drive_list.blockSignals(False)
            self.health.setText("")
            self.show_note()
            self.update_actions()
        self.set_folder(path)

    def set_folder(self, path, hint=""):
        self.looking = None                             # a folder chosen by hand wins over a late answer
        self.folder = path
        self.folder_box.setText(path)
        self.changed.emit(path, hint)

    def set_busy(self, busy):
        self.busy = busy
        self.lock()
        self.update_actions()

    def lock(self):
        for w in (self.choose, self.drive_list, self.refresh):
            w.setEnabled(not (self.busy or self.acting))

    def adopt(self, other):
        """Take over what another tab's panel shows, so the user picks a drive once."""
        if other is self or (other.drives is self.drives and other.folder == self.folder
                             and other.row() == self.row()):
            return
        self.lists += 1                                 # a list of our own still on its way no longer counts
        self.loading = False
        waiting, self.after_list = self.after_list, []
        for then in waiting:
            then()
        if other.loading:                               # the other tab is still reading: read our own, do not
            self.load_drives()                          # copy its empty list and wait for an answer that never comes
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
        self.show_note()
        self.update_actions()
        if other.looking is not None and self.current_drive() is not None:
            self.look(self.current_drive())             # the other tab is still waiting for the drive
            return
        self.looking = None
        self.set_folder(other.folder, "Press Start." if other.folder else "")
