# SPDX-License-Identifier: GPL-3.0-or-later
"""The block map: grouping rule (no Qt) and the widget (skipped where PySide6 is not installed)."""
import os
import time
import unittest

import numpy as np

from diskygui import mapmodel as mm

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
    from diskygui.mapwidget import BlockMap, MARGIN
    from diskygui.theme import THEMES
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise                   # our own code failing to import must fail loudly, never skip
    HAVE_QT = False

DRIVE = 114_688  # blocks on a 112 GiB drive


def full_run(model, n=DRIVE, bad=()):
    """A whole test as the engine reports it: write pass, then verify pass with damaged blocks alone."""
    model.apply({"event": "start", "contract": 1, "block_bytes": 1 << 20, "sector_bytes": 512})
    model.apply({"event": "phase", "phase": "write", "total_bytes": n << 20})
    for first in range(0, n, 1024):
        model.apply({"event": "blocks", "phase": "write", "first": first, "count": min(1024, n - first),
                     "state": "written"})
    model.apply({"event": "phase", "phase": "verify", "total_bytes": n << 20})
    at = 0
    for b in sorted(bad):
        if b > at:
            model.apply({"event": "blocks", "phase": "verify", "first": at, "count": b - at, "state": "good"})
        model.apply({"event": "blocks", "phase": "verify", "first": b, "count": 1, "state": "bad"})
        at = b + 1
    if at < n:
        model.apply({"event": "blocks", "phase": "verify", "first": at, "count": n - at, "state": "good"})
    model.apply({"event": "summary", "verdict": "bad" if bad else "good"})


