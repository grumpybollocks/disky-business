# SPDX-License-Identifier: GPL-3.0-or-later
"""The Care tab: check a partition's file system, repair it, and trim an SSD.

Check only reads. Repair changes the drive, so every Repair first shows the warnings that apply and asks.
The work is done by the system's own tools through the engine (`disky fscheck`, `disky fsrepair`).
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout

from disky import fscare

from . import driveactions
from .drives import NTFS, fs_name, size_text
from .trimtab import TrimTab

ABOUT = ("Check reads a partition's file system and says whether it is sound. Repair lets the system's own tool "
         "fix it; that changes the drive, so copy off what matters first.")
SYSTEM = ("Your system or home folder runs from this partition. It cannot be unmounted while in use, so it "
          "cannot be checked or repaired from here.")
MOUNTED = "Mounted at {where}. Unmount it first (button on the left); a file system in use cannot be checked."
READY = "Not mounted, so it is ready. Check only reads. Repair changes the drive."


def advice(volume, home=None):
    """(can it be checked or repaired now, what the user should know) for one partition."""
    if volume.fstype in NTFS:
        return False, fscare.NTFS_WHY
    if driveactions.in_system_use(volume, home):
        return False, SYSTEM
    if volume.mounts:
        return False, MOUNTED.format(where=volume.mounts[0])
    return True, READY


def repair_question(volume, last_check, failing, drive=""):
    """What the user reads before a repair starts: which drive, and only the warnings that apply."""
    where = f" on {drive}" if drive else ""
    lines = [f"Repair {volume.device} ({fs_name(volume.fstype)}, {size_text(volume.size)}){where}?", "",
             "Repair changes the drive. If files on it matter and you can still read them, copy them off first: "
             "a repair can remove damaged files.",
             "Once started it cannot be stopped. Do not unplug the drive while it runs."]
    if last_check is None:
        lines += ["", "It has not been checked yet. Check only reads, and tells you whether a repair is needed."]
    elif last_check:
        lines += ["", "The last check found no problems, so a repair is probably not needed."]
    else:
        lines += ["", "The last check found problems."]
    if failing:
        lines += ["", "This drive reports that it is FAILING. Repairing a dying drive can make things worse: "
                      "copy your files off before anything else."]
    return "\n".join(lines)


class CareTab(TrimTab):
    title = "Care"
    NOTE = "Pick a drive above, then a partition on the right."

    def extend(self, right):
        self.volumes = []
        self.checked = {}                               # (drive, partition) -> what its last check here found
        self.shown_drive = None
        self.care_result = None
        about = QLabel(ABOUT)
        about.setWordWrap(True)
        head = QLabel("Partitions")
        head.setObjectName("heading")
        self.parts = QListWidget()
        self.parts.setFixedHeight(96)
        self.parts.itemSelectionChanged.connect(self.show_advice)
        self.info = QLabel("Pick a drive on the left to see its partitions.")
        self.info.setWordWrap(True)
        self.check_button = QPushButton("Check")
        self.check_button.setToolTip("Only reads: is the file system sound?")
        self.check_button.clicked.connect(self.check)
        self.repair_button = QPushButton("Repair")
        self.repair_button.setToolTip("Lets the system's tool fix the file system. Changes the drive; asks first")
        self.repair_button.clicked.connect(self.repair)
        self.care_status = QLabel("")
        self.care_status.setObjectName("status")
        self.care_status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.care_status, 1)
        row.addWidget(self.check_button)
        row.addWidget(self.repair_button)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        trim_head = QLabel("TRIM")
        trim_head.setObjectName("heading")
        top = QVBoxLayout()
        for w in (about, head, self.parts, self.info):
            top.addWidget(w)
        top.addLayout(row)
        top.addSpacing(6)
        top.addWidget(line)
        top.addWidget(trim_head)
        right.insertLayout(0, top)

    # -- partitions ------------------------------------------------------------------------------------------
    def picked(self):
        rows = self.parts.selectionModel().selectedRows()
        return self.volumes[rows[0].row()] if rows and rows[0].row() < len(self.volumes) else None

    def on_folder(self, folder, hint):
        super().on_folder(folder, hint)
        if self.mode is None:
            d = self.panel.current_drive()
            if (d.path if d else None) != self.shown_drive:
                self.shown_drive = d.path if d else None
                self.care_status.setText("")            # a result belongs to the drive it was got from
            before = self.picked().device if self.picked() else None
            self.volumes = list(d.volumes) if d else []
            self.parts.blockSignals(True)
            self.parts.clear()
            self.parts.addItems([
                f"{v.device}    {fs_name(v.fstype)}    {size_text(v.size)}    "
                + ("mounted at " + v.mounts[0] if v.mounts else "not mounted") for v in self.volumes])
            self.parts.blockSignals(False)
            again = next((i for i, v in enumerate(self.volumes) if v.device == before), -1)
            if again >= 0:
                self.parts.setCurrentRow(again)         # the user's own pick, kept across a mount or unmount
            elif len(self.volumes) == 1:
                self.parts.setCurrentRow(0)             # a drive with one partition: nothing to choose
            self.show_advice()

    def show_advice(self):
        v = self.picked()
        if v is None:
            self.info.setText("Pick a partition." if self.volumes else
                              "Pick a drive on the left to see its partitions.")
        else:
            self.info.setText(advice(v)[1])
        self.update_buttons()

    def update_buttons(self):
        super().update_buttons()
        if hasattr(self, "check_button"):
            v = self.picked()
            ready = self.mode is None and v is not None and advice(v)[0]
            self.check_button.setEnabled(ready)
            self.repair_button.setEnabled(ready)

    # -- check and repair ------------------------------------------------------------------------------------
    def still_there(self):
        """(drive, partition) as they are now, or None after saying why not. Read again from the system each
        time: a name like sdb1 can belong to another drive once drives were unplugged and plugged in."""
        v, d = self.picked(), self.panel.current_drive()
        if v is None or d is None or not advice(v)[0] or self.mode is not None:
            return None
        now = self.panel.fresh(d)
        same = next((x for x in now.volumes if x.device == v.device), None) if now else None
        if same is None or (same.fstype, same.size) != (v.fstype, v.size) or not advice(same)[0]:
            self.panel.load_drives()                    # shows the drives as they are; nothing stays picked
            self.care_status.setText("The drives changed since the list was read. Pick the drive again.")
            return None
        return now, same

    def check(self):
        found = self.still_there()
        if found:
            self._care("check", "fscheck", "Checking... this only reads.", *found)

    def repair(self):
        found = self.still_there()
        if not found:
            return
        d, v = found
        if self.confirm(repair_question(v, self.checked.get((d.path, v.device)), d.health.state == "failing",
                                        d.model)):
            self._care("repair", "fsrepair", "Repairing... do not unplug the drive.", d, v, "--yes")

    def _care(self, mode, command, words, d, v, *options):
        self.error = self.care_result = None
        self.care_device = (d.path, v.device)
        self.care_status.setText(words)
        self.set_busy(mode)
        self.runner.start(command, v.device, *options)

    def on_event(self, ev):
        if ev.get("event") == "fs_result":
            self.care_result = ev
        else:
            super().on_event(ev)

    def on_finished(self, code, crash):
        if self.mode == "trim":
            return super().on_finished(code, crash)
        if crash:
            self.care_status.setText("It did not finish. " + crash)
        elif self.error:
            self.care_status.setText(self.error)
        elif self.care_result:
            if self.mode == "check":
                self.checked[self.care_device] = bool(self.care_result.get("ok"))
            self.care_status.setText(str(self.care_result.get("message", "")))
        else:
            self.care_status.setText(f"Ended without a result (exit {code}).")
        self.set_busy(None)
