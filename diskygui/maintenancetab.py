# SPDX-License-Identifier: GPL-3.0-or-later
"""The Maintenance tab: check a partition's file system, repair it, and trim an SSD.

Check only reads. Repair changes the drive, so every Repair first shows the warnings that apply and asks.
The work is done by the system's own tools through the engine (`disky fscheck`, `disky fsrepair`).
"""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout

from disky import fscare

from . import driveactions
from .drivepanel import fresh
from .drives import NTFS, Drive, fs_name, size_text
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
    if last_check == "repaired":
        lines += ["", "It was repaired in this session and has not been checked since. Check only reads, and "
                      "tells you whether another repair is needed."]
    elif last_check is None:
        lines += ["", "It has not been checked yet. Check only reads, and tells you whether a repair is needed."]
    elif last_check:
        lines += ["", "The last check found no problems, so a repair is probably not needed."]
    else:
        lines += ["", "The last check found problems."]
    if failing:
        lines += ["", "This drive reports that it is FAILING. Repairing a dying drive can make things worse: "
                      "copy your files off before anything else."]
    return "\n".join(lines)


class MaintenanceTab(TrimTab):
    title = "Maintenance"
    USES_FOLDER = False                 # it works on partitions and whole drives: no folder to choose
    NO_STOP = ("check", "repair")       # a repair must never be cut short; a check is left to finish
    NOTE = "Pick a drive above, then a partition on the right."

    def extend(self, right):
        self.volumes = []
        self.checked = {}                               # (drive, partition) -> what its last check here found
        self.shown_drive = None
        self.maint_result = None
        self.verifying = False                      # the drive is being read again before a job
        about = QLabel(ABOUT)
        about.setObjectName("intro")
        about.setWordWrap(True)
        head = QLabel("Partitions")
        head.setObjectName("heading")
        self.parts = QListWidget()
        self.parts.setFixedHeight(96)
        self.parts.itemSelectionChanged.connect(self.show_advice)
        self.info = QLabel("Pick a drive on the left to see its partitions.")
        self.info.setWordWrap(True)
        self.check_button = QPushButton("Check")
        self.check_button.setObjectName("primary")
        self.check_button.setToolTip("Only reads: is the file system sound?")
        self.check_button.clicked.connect(self.check)
        self.repair_button = QPushButton("Repair")
        self.repair_button.setToolTip("Lets the system's tool fix the file system. Changes the drive; asks first")
        self.repair_button.clicked.connect(self.repair)
        self.maint_status = QLabel("")
        self.maint_status.setObjectName("status")
        self.maint_status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.maint_status, 1)
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
                self.maint_status.setText("")            # a result belongs to the drive it was got from
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
        d = self.panel.current_drive()
        shown = getattr(self, "status_of", None)
        if self.mode is None and shown is not None and (v is None or d is None or (d.path, v.device) != shown):
            self.maint_status.setText("")               # the result belonged to the partition picked before
            self.status_of = None
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
            ready = (self.mode is None and not self.verifying and not self.panel.acting
                     and v is not None and advice(v)[0])
            self.check_button.setEnabled(ready)
            self.repair_button.setEnabled(ready)

    # -- check and repair ------------------------------------------------------------------------------------
    def still_there(self, go):
        """Read the drive again from the system, off the window's thread, then `go(drive, partition)` as they are
        now, or say why not. A name like sdb1 can belong to another drive once drives were unplugged and plugged
        in, so nothing is done on the strength of the old list."""
        v, d = self.picked(), self.panel.current_drive()
        if (v is None or d is None or not advice(v)[0] or self.mode is not None or self.verifying
                or self.panel.acting):                  # never while the drive is being mounted or unmounted
            return
        self.verifying = True                           # no second click while the system answers
        self.working_changed()                          # and no other tab meanwhile
        read = self.panel.read_drives
        self.panel.later(lambda: fresh(read, d), self.verified, d, v, go)

    def verified(self, d, v, go, now):
        self.verifying = False
        if (self.mode is not None or self.panel.acting or self.picked() is not v
                or self.panel.current_drive() is not d):
            self.working_changed()
            return                                      # the user picked something else meanwhile
        now = now if isinstance(now, Drive) else None
        same = next((x for x in now.volumes if x.device == v.device), None) if now else None
        if same is None or (same.fstype, same.size) != (v.fstype, v.size) or not advice(same)[0]:
            self.panel.load_drives()                    # shows the drives as they are; nothing stays picked
            self.maint_status.setText("The drives changed since the list was read. Pick the drive again.")
            self.working_changed()
            return
        go(now, same)
        self.working_changed()

    def check(self):
        self.still_there(self.start_check)

    def start_check(self, d, v):
        self._maint("check", "fscheck", "Checking... this only reads.", d, v)

    def repair(self):
        self.still_there(self.ask_repair)

    def ask_repair(self, d, v):
        if self.confirm(repair_question(v, self.checked.get((d.path, v.device)), d.health.state == "failing",
                                        d.model)):
            # The question can stay open for any length of time: the drive is read once more after the yes,
            # so the repair runs on the partition that was confirmed and on nothing else.
            self.still_there(self.start_repair)
        else:
            self.update_buttons()

    def start_repair(self, d, v):
        self._maint("repair", "fsrepair", "Repairing... do not unplug the drive.", d, v, "--yes")

    def _maint(self, mode, command, words, d, v, *options):
        self.error = self.maint_result = None
        self.maint_device = (d.path, v.device)
        self.maint_status.setText(words)
        self.set_busy(mode)
        # The engine is told what was confirmed and refuses anything else under that name.
        self.runner.start(command, v.device, *options, "--expect", f"{v.fstype}:{v.size}", admin="if_open")

    def on_event(self, ev):
        if ev.get("event") == "fs_result":
            self.maint_result = ev
        else:
            super().on_event(ev)

    def on_finished(self, code, crash):
        if self.mode == "trim":
            return super().on_finished(code, crash)
        if crash:
            self.maint_status.setText("It did not finish. " + crash)
        elif self.error:
            self.maint_status.setText(self.error)
        elif self.maint_result:
            if self.mode == "check":
                self.checked[self.maint_device] = bool(self.maint_result.get("ok"))
            elif self.maint_result.get("ok"):
                self.checked[self.maint_device] = "repaired"    # the earlier check no longer describes it
            # The result names its partition, so it cannot be read as belonging to another one.
            self.maint_status.setText(f"{self.maint_device[1]}: {self.maint_result.get('message', '')}")
            self.status_of = self.maint_device
        else:
            self.maint_status.setText(f"Ended without a result (exit {code}).")
        self.set_busy(None)
