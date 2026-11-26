# SPDX-License-Identifier: GPL-3.0-or-later
"""The Speed tab and the tab switching, with a pretend engine, drawn off-screen."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import drives as dr
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    from tests.test_gui_drives import ANSWER
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise                   # our own code failing to import must fail loudly, never skip
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class SpeedTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, found=()):
        fake = lambda: EngineRunner([sys.executable, FAKE, scenario])      # noqa: E731
        self.win = MainWindow(theme=THEMES["w98"], runners={"Test": fake(), "Speed": fake()},
                              drives=lambda: list(found))
        self.win.resize(1180, 760)
        self.win.show()
        self.addCleanup(self.win.close)
        self.test, self.speed = self.win.pages[:2]
        return self.speed

    def wait(self, done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def run_to_end(self, scenario):
        tab = self.open(scenario)
        self.win.tabs.setCurrentIndex(1)
        tab.set_folder("/a/folder")
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        return tab

    def test_results_and_graph(self):
        tab = self.run_to_end("good")
        self.assertEqual({k: v.text() for k, v in tab.values.items()}, {
            "seq_write": "38.75 MiB/s", "seq_read": "102.5 MiB/s",
            "rand_read": "4.49 MiB/s   (1 150 per second)",
            "rand_write": "0.41 MiB/s   (106 per second, through the system's cache)"})
        self.assertEqual(tab.status.text(), "Done. The temporary file was removed.")
        self.assertEqual(tab.problems_seen, [])
        g = tab.graph
        self.assertEqual((len(g.samples["seq_write"]), len(g.samples["seq_read"]), g.total), (4, 4, 128 << 20))
        self.app.processEvents()
        w, r = g.points("seq_write"), g.points("seq_read")
        self.assertTrue(all(a.x() < b.x() for a, b in zip(w, w[1:])))           # left to right along the file
        self.assertTrue(all(0 <= p.x() <= g.width() and 0 <= p.y() <= g.height() for p in w + r))
        self.assertTrue(all(fast.y() < slow.y() for fast, slow in zip(r, w)))  # faster is higher up
        self.assertLess(w[1].y(), w[3].y())                                     # 50 MiB/s above 20 MiB/s
        self.assertEqual(g.top(), 120)

    def test_read_from_memory_is_said(self):
        tab = self.run_to_end("memory")
        self.assertIn("not the drive's real speed", tab.values["seq_read"].text())

    def test_stop(self):
        tab = self.open("slow")
        tab.set_folder("/a/folder")
        tab.go.click()
        self.wait(lambda: len(tab.graph.samples["seq_write"]) > 2)
        self.assertEqual(tab.go.text(), "Stop")
        self.assertFalse(self.win.tabs.tabBar().isEnabled())       # no second job on another tab meanwhile
        tab.go.click()
        self.wait(lambda: tab.mode is None, 3.0)
        self.assertFalse(tab.runner.running())
        self.assertEqual(tab.status.text(), "Stopped. The temporary file was removed.")
        self.assertTrue(self.win.tabs.tabBar().isEnabled())

    def test_engine_error_and_crash_are_shown(self):
        self.assertEqual(self.run_to_end("refuse").status.text(), "A speed-test file from an earlier run is still there.")
        tab = self.run_to_end("crash")
        self.assertIn("did not finish", tab.status.text())
        self.assertEqual(tab.values["seq_write"].text(), "38.75 MiB/s")     # what was measured stays shown
        self.assertEqual(tab.values["rand_read"].text(), "-")

    def test_a_new_run_starts_clean(self):
        tab = self.run_to_end("good")
        tab.go.click()
        self.assertEqual((tab.values["seq_write"].text(), tab.graph.samples["seq_read"]), ("-", []))
        self.wait(lambda: tab.mode is None)

    def test_drive_picked_on_one_tab_is_the_drive_on_the_next(self):
        tab = self.open("good", dr.parse(ANSWER))
        self.assertFalse(tab.go.isEnabled())
        self.test.drive_list.setCurrentRow(1)
        self.test.set_folder("/picked/by/hand")
        self.win.tabs.setCurrentIndex(1)
        self.assertEqual((tab.folder, tab.panel.drive_list.currentRow()), ("/picked/by/hand", 1))
        self.assertEqual(tab.panel.health.text(), self.test.health.text())
        self.assertTrue(tab.go.isEnabled())
        tab.set_folder("/other")
        self.win.tabs.setCurrentIndex(0)
        self.assertEqual(self.test.folder, "/other")

    def test_theme_reaches_the_speed_tab(self):
        tab = self.open("good")
        self.win.theme_button.click()
        t = self.win.theme
        self.assertIsNot(t, THEMES["w98"])
        self.assertIs(tab.graph.theme, t)
        self.assertIn(t.states["written"], tab.swatches[0][0].styleSheet())


if __name__ == "__main__":
    unittest.main()
