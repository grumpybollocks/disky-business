# SPDX-License-Identifier: GPL-3.0-or-later
"""The Surface tab: read a whole drive once, without writing, and show which blocks were slow or unreadable."""
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .adminsession import admin_wrap
from .drives import NTFS
from .mapmodel import BAD, CHANGED, GOOD, OVERWRITTEN, MapModel
from .mapwidget import BlockMap
from .tabbase import TabBase
from .trimtab import size_text

ABOUT = ("A surface scan reads the whole drive once, from the first block to the last, and times every block. "
         "It writes nothing. Slow blocks are places the drive struggles to read; unreadable ones are damage.")
# The map's own rule (the worst block in a square wins) is what a surface map needs too, so the scan's words
# are laid over the map's states: slow and worse are never hidden by the fast blocks around them.
CODES = {"fast": GOOD, "slow": CHANGED, "very_slow": OVERWRITTEN, "unreadable": BAD}
LEGEND = (("untouched", "Not read yet"), ("working", "Reading now"), ("good", "Fast"), ("changed", "Slow"),
          ("overwritten", "Very slow"), ("bad", "Unreadable"))
IDLE = "Pick a drive, then press Start."
NTFS_WORDS = "This drive holds a Windows (NTFS) partition. This program leaves such drives alone."


def time_left(seconds):
    if seconds is None:
        return ""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h left"
    return f"{round(seconds / 60)} min left" if seconds >= 90 else f"{int(seconds)} s left"


def verdict(result):
    """The result in plain words."""
    b = result.get("blocks") or {}
    fast, slow, very, bad = (int(b.get(k) or 0) for k in ("fast", "slow", "very_slow", "unreadable"))
    read = fast + slow + very + bad
    total = result.get("total_bytes") or 0
    head = ""
    if result.get("stopped"):
        done = min(100, 100 * read * (1 << 20) // total) if total else 0
        head = f"Stopped at {done} %. So far: "
    if not (slow or very or bad):
        return head + (f"all {read} blocks read in good time." if head else "Every block was read in good time.")
    parts = [f"{n} {word}" for n, word in ((bad, "unreadable"), (very, "very slow"), (slow, "slow")) if n]
    words = head + ", ".join(parts) + f" of {read} blocks. "
    if bad:
        return words + "Unreadable blocks are damage: copy your files off this drive now."
    if very:
        return words + "Very slow blocks are often the first sign of a failing drive: keep a backup and scan again."
    return words + "A few slow blocks are common (the drive was busy or waking up); many in one place deserve a second scan."


class SurfaceTab(TabBase):
    title = "Surface"
    NOTE = ""
    USES_FOLDER = False                 # it works on the drive itself

    def __init__(self, theme, runner=None, tip=None, drives=None, parent=None):
        super().__init__(theme, runner, tip, drives, parent)
        self.wrap = admin_wrap                          # the password prompt; replaced in tests
        self.result = None
        self.shown_for = None                           # the drive (its path) the map and the words belong to
        self.model = MapModel()
        self.map = BlockMap(self.model, theme)
        about = QLabel(ABOUT)
        about.setObjectName("intro")
        about.setWordWrap(True)
        caption = QLabel("Surface map: every block of the drive, by how long it took to read")
        caption.setObjectName("heading")
        self.status = QLabel(IDLE)
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        self.progress = self.progress_line()
        self.go = QPushButton("Start surface scan")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.start_or_stop)
        bottom = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(self.status)
        words.addWidget(self.progress)
        bottom.addLayout(words, 1)
        bottom.addWidget(self.go)
        right = QVBoxLayout()
        right.addWidget(about)
        right.addWidget(caption)
        right.addWidget(self.framed(self.map), 1)
        right.addLayout(self.legend(LEGEND, per_row=3))
        right.addLayout(bottom)
        self.assemble(right)
        self.set_theme(theme)
        self.update_buttons()

    def set_theme(self, theme):
        self.theme = theme
        self.map.set_look(theme)
        self.paint_legend(theme.states)

    # -- what may be scanned ---------------------------------------------------------------------------------
    def refusal(self, d):
        """Why this drive cannot be scanned, or "" when it can."""
        if d is None:
            return IDLE
        if not d.device:
            return "This drive has no device name the scan could read."
        if any(v.fstype in NTFS for v in d.volumes):
            return NTFS_WORDS
        return ""

    def update_buttons(self):
        running = self.mode is not None
        d = self.panel.current_drive()
        self.go.setText("Stop" if running else "Start surface scan")
        self.go.setEnabled(running or not self.refusal(d))
        self.progress.setVisible(running)
        self.status.setVisible(not running)
        self.panel.set_busy(running)

    def on_folder(self, folder, hint):
        d = self.panel.current_drive()
        if self.mode is None and (d.path if d else None) != self.shown_for:
            self.shown_for = d.path if d else None      # another drive: the last drive's map says nothing here
            self.result = None
            self.model.reset()
            self.model.version += 1
            self.status.setText(self.refusal(d) or f"{d.model}, {size_text(d.size)}. Press Start.")
        self.update_buttons()

    # -- the job ---------------------------------------------------------------------------------------------
    def start_or_stop(self):
        if self.mode is not None:
            self.progress.show_stopping()
            self.runner.stop()
            return
        d = self.panel.current_drive()
        if self.refusal(d):
            return
        if self.confirm(f"Surface scan of {d.model}?\nIt reads the whole drive ({size_text(d.size)}) once and "
                        "writes nothing. A large or slow drive takes hours; it can be stopped at any time. "
                        + self.password_words()):
            self.error = self.result = None
            self.shown_for = d.path
            self.model.reset()
            self.model.version += 1
            self.progress.show_progress("Starting...")
            self.set_busy("surface")
            self.runner.start("surface", d.device, wrap=self.wrap())

    def on_event(self, ev):
        kind = ev.get("event")
        if kind == "surface_start":
            self.model.set_total_blocks(int(ev.get("blocks") or 0))
            self.model.cursor = 0
            self.model.version += 1
        elif kind == "surface_blocks":
            code = CODES.get(ev.get("state"))
            if code is not None:
                self.model.set_blocks(int(ev["first"]), int(ev["count"]), code)
        elif kind == "surface_progress":
            total = ev.get("total_bytes")
            self.progress.show_progress("Reading the drive", 100 * ev["done_bytes"] / total if total else None,
                                        f"{ev.get('mib_per_s', 0):.0f} MiB/s", time_left(ev.get("eta_s")))
        elif kind == "surface_result":
            self.result = ev
        elif kind == "error":
            self.error = ev.get("message") or "The engine reported an error."

    def on_finished(self, code, crash):
        self.model.cursor = None
        self.model.version += 1
        if crash:
            self.status.setText("It did not finish. " + crash)
        elif self.error:
            self.status.setText(self.error)
        elif code in (126, 127):
            self.status.setText("Not scanned: the admin password was not given.")
        elif self.result:
            self.status.setText(verdict(self.result))
        else:
            self.status.setText(f"Ended without a result (exit {code}).")
        self.set_busy(None)
