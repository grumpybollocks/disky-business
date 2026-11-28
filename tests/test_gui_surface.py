# SPDX-License-Identifier: GPL-3.0-or-later
"""The Surface tab, with the pretend engine and made-up drives."""
import os
import sys
import time
import unittest

import tests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import drives as dr
    from diskygui import mapmodel, surfacetab
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")


def drive(model="EXAMPLE DISK", fs="exfat", device="sdz", path="/d/z"):
    return dr.Drive(model, 400 << 20, "HDD", True, dr.Health(), [dr.Volume(device + "1", 400 << 20, fs, [])],
                    path=path, device=device)


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class Tab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, found=None, yes=True):
        self.win = MainWindow(theme=THEMES["w98"], runners={"Surface": EngineRunner([sys.executable, FAKE, scenario])},
                              drives=lambda: list(found if found is not None else [drive()]))
        self.win.resize(1180, 760)
        self.win.show()
        self.win.confirm_close = lambda text: True
        self.addCleanup(self.win.close)
        tab = next(p for p in self.win.pages if p.title == "Surface")
        self.win.tabs.setCurrentWidget(tab)
        tab.panel.settle()
        tab.wrap = lambda: ()
        self.asked = []
        tab.confirm = lambda text: self.asked.append(text) or yes
        return tab

    def pick(self, tab, row=0):
        tab.panel.drive_list.setCurrentRow(row)
        tab.panel.settle()

    def wait(self, done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def test_a_sound_drive(self):
        tab = self.open("good")
        self.assertEqual((tab.go.isEnabled(), tab.status.text()), (False, surfacetab.IDLE))
        self.pick(tab)
        self.assertTrue(tab.go.isEnabled())
        self.assertIn("EXAMPLE DISK", tab.status.text())
        tab.go.click()
        self.assertEqual(len(self.asked), 1)
        self.assertIn("writes nothing", self.asked[0])
        self.assertIn("EXAMPLE DISK", self.asked[0])
        self.assertTrue(tests.others_locked(self.win, tab))             # one job at a time
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.runner.last_argv[-4:], ["surface", "sdz", "--json", "--stdin-stop"])
        self.assertEqual(tab.status.text(), "Every block was read in good time.")
        self.assertEqual(tab.model.counts()["good"], 400)
        self.assertEqual(tab.problems_seen, [])
        self.assertTrue(tab.panel.folder_box.isHidden())                # it works on the drive, not in a folder

    def test_slow_and_unreadable_blocks_reach_the_map_and_the_words(self):
        tab = self.open("bad")
        self.pick(tab)
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        c = tab.model.counts()
        self.assertEqual((c["good"], c["changed"], c["overwritten"], c["bad"]), (394, 3, 1, 2))
        self.assertEqual(tab.status.text(), "2 unreadable, 1 very slow, 3 slow of 400 blocks. Unreadable blocks "
                         "are damage: copy your files off this drive now.")
        squares, _ = tab.model.squares(40)                              # ten blocks to a square
        self.assertEqual((squares[10], squares[20], squares[30], squares[0]),
                         (mapmodel.CHANGED, mapmodel.OVERWRITTEN, mapmodel.BAD, mapmodel.GOOD))   # never hidden

    def test_the_words_for_each_outcome(self):
        say = surfacetab.verdict
        b = lambda **k: dict({"fast": 100, "slow": 0, "very_slow": 0, "unreadable": 0}, **k)   # noqa: E731
        self.assertIn("first sign", say({"blocks": b(very_slow=2)}))
        self.assertIn("common", say({"blocks": b(slow=2)}))
        self.assertEqual(say({"blocks": b(), "stopped": True, "total_bytes": 400 << 20}),
                         "Stopped at 25 %. So far: all 100 blocks read in good time.")
        self.assertEqual(say({}), "Every block was read in good time.")  # a result with holes breaks nothing

    def test_stop(self):
        tab = self.open("slow")
        self.pick(tab)
        tab.go.click()
        self.wait(lambda: tab.model.counts()["good"] > 3)
        self.assertEqual((tab.go.text(), tab.go.isEnabled()), ("Stop", True))
        self.assertFalse(tab.progress.isHidden())
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        self.assertTrue(tab.status.text().startswith("Stopped at "), tab.status.text())
        self.assertEqual(tab.go.text(), "Start surface scan")
        self.assertTrue(tests.none_locked(self.win))

    def test_no_means_no_and_an_error_is_said(self):
        tab = self.open("good", yes=False)
        self.pick(tab)
        tab.go.click()
        self.assertEqual((tab.mode, tab.runner.running()), (None, False))
        tab = self.open("refuse")
        self.pick(tab)
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.status.text(), "sdz is not a drive (was it unplugged?).")

    def test_a_windows_drive_is_left_alone(self):
        tab = self.open("good", found=[drive("EXAMPLE WINDOWS DISK", fs="ntfs"), drive("NO NAME", device="")])
        for row in (0, 1):
            self.pick(tab, row)
            self.assertFalse(tab.go.isEnabled())
            tab.start_or_stop()                                         # not even past the button
            self.assertEqual((tab.mode, self.asked), (None, []))
        self.pick(tab, 0)
        self.assertEqual(tab.status.text(), surfacetab.NTFS_WORDS)

    def test_the_map_belongs_to_its_drive(self):
        tab = self.open("bad", found=[drive("A"), drive("B", device="sdy", path="/d/y")])
        self.pick(tab, 0)
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.model.counts()["bad"], 2)
        self.pick(tab, 1)                                               # another drive: A's damage is not B's
        self.assertEqual(len(tab.model.states), 0)
        self.assertIn("B", tab.status.text())
        self.assertNotIn("unreadable", tab.status.text())


if __name__ == "__main__":
    unittest.main()
