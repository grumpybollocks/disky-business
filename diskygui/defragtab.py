# SPDX-License-Identifier: GPL-3.0-or-later
"""The Defrag tab: scan how fragmented a drive's files are, and defragment one file where that is safe.

  btrfs, ext4, XFS   scan without a password; one file at a time can be defragmented with the system's tool
  exFAT              scan only, and it asks for the admin password (the drive's tables are read directly);
                     no defragmenter is offered because none can be trusted yet
  anything else      nothing is offered
"""
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from disky import frag, safety

from .drives import fs_name
from .mapmodel import BAD, GOOD, UNTOUCHED, WRITTEN, MapModel
from .mapwidget import BlockMap
from .runner import _ROOT
from .tabbase import TabBase

LEGEND = (("free", "Free"), ("other", "Other data"), ("whole", "File in one piece"), ("fragmented", "File in pieces"))
AS_STATE = {"free": "untouched", "other": "written", "whole": "good", "fragmented": "bad"}
MAP_CODE = {"free": UNTOUCHED, "other": WRITTEN, "whole": GOOD, "fragmented": BAD}
NTFS_WHY = ("Windows (NTFS) drives are not scanned or defragmented here. Linux has no NTFS defragmenter that can "
            "be trusted, and Linux may change a damaged NTFS drive just by reading it. Windows defragments its "
            "own drives by itself (Optimise Drives).")
NO_TOOL = ("No defragmenter is offered for {fs}: none exists for Linux that can be trusted with your files.")


