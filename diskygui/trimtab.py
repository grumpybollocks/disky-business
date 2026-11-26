# SPDX-License-Identifier: GPL-3.0-or-later
"""The TRIM part of the Care tab: says whether the drive accepts TRIM, and runs it through the system's
password prompt. On its own it is a complete tab; caretab.py builds on it."""
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from disky import trim

from .defragtab import admin_wrap
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
        self.go = QPushButton("Trim now")
        self.go.clicked.connect(self.start)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.go)
        right = QVBoxLayout()
        right.addWidget(about)
        right.addWidget(box)
        right.addStretch(1)
        right.addLayout(bottom)
        self.extend(right)
        self.assemble(right)
        self.update_buttons()

    def extend(self, right):
        """A tab built on this one adds its own part to the right-hand side here."""

    def on_folder(self, folder, hint):
        if self.mode is None:
            self.facts = self.info_of(folder) if folder else None
            self.hint = "" if folder else hint
            self.status.setText("")
            self.show_facts()
        self.update_buttons()

    def show_facts(self):
        f = self.facts
        if not f and self.panel.current_drive() is not None:
            self.answer.setText("Nothing to trim on this drive here.")
            self.detail.setText(getattr(self, "hint", "") or "Disky has no folder on it to work from.")
        elif not f:
            self.answer.setText("Choose a drive.")
            self.detail.setText("")
        elif f["supported"]:
            self.answer.setText("This drive accepts TRIM.")
            self.detail.setText(f"Mounted at {f['mount']}. It takes up to {size_text(f['discard_max_bytes'])} "
                                "at a time. Trimming asks for the admin password.")
        else:
            self.answer.setText("This drive does not accept TRIM.")
            self.detail.setText("Spinning disks have no use for it. Many USB cases do not pass it on to the SSD "
                                "inside, so it cannot be done through them. Nothing is offered.")

    def update_buttons(self):
        self.go.setEnabled(self.mode is None and bool(self.facts and self.facts["supported"]))
        self.panel.set_busy(self.mode is not None)

    def start(self):
        if self.mode is None and self.facts and self.facts["supported"] and self.confirm(
                f"Run TRIM on {self.facts['mount']}?\nIt tells the drive which space is free; files are not "
                "touched. The system will ask for the admin password."):
            self.error = self.result = None
            self.status.setText("Trimming...")
            self.set_busy("trim")
            self.runner.start("trim", self.folder, wrap=self.wrap())

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
            self.status.setText("Done: " + (f"{size_text(got)} of free space trimmed." if got is not None
                                            else self.result.get("message", "")))
        else:
            self.status.setText(f"Ended without a result (exit {code}).")
        self.set_busy(None)
