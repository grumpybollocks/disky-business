# SPDX-License-Identifier: GPL-3.0-or-later
"""The TRIM part of the Care tab: says whether the drive accepts TRIM, and runs it through the system's
password prompt. On its own it is a complete tab; caretab.py builds on it."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from disky import trim

from .defragtab import admin_wrap
from .drives import NTFS, top_mounts
from .tabbase import TabBase

ABOUT = ("TRIM tells an SSD which space is free, so it can prepare it for new data and stay fast. It never "
         "touches your files. Linux usually does it once a week by itself, so this is rarely needed.")


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
        self.go = QPushButton("Trim this drive")
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
        self.update_buttons()

    def find_targets(self, folder):
        """What "Trim this drive" would trim: every mounted file system of the picked drive that accepts TRIM.
        No folder is needed. Windows (NTFS) partitions are left alone. Without a picked drive, the folder
        chosen by hand counts."""
        d = self.panel.current_drive()
        places = [top_mounts(v.mounts)[0] for v in d.volumes if v.mounts and v.fstype not in NTFS] if d else []
        if not places and folder and d is None:
            places = [folder]
        facts = [self.info_of(place) for place in places]
        self.targets = [place for place, f in zip(places, facts) if f and f["supported"]]
        self.facts = next((f for f in facts if f and f["supported"]), facts[0] if facts else None)
        self.unmounted = bool(d and not places)

    def show_facts(self):
        f = self.facts
        if getattr(self, "unmounted", False):
            self.answer.setText("Mount this drive first.")
            self.detail.setText("TRIM works through a mounted file system. Press Mount, on the left, then look "
                                "here again.")
        elif not f:
            self.answer.setText("Choose a drive.")
            self.detail.setText("")
        elif f["supported"]:
            self.answer.setText("This drive accepts TRIM.")
            where = ", ".join(self.targets)
            text = (f"\"Trim this drive\" tells it which space is free ({where}). Files are not touched. It "
                    "needs admin rights.")
            if f.get("auto_discard"):
                text += (" It is mounted with continuous TRIM (the discard option), so the drive already hears "
                         "about freed space as files are deleted; this is rarely needed.")
            self.detail.setText(text)
        else:
            self.answer.setText("This drive does not accept TRIM.")
            self.detail.setText("Spinning disks have no use for it. Many USB cases do not pass it on to the SSD "
                                "inside, so it cannot be done through them. Nothing is offered.")

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
