# SPDX-License-Identifier: GPL-3.0-or-later
"""The window with a pretend engine, drawn off-screen. Skipped where PySide6 is not installed."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import mapmodel as mm
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise                   # our own code failing to import must fail loudly, never skip
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    drives = ()

    def open(self, scenario):
        self.win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, FAKE, scenario]), drives=lambda: list(self.drives))
        self.tab = self.win.pages[0]
        self.tab.set_folder("/a/folder")
        self.win.show()
        self.addCleanup(self.win.close)
        return self.tab

    def wait(self, done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def run_to_end(self, scenario):
        tab = self.open(scenario)
        tab.go.click()
        self.wait(lambda: tab.mode is None)
        return tab

    def shown(self, tab):
        self.assertIs(tab.stack.currentWidget(), tab.verdict)
        return tab.verdict_title.text(), tab.verdict_detail.text()

    def test_good(self):
        tab = self.run_to_end("good")
        title, detail = self.shown(tab)
        self.assertEqual(title, "Drive is good.")
        self.assertIn("200.0 MiB", detail)
        self.assertEqual(tab.model.counts()["good"], 200)
        self.assertEqual((tab.go.text(), tab.go.isEnabled(), tab.problems_seen), ("Start test", True, []))

    def test_bad_names_the_block_and_the_map_shows_it(self):
        tab = self.run_to_end("bad")
        self.assertEqual(self.shown(tab)[0], "Drive is bad: 1 block, first at 57.0 MiB.")
        tab.map.refresh()
        bad = [i for i, s in enumerate(tab.map._shown.tolist()) if s == mm.BAD]
        self.assertEqual({tab.map.blocks_of(i)[0] for i in bad}, {57})          # only block 57, and it is shown

    def test_not_verified_is_not_called_good(self):
        title, detail = self.shown(self.run_to_end("memory"))
        self.assertEqual(title, "Not verified.")
        self.assertIn("proves nothing", detail)

    def test_stop_ends_the_engine_and_the_window_keeps_answering(self):
        tab = self.open("slow")
        tab.go.click()
        self.wait(lambda: "Writing" in tab.status.text() and "MiB/s" in tab.status.text())
        self.assertEqual(tab.go.text(), "Stop test")
        self.assertFalse(tab.remove.isEnabled())
        tab.go.click()
        self.wait(lambda: tab.mode is None, 3.0)
        self.assertFalse(tab.runner.running())
        self.assertEqual(self.shown(tab)[0], "Stopped.")

    def test_crash_is_shown_as_an_error(self):
        tab = self.run_to_end("crash")
        title, detail = self.shown(tab)
        self.assertEqual(title, "The test did not finish.")
        self.assertIn("unexpectedly", detail)
        self.assertTrue(tab.go.isEnabled())

    def test_engine_error_message_is_shown(self):
        title, detail = self.shown(self.run_to_end("refuse"))
        self.assertEqual(title, "Could not run.")
        self.assertIn("Check test files again", detail)
        self.assertNotIn("disky ", detail)             # no terminal commands in the window

    def test_check_existing_files_runs_a_verify(self):
        tab = self.open("bad")
        tab.check.click()
        self.assertEqual(tab.go.text(), "Stop test")
        self.assertFalse(tab.check.isEnabled())
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.runner.last_argv[-4:], ["verify", "/a/folder", "--json", "--stdin-stop"])
        self.assertEqual(self.shown(tab)[0], "Drive is bad: 1 block, first at 57.0 MiB.")
        self.assertEqual(tab.model.counts()["bad"], 1)
        self.assertTrue(tab.check.isEnabled())

    def test_a_check_does_not_claim_to_have_written(self):
        tab = self.open("good")
        tab.check.click()
        self.wait(lambda: tab.mode is None)
        self.assertEqual(self.shown(tab), ("Drive is good.", "200.0 MiB read back. Every block matched."))

    def test_unknown_events_are_ignored_and_junk_is_noted(self):
        tab = self.run_to_end("junk")
        self.assertEqual(self.shown(tab)[0], "Drive is good.")
        self.assertEqual(len(tab.problems_seen), 1)
        self.assertIn("this is not JSON", tab.problems_seen[0])

    def test_system_drive_warning_is_shown_and_the_run_goes_on(self):
        tab = self.run_to_end("warn")
        self.assertIn("this drive runs your system", self.win.tip_line.text())
        self.assertEqual(self.shown(tab)[0], "Drive is good.")
        self.win.next_tip()                 # the warning stays for one turn
        self.assertIn("this drive runs your system", self.win.tip_line.text())
        self.win.next_tip()
        self.assertNotIn("this drive runs your system", self.win.tip_line.text())

    def test_remove_asks_first(self):
        tab = self.open("good")
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.remove.click()
        self.assertEqual((len(asked), tab.mode), (1, None))
        tab.confirm = lambda text: True
        tab.remove.click()
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.status.text(), "Test files deleted.")

    def test_remove_says_when_a_look_alike_file_was_kept(self):
        tab = self.open("kept")
        tab.confirm = lambda text: True
        tab.remove.click()
        self.wait(lambda: tab.mode is None)
        self.assertIn("1 file with a test file's name kept", tab.status.text())

    def test_theme_button_changes_everything_at_once(self):
        tab = self.run_to_end("bad")
        seen = []
        for _ in THEMES:
            t = self.win.theme
            tab.map.refresh()
            i = tab.map._shown.tolist().index(mm.BAD)
            x, y = tab.map._xy(i)
            c = tab.map.style.size // 2
            seen.append((t.name, tab.map.grab().toImage().pixelColor(x + c, y + c).name()))
            self.assertEqual(seen[-1][1], t.states["bad"])                      # the map
            self.assertIn(t.states["good"], dict((s, w.styleSheet()) for w, s in tab.swatches)["good"])  # legend
            self.assertIn(t.states["bad"], tab.verdict_mark.styleSheet())       # the verdict mark
            self.assertIn(t.face, self.win.styleSheet())                        # buttons, tabs, boxes
            self.assertEqual(self.win.theme_button.text(), "Look: " + t.label)
            self.win.theme_button.click()
        self.assertEqual([n for n, _ in seen], list(THEMES))
        self.assertEqual(self.win.theme.name, list(THEMES)[0])                  # and round again
        self.assertEqual(self.shown(tab)[0], "Drive is bad: 1 block, first at 57.0 MiB.")   # nothing lost

    def test_every_theme_is_complete_and_its_states_can_be_told_apart(self):
        from PySide6.QtGui import QColor
        for t in THEMES.values():
            self.assertEqual(sorted(t.states), sorted(mm.NAMES), t.name)
            self.assertEqual(sorted(t.frag), ["fragmented", "free", "other", "whole"], t.name)
            fr = [QColor(c) for c in list(t.frag.values()) + [t.map_bg]]
            for a in range(len(fr) - 1):
                for b in range(a + 1, len(fr)):
                    if a == 0 and b == len(fr) - 1:
                        continue                # free space may sit close to the background
                    d = sum(abs(p - q) for p, q in zip(fr[a].getRgb()[:3], fr[b].getRgb()[:3]))
                    self.assertGreater(d, 60, (t.name, fr[a].name(), fr[b].name()))
            cols = [QColor(c) for c in list(t.states.values()) + [t.map_bg]]
            for a in range(len(cols)):
                for b in range(a + 1, len(cols)):
                    d = sum(abs(p - q) for p, q in zip(cols[a].getRgb()[:3], cols[b].getRgb()[:3]))
                    self.assertGreater(d, 60, (t.name, cols[a].name(), cols[b].name()))

    def test_empty_map_shows_the_grid(self):
        tab = self.open("good")
        tab.map.refresh()
        self.assertTrue(tab.map.placeholder)
        self.assertEqual(len(tab.map._shown), tab.map.cols * tab.map.rows)
        self.assertIsNone(tab.map.square_at(10, 10))

    def test_closing_the_window_mid_run_ends_the_engine(self):
        tab = self.open("slow")
        tab.go.click()
        self.wait(lambda: "MiB/s" in tab.status.text())
        proc = tab.runner._proc
        self.assertIsNotNone(proc)
        pid = proc.processId()
        self.win.close()
        end = time.monotonic() + 5
        while os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split()[2] != "Z":
            self.assertLess(time.monotonic(), end, "the engine was left running")
            self.app.processEvents()
            time.sleep(0.01)

    def test_no_folder_no_start(self):
        self.win = MainWindow(theme=THEMES["w98"], runner=EngineRunner([sys.executable, FAKE, "good"]), drives=lambda: [])
        self.addCleanup(self.win.close)
        tab = self.win.pages[0]
        self.assertFalse(tab.go.isEnabled())
        tab.start_or_stop()
        self.assertIsNone(tab.mode)


    def test_a_tip_stays_long_enough_to_read(self):
        from diskygui import window
        tab = self.open("good")
        self.assertGreaterEqual(window.TIP_SECONDS, 25)
        self.assertEqual(tab.window()._timer.interval(), window.TIP_SECONDS * 1000)

    def test_the_app_has_an_icon_and_names_its_menu_entry(self):
        from PySide6.QtGui import QIcon
        from diskygui import __main__ as start
        self.assertTrue(os.path.isfile(start.ICON))
        self.assertFalse(QIcon(start.ICON).pixmap(64, 64).isNull())           # it really draws
        with open(os.path.join(os.path.dirname(start.ICON), "..", "packaging", "disky-business.desktop")) as f:
            entry = f.read()
        self.assertIn("Icon=disky-business", entry)
        import inspect
        source = inspect.getsource(start.main)
        self.assertIn('setDesktopFileName("disky-business")', source)        # the menu entry's file name
        self.assertIn("setWindowIcon", source)

    def test_the_tab_says_what_start_will_do_before_it_is_pressed(self):
        import tempfile
        from diskygui.window import test_plan

        class Space:
            f_bavail, f_frsize, f_blocks = 100_000, 1 << 20, 400_000      # 100 000 MiB free of 400 000 MiB
        plain = test_plan("/x", statvfs=lambda p: Space, files=lambda p: [], system=lambda p: False)
        self.assertIn("fills the free space here (105 GB)", plain)
        self.assertIn("Your own files are not touched", plain)
        self.assertNotIn("left free", plain)
        system = test_plan("/x", statvfs=lambda p: Space, files=lambda p: [], system=lambda p: True)
        self.assertIn("(84 GB)", system)                                # 5 % of the drive is kept
        self.assertIn("Your system runs from this drive, so 21 GB is left free", system)
        old = test_plan("/x", statvfs=lambda p: Space, files=lambda p: [(0, "a"), (1, "b")], system=lambda p: False)
        self.assertIn("already holds 2 Disky test files", old)
        self.assertNotIn("fills the free space", old)

        class Full:
            f_bavail, f_frsize, f_blocks = 0, 1 << 20, 400_000
        self.assertEqual(test_plan("/x", statvfs=lambda p: Full, files=lambda p: [], system=lambda p: False),
                         "There is no free space here to test.")
        tab = self.open("good")
        with tempfile.TemporaryDirectory() as folder:
            tab.set_folder(folder)
            self.assertIs(tab.stack.currentWidget(), tab.plan)          # shown as soon as a drive is picked
            self.assertIn("\"Start test\" fills the free space here", tab.plan.text())
            open(os.path.join(folder, "1.h2w"), "w").close()
            tab.set_folder(folder)
            self.assertIn("already holds 1 Disky test file from", tab.plan.text())
        self.assertEqual((tab.go.text(), tab.check.text(), tab.remove.text()),
                         ("Start test", "Check test files again", "Delete test files"))

if __name__ == "__main__":
    unittest.main()
