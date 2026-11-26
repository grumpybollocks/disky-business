# SPDX-License-Identifier: GPL-3.0-or-later
"""The Defrag tab with a pretend engine, drawn off-screen."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import mapmodel as mm
    from diskygui.defragtab import admin_wrap
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
class DefragTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, fs):
        self.win = MainWindow(theme=THEMES["w98"], drives=lambda: [],
                              runners={"Defrag": EngineRunner([sys.executable, FAKE, scenario])})
        self.win.resize(1180, 760)
        self.win.show()
        self.addCleanup(self.win.close)
        tab = next(p for p in self.win.pages if p.title == "Defrag")
        self.assertEqual(tab.title, "Defrag")
        tab.fs_type = lambda path: fs
        tab.device_of = lambda path: "/dev/sdz9"
        self.win.tabs.setCurrentIndex(2)
        tab.set_folder("/a/folder")
        return tab

    def wait(self, done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done():
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def scan(self, tab):
        tab.scan_button.click()
        self.wait(lambda: tab.mode is None)

    def test_linux_file_system_scan_then_defrag_one_file(self):
        tab = self.open("good", "btrfs")
        self.assertEqual(tab.scan_command(), ("fragfiles", "/a/folder", ()))        # no password asked
        self.assertFalse(tab.defrag_button.isEnabled())
        self.scan(tab)
        self.assertEqual(tab.status.text(), "2 of 10 files are in more than one piece (20.0 %), holding 8.0 MiB "
                                            "of 20.0 MiB. 1 could not be read.")
        self.assertEqual([tab.files.item(i).text() for i in range(2)],
                         ["64 pieces    8.0 MiB    /drive/big.bin", "3 pieces    4.0 KiB    /drive/small.txt"])
        self.assertFalse(tab.defrag_button.isEnabled())                             # nothing selected yet
        tab.files.setCurrentRow(0)
        self.assertTrue(tab.defrag_button.isEnabled())
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.defrag_button.click()
        self.assertEqual((len(asked), tab.mode), (1, None))                         # "no" runs nothing
        tab.confirm = lambda text: True
        tab.defrag_button.click()
        self.wait(lambda: tab.mode is None)
        self.assertEqual(tab.runner.last_argv[-4:], ["defrag", "/drive/big.bin", "--json", "--stdin-stop"])
        self.assertEqual(tab.status.text(), "64 pieces before, 1 after. Content checked: unchanged.")
        self.assertEqual(tab.files.item(0).text(), "1 pieces    8.0 MiB    /drive/big.bin")

    def test_exfat_scan_asks_for_admin_draws_the_map_and_offers_no_defrag(self):
        tab = self.open("good", "exfat")
        self.assertIn("admin password", tab.status.text())
        mode, target, wrap = tab.scan_command()
        self.assertEqual((mode, target, list(wrap)), ("fragscan", "/dev/sdz9", admin_wrap()))
        self.assertEqual(wrap[0], "pkexec")
        tab.device_of = lambda path: "/a/folder"        # the pretend engine needs no password
        tab.scan_command = lambda: ("fragscan", "/a/folder", ())
        self.scan(tab)
        self.assertIn("2 of 10 files", tab.status.text())
        self.assertIn("No defragmenter is offered for exFAT", tab.status.text())
        tab.files.setCurrentRow(0)
        self.assertFalse(tab.defrag_button.isEnabled())
        tab.defrag_selected()
        self.assertIsNone(tab.mode)
        self.assertEqual(len(tab.model.states), 1000)
        self.assertEqual(tab.model.counts()["bad"], 1)                               # the fragmented cluster
        tab.map.refresh()
        bad = [i for i, s in enumerate(tab.map._shown.tolist()) if s == mm.BAD]
        self.assertEqual({tab.map.blocks_of(i)[0] for i in bad}, {310})             # and it shows on the map

    def test_a_small_share_is_not_shown_as_nothing(self):
        from diskygui.defragtab import share_text
        self.assertEqual([share_text(9, 20941), share_text(0, 10), share_text(2, 10), share_text(1, 1000)],
                         ["under 0.1 %", "0 %", "20.0 %", "0.1 %"])

    def test_other_file_systems_offer_nothing(self):
        tab = self.open("good", "vfat")
        self.assertFalse(tab.scan_button.isEnabled())
        self.assertIsNone(tab.scan_command())
        self.assertIn("No fragmentation information for FAT", tab.status.text())

    def test_no_password_and_errors_are_said_plainly(self):
        tab = self.open("nopassword", "btrfs")
        self.scan(tab)
        self.assertEqual(tab.status.text(), "Not scanned: the admin password was not given.")
        tab = self.open("refuse", "btrfs")
        self.scan(tab)
        self.assertEqual(tab.status.text(), "This is not an exFAT volume.")

    def test_windows_drives_get_the_reason_not_a_scan(self):
        from diskygui.defragtab import NTFS_WHY
        tab = self.open("good", "ntfs3")
        self.assertEqual((tab.status.text(), tab.scan_button.isEnabled()), (NTFS_WHY, False))
        tab.set_folder("")
        self.assertEqual(tab.status.text(), "Pick a drive, then press Scan.")

    def test_theme_reaches_the_defrag_tab(self):
        tab = self.open("good", "btrfs")
        self.win.theme_button.click()
        self.assertIs(tab.map.theme, self.win.theme)
        self.assertIn(self.win.theme.frag["fragmented"], tab.swatches[3][0].styleSheet())

    def test_the_defrag_map_cannot_be_mistaken_for_the_test_map(self):
        tab = self.open("good", "btrfs")
        test_map = self.win.pages[0].map
        for _ in THEMES:
            t = self.win.theme
            self.assertNotEqual(tab.map.style.name, test_map.style.name)            # another shape
            self.assertNotEqual(tab.map.style.size, test_map.style.size)
            mine = {tab.map.colours[k] for k in ("written", "good", "bad")}
            theirs = {test_map.colours[k] for k in ("written", "good", "changed", "overwritten")}
            self.assertEqual(mine & theirs, set(), t.name)                           # other colours
            self.assertEqual(tab.map.colours["good"], t.frag["whole"])
            self.win.theme_button.click()


if __name__ == "__main__":
    unittest.main()
