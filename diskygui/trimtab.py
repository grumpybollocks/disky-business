# SPDX-License-Identifier: GPL-3.0-or-later
"""The TRIM part of the Maintenance tab: says whether the drive accepts TRIM, and runs it through the system's
password prompt. On its own it is a complete tab; maintenancetab.py builds on it."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from disky import trim

from .defragtab import admin_wrap
from .drives import NTFS, top_mounts
from .tabbase import TabBase

ABOUT = ("TRIM tells an SSD which space is free, so it can prepare it for new data and stay fast. It never "
         "touches your files.")


def size_text(n):
    for unit, step in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


class TrimTab(TabBase):
    title = "TRIM"
    NOTE = "Pick a drive above."

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.info_of = trim.info                    # replaced in tests
        self.weekly_of = trim.weekly                # replaced in tests
        self.weekly = None                          # Linux's weekly TRIM, read off the window's thread
        self.wrap = admin_wrap                      # the password prompt; replaced in tests
        self.facts = None
        self.result = None
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
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.go = QPushButton("Trim now")
        self.go.setToolTip("Tell the SSD which space is free. Files are not touched")
        self.go.clicked.connect(self.start)
        self.targets = []                               # the mounted file systems of the picked drive to trim
        self.queue = []
        self.trimmed = 0
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.go)
        right = QVBoxLayout()
        right.addWidget(about)
        right.addWidget(box)
        right.addLayout(bottom)                         # the button right under the answer, not at the far bottom
        right.addStretch(1)
        self.extend(right)
        self.assemble(right)
        self.update_buttons()

    def extend(self, right):
        """A tab built on this one adds its own part to the right-hand side here."""

    def on_folder(self, folder, hint):
        if self.mode is None:
            self.hint = "" if folder else hint
            self.find_targets(folder)
            self.status.setText("")
            self.show_facts()
            if self.facts and self.facts["supported"]:
                weekly_of = self.weekly_of                  # systemd may be slow: asked off this thread
                self.panel.later(weekly_of, self.weekly_read)
        self.update_buttons()

    def find_targets(self, folder):
        """What "Trim now" would trim: every mounted file system of the picked drive that accepts TRIM.
        No folder is needed. Windows (NTFS) partitions are left alone. Without a picked drive, the folder
        chosen by hand counts."""
        d = self.panel.current_drive()
        places = [top_mounts(v.mounts)[0] for v in d.volumes if v.mounts and v.fstype not in NTFS] if d else []
        if not places and folder and d is None:
            places = [folder]
        facts = [self.info_of(place) for place in places]
        self.targets = [place for place, f in zip(places, facts) if f and f["supported"]]
        self.facts = next((f for f in facts if f and f["supported"]), facts[0] if facts else None)
        self.windows_only = bool(d and not places and any(v.mounts and v.fstype in NTFS for v in d.volumes))
        self.unmounted = bool(d and not places and not self.windows_only)

    def weekly_read(self, result):
        self.weekly = result if isinstance(result, dict) else {"on": None, "last": "", "next": ""}
        if self.mode is None:
            self.show_facts()

    def weekly_line(self):
        w = self.weekly
        if w is None:
            return "Weekly TRIM by Linux: checking..."
        if w["on"] is None:
            return "Weekly TRIM by Linux: could not be read."
        if not w["on"]:
            return "Weekly TRIM by Linux: off."
        when = ([f"Last ran {w['last']}"] if w["last"] else []) + (
            [("next " if w["last"] else "Next ") + w["next"]] if w["next"] else [])
        return "Weekly TRIM by Linux: on." + (f" {', '.join(when)}." if when else "")

    def show_facts(self):
        f, d = self.facts, self.panel.current_drive()
        # The drive service knows the kind; the kernel's "rotational" is wrong behind most USB cases.
        spinning = d.kind == "HDD" if d is not None else bool(f and f.get("rotational"))
        if spinning:
            self.answer.setText("Not needed: spinning disks have no use for TRIM.")
            self.detail.setText("Accepts TRIM: no. Only SSDs use it.")
        elif getattr(self, "windows_only", False):
            self.answer.setText("Windows (NTFS) drive: left alone.")
            self.detail.setText("Disky does not trim Windows drives. Windows trims its own drives when it runs.")
        elif getattr(self, "unmounted", False):
            self.answer.setText("Mount this drive first.")
            self.detail.setText("TRIM goes through a mounted file system. Press Mount, under the drive list, then "
                                "look here again.")
        elif not f:
            self.answer.setText("Choose a drive.")
            self.detail.setText("")
        elif not f["supported"]:
            self.answer.setText("This drive does not accept TRIM.")
            self.detail.setText("Accepts TRIM: no. If this is an SSD in a USB case, the case does not pass TRIM "
                                "on; connected inside the computer it would. Most USB sticks and memory cards "
                                "do not take it either.")
        else:
            w = self.weekly or {}
            if f.get("auto_discard"):
                answer = "Not needed: the drive is told about freed space straight away."
            elif w.get("on") and d is not None and d.usb:
                answer = "Optional: Linux trims it once a week, but only if it is plugged in then."
            elif w.get("on"):
                answer = "Not needed: Linux trims this drive once a week."
            elif w.get("on") is False:
                answer = "Worth doing: nothing trims this drive by itself."
            else:
                answer = "This drive accepts TRIM."
            self.answer.setText(answer)
            self.detail.setText("\n".join([
                "Accepts TRIM: yes.",
                self.weekly_line(),
                "Continuous TRIM (the discard mount option): " + ("on." if f.get("auto_discard") else "off."),
                f"Trim now covers: {', '.join(self.targets)}. It tells the drive which space is free right away; "
                "files are not touched. It needs admin rights."]))

    def update_buttons(self):
        self.go.setEnabled(self.mode is None and bool(self.targets))
        self.panel.set_busy(self.mode is not None)

    def start(self):
        if self.mode is None and self.targets and self.confirm(
                f"Run TRIM on {', '.join(self.targets)}?\nIt tells the drive which space is free; files are not "
                "touched. " + self.password_words()):
            self.error = self.result = None
            self.queue, self.trimmed = list(self.targets), 0
            self.status.setText("Trimming...")
            self.set_busy("trim")
            self.runner.start("trim", self.queue.pop(0), wrap=self.wrap())

    def on_event(self, ev):
        kind = ev.get("event")
        if kind == "trim_result":
            self.result = ev
        elif kind == "error":
            self.error = ev.get("message") or "The engine reported an error."

    def on_finished(self, code, crash):
        if crash:
            self.status.setText("It did not finish. " + crash)
        elif self.error:
            self.status.setText(self.error)
        elif code in (126, 127):
            self.status.setText("Not trimmed: the admin password was not given.")
        elif self.result:
            got = self.result.get("trimmed_bytes")
            self.trimmed += got or 0
            if self.queue:                              # the drive's next file system, in the same session
                self.result = None
                self.continuing = True                  # still the same job: the tab stays busy
                self.runner.start("trim", self.queue.pop(0), wrap=self.wrap())
                return
            self.status.setText("Done: " + (f"{size_text(self.trimmed)} of free space trimmed."
                                            if got is not None else self.result.get("message", "")))
        else:
            self.status.setText(f"Ended without a result (exit {code}).")
        self.set_busy(None)