def size_text(n):
    for unit, step in (("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return f"{n} bytes"


def admin_wrap():
    """Command put in front of the engine when it must read a partition directly."""
    return ["pkexec", "env", f"PYTHONPATH={_ROOT}", "PYTHONDONTWRITEBYTECODE=1"]


def share_text(part, whole):
    """A share in words a person reads right: never "0.0 %" for something that is there."""
    if not part:
        return "0 %"
    share = 100 * part / whole
    return "under 0.1 %" if share < 0.1 else f"{share:.1f} %"


class DefragTab(TabBase):
    title = "Defrag"
    NOTE = "Pick a drive above. Scanning only reads."

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.fs_type = frag.fs_type                 # replaced in tests
        self.device_of = safety.mount_source
        self.worst = []
        self.summary = self.result = None
        self.defrag_row = -1
        self.model = MapModel()
        self.map = BlockMap(self.model, theme)
        self.map.setMinimumHeight(120)
        frame = self.framed(self.map)
        key = self.legend(LEGEND)

        self.files = QListWidget()
        self.files.setFixedHeight(170)
        self.files.itemSelectionChanged.connect(self.update_buttons)
        self.status = QLabel("Choose a folder, then press Scan.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self.scan_or_stop)
        self.defrag_button = QPushButton("Defragment this file")
        self.defrag_button.clicked.connect(self.defrag_selected)
        buttons = QVBoxLayout()
        buttons.addWidget(self.scan_button)
        buttons.addWidget(self.defrag_button)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addLayout(buttons)

        caption = QLabel("Drive layout: where the files lie (a scan only reads)")
        caption.setObjectName("caption")
        right = QVBoxLayout()
        right.addWidget(caption)
        right.addWidget(frame, 1)
        right.addLayout(key)
        right.addWidget(QLabel("Most fragmented files"))
        right.addWidget(self.files)
        right.addLayout(bottom)
        self.assemble(right)
        self.set_theme(theme)
        self.update_buttons()

    def set_theme(self, theme):
        self.theme = theme
        self.map.set_look(theme, theme.frag_style, {AS_STATE[k]: c for k, c in theme.frag.items()})
        self.paint_legend(theme.frag)

    def kind(self):
        return self.fs_type(self.folder) if self.folder else ""

    def on_folder(self, folder, hint):
        if self.mode is None:
            self.worst = []
            self.files.clear()
            self.model.reset()
            self.model.version += 1
            fs = self.kind()
            picked = self.panel.current_drive()
            windows = fs in frag.HANDS_OFF if folder else bool(
                picked and any(v.fstype in frag.HANDS_OFF for v in picked.volumes))
            if windows:
                self.status.setText(NTFS_WHY)
            elif not folder:
                self.status.setText(hint or "Pick a drive, then press Scan.")
            elif fs in frag.TOOLS:
                self.status.setText(f"{fs_name(fs)}: press Scan. Scanning only reads.")
            elif fs == "exfat":
                self.status.setText("exFAT: press Scan. It only reads, and asks for the admin password because "
                                    "the drive's tables are read directly. " + NO_TOOL.format(fs="exFAT"))
            else:
                self.status.setText(f"No fragmentation information for {fs_name(fs) if fs else 'this file system'} yet.")
        self.update_buttons()

    def _picked(self):
        """The file the user picked; -1 when none (keyboard focus alone picks nothing)."""
        rows = self.files.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def update_buttons(self):
        fs = self.kind()
        busy = self.mode is not None
        self.scan_button.setText("Stop" if self.mode == "scan" else "Scan")
        self.scan_button.setEnabled(bool(self.folder) and (fs in frag.TOOLS or fs == "exfat") and self.mode != "defrag")
        self.defrag_button.setEnabled(not busy and fs in frag.TOOLS and 0 <= self._picked() < len(self.worst))
        self.panel.set_busy(busy)

    def scan_command(self):
        """(mode, target, wrapper) for the folder's file system, or None."""
        fs = self.kind()
        if fs in frag.TOOLS:
            return "fragfiles", self.folder, ()
        if fs == "exfat":
            device = self.device_of(self.folder)
            if device:
                return "fragscan", device, tuple(admin_wrap())
        return None

    def scan_or_stop(self):
        if self.mode == "scan":
            self.status.setText("Stopping...")
            self.runner.stop()
            return
        cmd = self.scan_command()
        if self.mode is None and cmd:
            mode, target, wrap = cmd
            if wrap and not self.confirm(
                    f"Scan {target}?\nReading an exFAT drive directly needs admin rights, so the system will ask "
                    "for the admin password. The scan only reads; nothing on the drive is changed."):
                return
            self._begin("scan", "Scanning...")
            self.worst = []
            self.files.clear()
            self.model.reset()
            self.model.version += 1
            self.runner.start(mode, target, wrap=wrap)

    def defrag_selected(self):
        row = self._picked()
        if self.mode is not None or not 0 <= row < len(self.worst) or self.kind() not in frag.TOOLS:
            return
        path = self.worst[row]["path"]
        if self.confirm(f"Defragment this file with the system's own tool?\n{path}\n\n"
                        "Its content is checked before and after."):
            self.defrag_row = row
            self._begin("defrag", "Defragmenting...")
            self.runner.start("defrag", path)

    def _begin(self, mode, text):
        self.error = None
        self.status.setText(text)
        self.set_busy(mode)

    def _show_worst(self):
        self.files.clear()
        self.files.addItems([f"{w['pieces']} pieces    {size_text(w['bytes'])}    {w['path']}" for w in self.worst])

    def on_event(self, ev):
        kind = ev.get("event")
        if kind in ("frag_files", "frag_summary"):
            self.worst = list(ev.get("worst", []))
            self._show_worst()
            files, bad = ev.get("files", 0), ev.get("fragmented_files", 0)
            pct = f" ({share_text(bad, files)})" if files else ""
            self.summary = (f"{bad} of {files} files are in more than one piece{pct}, holding "
                            f"{size_text(ev.get('fragmented_bytes', 0))} of {size_text(ev.get('file_bytes', 0))}.")
            if ev.get("skipped"):
                self.summary += f" {ev['skipped']} could not be read."
            if kind == "frag_summary":
                self.model.set_total_blocks(int(ev.get("clusters", 0)))
        elif kind == "frag_blocks":
            code = MAP_CODE.get(ev.get("state"))
            if code is not None and code != UNTOUCHED:
                self.model.set_blocks(int(ev["first"]), int(ev["count"]), code)
                self.model.cursor = None
        elif kind == "defrag_result":
            self.result = ev
        elif kind == "warning":
            self.tip(ev.get("message", ""))
        elif kind == "error":
            self.error = ev.get("message") or "The engine reported an error."

    def on_finished(self, code, crash):
        mode = self.mode
        self.model.cursor = None
        self.model.version += 1
        if crash:
            self.status.setText("It did not finish. " + crash)
        elif self.error:
            self.status.setText(self.error)
        elif code in (126, 127) and mode == "scan":
            self.status.setText("Not scanned: the admin password was not given.")
        elif code == 130:
            self.status.setText("Stopped.")
        elif mode == "scan" and getattr(self, "summary", None):
            self.status.setText(self.summary + ("" if self.kind() in frag.TOOLS else " " + NO_TOOL.format(fs=fs_name(self.kind()))))
        elif mode == "defrag" and getattr(self, "result", None):
            r = self.result
            if code != 0 or r.get("exit"):
                self.status.setText(f"The system tool did not finish the job (exit {r.get('exit')}). "
                                    f"{r.get('before')} pieces before, {r.get('after')} now. Content checked: unchanged.")
            else:
                self.status.setText(f"{r['before']} pieces before, {r['after']} after. Content checked: unchanged.")
            if 0 <= self.defrag_row < len(self.worst):
                self.worst[self.defrag_row]["pieces"] = r["after"]
                self._show_worst()
        else:
            self.status.setText(f"Ended without a result (exit {code}).")
        self.summary = self.result = None
        self.set_busy(None)

