# SPDX-License-Identifier: GPL-3.0-or-later
"""Block states and how they group into squares. No Qt here, so it is tested without a screen.

The engine reports per 1 MiB block. One square stands for many blocks. The rule for a square's colour:
  - any damaged block in it -> the worst damage (bad, then overwritten, then changed); damage is never hidden;
  - otherwise the least finished block (untouched, then written, then good), so a square turns green only when
    every block in it was read back good.
"""
import numpy as np

UNTOUCHED, WRITTEN, GOOD, CHANGED, OVERWRITTEN, BAD, WORKING = range(7)
NAMES = ("untouched", "written", "good", "changed", "overwritten", "bad", "working")
CODES = {"written": WRITTEN, "good": GOOD, "changed": CHANGED, "overwritten": OVERWRITTEN, "bad": BAD}


class MapModel:
    def __init__(self, layout=False):
        # layout=True (the Defrag map): a square shows the highest state in it (in pieces, whole file, other
        # data, free), so a file is never hidden by the free space around it in the same square.
        self.layout = layout
        self.reset()

    def reset(self, block_bytes=1 << 20):
        self.block_bytes = block_bytes
        self.states = np.zeros(0, np.uint8)
        self.cursor = None      # block being worked on now, or None when nothing runs
        self.version = 0        # goes up on every change; the widget redraws when it differs

    # -- feeding -------------------------------------------------------------------------------------------
    def set_total_blocks(self, n):
        """Size the map. Shrinks only over blocks nobody touched (the write pass's total is an estimate)."""
        cur = len(self.states)
        if n > cur:
            self.states = np.concatenate([self.states, np.zeros(n - cur, np.uint8)])
        elif n < cur and not self.states[n:].any():
            self.states = self.states[:n].copy()
        else:
            return
        self.version += 1

    def set_blocks(self, first, count, code):
        if count <= 0 or first < 0:
            return
        if first + count > len(self.states):
            self.set_total_blocks(first + count)
        self.states[first:first + count] = code
        self.cursor = first + count
        self.version += 1

    def apply(self, ev):
        """Take one engine event (a dict). Unknown events and fields are ignored, as the contract demands."""
        kind = ev.get("event")
        if kind == "start":
            self.reset(int(ev.get("block_bytes") or (1 << 20)))
            self.version += 1
        elif kind == "phase":
            total = ev.get("total_bytes")
            if total:
                self.set_total_blocks(-(-int(total) // self.block_bytes))
            self.cursor = 0
            self.version += 1
        elif kind == "blocks":
            code = CODES.get(ev.get("state"))
            if code is not None:
                self.set_blocks(int(ev["first"]), int(ev["count"]), code)
        elif kind in ("summary", "done", "error"):
            self.cursor = None
            self.version += 1

    # -- reading -------------------------------------------------------------------------------------------
    def bounds(self, max_squares):
        """Block index where each square starts, plus the end. Square i covers bounds[i] up to bounds[i+1]."""
        n = len(self.states)
        m = max_squares                 # a small drive spreads each block over several squares
        if m <= 0 or n <= 0:
            return np.zeros(1, np.int64)
        return (np.arange(m + 1, dtype=np.int64) * n) // m

    def squares(self, max_squares):
        """State code per square (see the rule at the top), and the bounds used."""
        b = self.bounds(max_squares)
        if len(b) < 2:
            return np.zeros(0, np.uint8), b
        worst = np.maximum.reduceat(self.states, b[:-1])
        least = np.minimum.reduceat(self.states, b[:-1])
        out = (worst if self.layout else np.where(worst >= CHANGED, worst, least)).astype(np.uint8)
        if self.cursor is not None and 0 <= self.cursor < len(self.states):
            hi = int(np.searchsorted(b[:-1], self.cursor, side="right"))
            lo = min(int(np.searchsorted(b[:-1], self.cursor, side="left")), hi - 1)
            part = out[lo:hi]
            part[part < CHANGED] = WORKING
        return out, b

    def counts(self):
        """How many blocks are in each state, by name."""
        c = np.bincount(self.states, minlength=len(NAMES))
        return {NAMES[i]: int(c[i]) for i in range(BAD + 1)}