class Grouping(unittest.TestCase):
    def test_one_bad_block_is_never_hidden(self):
        for n, bad in ((100_000, 73_519), (100_000, 0), (100_000, 99_999), (DRIVE, 5)):
            for k in (1, 7, 300, 2_500, 9_000, n, n + 50):
                m = mm.MapModel()
                full_run(m, n, [bad])
                sq, b = m.squares(k)
                hits = np.nonzero(sq == mm.BAD)[0]
                self.assertGreaterEqual(len(hits), 1, (n, bad, k))          # shown at every map size
                self.assertEqual(len(hits), 1 if k <= n else len(hits))     # once, unless squares outnumber blocks
                for i in hits.tolist():
                    self.assertTrue(b[i] <= bad < max(b[i + 1], b[i] + 1), (n, bad, k))
                self.assertTrue((np.delete(sq, hits) == mm.GOOD).all())

    def test_worst_damage_wins(self):
        m = mm.MapModel()
        m.set_blocks(0, 10, mm.GOOD)
        m.set_blocks(3, 1, mm.CHANGED)
        m.set_blocks(4, 1, mm.OVERWRITTEN)
        m.cursor = None
        self.assertEqual(m.squares(1)[0].tolist(), [mm.OVERWRITTEN])
        m.set_blocks(9, 1, mm.BAD)
        m.cursor = None
        self.assertEqual(m.squares(1)[0].tolist(), [mm.BAD])

    def test_square_is_green_only_when_every_block_is(self):
        m = mm.MapModel()
        m.set_blocks(0, 100, mm.WRITTEN)
        m.set_blocks(0, 55, mm.GOOD)
        m.cursor = None
        self.assertEqual(m.squares(10)[0].tolist(), [mm.GOOD] * 5 + [mm.WRITTEN] * 5)

    def test_working_square_follows_the_run_and_never_covers_damage(self):
        m = mm.MapModel()
        m.apply({"event": "phase", "phase": "write", "total_bytes": 100 << 20})
        m.apply({"event": "blocks", "first": 0, "count": 30, "state": "written"})
        self.assertEqual(m.squares(10)[0].tolist(), [mm.WRITTEN] * 3 + [mm.WORKING] + [mm.UNTOUCHED] * 6)
        m.set_blocks(30, 1, mm.BAD)
        m.cursor = 30
        self.assertEqual(m.squares(10)[0][3], mm.BAD)
        m.apply({"event": "summary"})
        self.assertNotIn(mm.WORKING, m.squares(10)[0].tolist())

    def test_events(self):
        m = mm.MapModel()
        full_run(m, 5000, [17])
        self.assertEqual(m.counts(), {"untouched": 0, "written": 0, "good": 4999, "changed": 0,
                                      "overwritten": 0, "bad": 1})
        v = m.version
        m.apply({"event": "something_new", "x": 1})
        m.apply({"event": "blocks", "first": 0, "count": 1, "state": "a_state_from_the_future"})
        self.assertEqual((m.version, m.counts()["good"]), (v, 4999))

    def test_total_is_an_estimate(self):
        m = mm.MapModel()
        m.apply({"event": "phase", "phase": "write", "total_bytes": 1000 << 20})
        m.set_blocks(0, 990, mm.WRITTEN)
        m.apply({"event": "phase", "phase": "verify", "total_bytes": 990 << 20})
        self.assertEqual(len(m.states), 990)       # the untouched tail goes
        m.apply({"event": "phase", "phase": "verify", "total_bytes": 500 << 20})
        self.assertEqual(len(m.states), 990)       # written blocks are never dropped
        m.set_blocks(995, 10, mm.WRITTEN)
        self.assertEqual(len(m.states), 1005)      # and the map grows when the drive holds more


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Widget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, w=1920, h=1080, **run):
        m = mm.MapModel()
        full_run(m, **run)
        v = BlockMap(m)
        v.resize(w, h)
        v.refresh()
        return m, v

    def colour_at(self, v, i):
        x, y = v._xy(i)
        return v.grab().toImage().pixelColor(x + v.style.size // 2, y + v.style.size // 2).name()

    def test_bad_square_is_drawn_bad_at_full_hd(self):
        m, v = self.make(bad=[60_000])
        self.assertGreater(DRIVE, v.cols * v.rows)      # squares really are grouped here
        sq, _ = m.squares(v.cols * v.rows)
        i = int(np.nonzero(sq == mm.BAD)[0][0])
        t = THEMES["w98"]
        self.assertEqual(self.colour_at(v, i), QColor(t.states["bad"]).name())
        self.assertEqual(self.colour_at(v, i + 1), QColor(t.states["good"]).name())
        first, count = v.blocks_of(i)
        self.assertTrue(first <= 60_000 < first + count)

    def test_square_at_matches_position(self):
        _, v = self.make(w=640, h=480)
        x, y = v._xy(v.cols + 3)
        self.assertEqual(v.square_at(x + 2, y + 2), v.cols + 3)
        self.assertIsNone(v.square_at(x + v.style.size, y + 2))    # the gap between squares
        self.assertIsNone(v.square_at(MARGIN - 1, MARGIN))

    def test_small_drive_gets_one_square_per_block(self):
        m, v = self.make(w=640, h=480, n=64)
        squares = v.cols * v.rows
        self.assertGreater(squares, 64)
        self.assertEqual(len(v._shown), squares)                 # the whole map is used
        self.assertEqual((v.blocks_of(0), v.blocks_of(squares - 1)), ((0, 1), (63, 1)))
        owners = [v.blocks_of(i)[0] for i in range(squares)]
        self.assertEqual(sorted(set(owners)), list(range(64)))   # every block is on the map
        self.assertEqual(owners, sorted(owners))                 # in order

    def test_one_bad_block_on_a_small_drive_is_shown_and_nothing_else_is(self):
        m, v = self.make(w=640, h=480, n=64, bad=[10])
        shown = v._shown.tolist()
        bad = [i for i, s in enumerate(shown) if s == mm.BAD]
        self.assertTrue(bad)
        self.assertEqual({v.blocks_of(i)[0] for i in bad}, {10})
        self.assertEqual(set(shown) - {mm.BAD}, {mm.GOOD})

    @unittest.skipIf(os.environ.get("CI"), "shared CI runners are too uneven for a 16 ms limit")
    def test_repaint_fits_one_frame(self):
        """Plan step 2.3: a repaint under 16 ms at 1920x1080 while a run feeds the map."""
        m = mm.MapModel()
        m.apply({"event": "phase", "phase": "write", "total_bytes": DRIVE << 20})
        v = BlockMap(m)
        v.resize(1920, 1080)
        v.refresh()
        times = []
        for first in range(0, DRIVE, 512):              # about 220 updates, as during a run
            m.apply({"event": "blocks", "first": first, "count": 512, "state": "written"})
            t0 = time.perf_counter()
            v.refresh()
            v.grab()
            times.append(time.perf_counter() - t0)
        times.sort()
        self.assertLess(times[len(times) // 2] * 1000, 16, "median")
        self.assertLess(times[-1] * 1000, 50, "slowest")


if __name__ == "__main__":
    unittest.main()
