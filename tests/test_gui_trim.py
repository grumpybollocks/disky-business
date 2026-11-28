# SPDX-License-Identifier: GPL-3.0-or-later
"""The TRIM tab with made-up drive facts and the pretend engine."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui.defragtab import admin_wrap
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
YES = {"mount": "/drive", "device": "/dev/fake1", "supported": True, "discard_max_bytes": 1 << 31,
       "discard_granularity": 4096, "rotational": False}
NO = dict(YES, supported=False, discard_max_bytes=0, rotational=True)
WEEKLY_ON = {"on": True, "last": "23 November 2026", "next": "30 November 2026"}
WEEKLY_OFF = {"on": False, "last": "", "next": ""}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class TrimTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, facts, weekly=None, found=()):
        self.win = MainWindow(theme=THEMES["w98"], drives=lambda: list(found),
                              runners={"Maintenance": EngineRunner([sys.executable, FAKE, scenario])})
        self.addCleanup(self.win.close)
        self.win.show()
        tab = next(p for p in self.win.pages if p.title == "Maintenance")
        self.assertEqual(tab.title, "Maintenance")
        tab.info_of = lambda folder: facts
        tab.weekly_of = lambda: weekly or WEEKLY_OFF
        self.assertEqual(tab.wrap(), admin_wrap())     # the real tab asks through the system prompt
        tab.wrap = lambda: ["env", "PRETEND_PROMPT=1"]   # tests never call the real prompt
        tab.confirm = lambda text: True
        self.win.tabs.setCurrentWidget(tab)
        if found:
            tab.panel.drive_list.setCurrentRow(0)
        else:
            tab.set_folder("/drive/folder")
        self.assertTrue(tab.panel.settle())
        return tab

    def run_it(self, tab):
        tab.go.click()
        end = time.monotonic() + 5
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end)
            self.app.processEvents()
            time.sleep(0.005)

    def test_supported_drive_is_trimmed_through_the_password_prompt(self):
        tab = self.open("good", YES)
        self.assertEqual(tab.answer.text(), "Worth doing: nothing trims this drive by itself.")
        self.assertEqual((tab.go.isEnabled(), tab.go.text()), (True, "Trim now"))
        self.run_it(tab)
        self.assertEqual(tab.runner.last_argv[:2], ["env", "PRETEND_PROMPT=1"])     # the prompt goes first
        self.assertEqual(tab.runner.last_argv[-4:], ["trim", "/drive/folder", "--json", "--stdin-stop"])
        self.assertEqual(tab.status.text(), "Done: 5.0 GiB of free space trimmed.")

    def test_unsupported_drive_offers_nothing(self):
        tab = self.open("good", NO)
        self.assertEqual(tab.answer.text(), "Not needed: spinning disks have no use for TRIM.")
        self.assertFalse(tab.go.isEnabled())
        tab.start()
        self.assertIsNone(tab.mode)

    def test_asks_first_and_says_why_it_did_not_run(self):
        tab = self.open("good", YES)
        tab.confirm = lambda text: False
        tab.go.click()
        self.assertIsNone(tab.mode)
        tab = self.open("nopassword", YES)
        self.run_it(tab)
        self.assertEqual(tab.status.text(), "Not trimmed: the admin password was not given.")
        tab = self.open("refuse", YES)
        self.run_it(tab)
        self.assertEqual(tab.status.text(), "This drive does not accept TRIM.")


    def test_the_answer_says_whether_trim_is_needed_and_why(self):
        from diskygui import drives as dr
        tab = self.open("good", YES, WEEKLY_ON)
        self.assertEqual(tab.answer.text(), "Not needed: Linux trims this drive once a week.")
        self.assertEqual(tab.detail.text().split("\n"), [
            "Accepts TRIM: yes.",
            "Weekly TRIM by Linux: on. Last ran 23 November 2026, next 30 November 2026.",
            "Continuous TRIM (the discard mount option): off.",
            "Trim now covers: /drive/folder. It tells the drive which space is free right away; files are not "
            "touched. It needs admin rights."])
        tab = self.open("good", dict(YES, auto_discard=True), WEEKLY_OFF)
        self.assertEqual(tab.answer.text(), "Not needed: the drive is told about freed space straight away.")
        self.assertIn("Continuous TRIM (the discard mount option): on.", tab.detail.text())
        self.assertIn("Weekly TRIM by Linux: off.", tab.detail.text())
        tab = self.open("good", YES, {"on": None, "last": "", "next": ""})
        self.assertIn("Weekly TRIM by Linux: could not be read.", tab.detail.text())
        usb = dr.Drive("EXAMPLE USB SSD", 1 << 40, "SSD", True, dr.Health(),
                       [dr.Volume("sdz1", 1 << 30, "ext4", ["/drive"])], path="/p/usb")
        tab = self.open("good", YES, WEEKLY_ON, found=[usb])
        self.assertEqual(tab.answer.text(), "Optional: Linux trims it once a week, but only if it is plugged in then.")
        usb_ssd = dr.Drive("EXAMPLE USB SSD", 1 << 40, "SSD", True, dr.Health(),
                           [dr.Volume("sdz1", 1 << 30, "vfat", ["/drive"])], path="/p/usb")
        tab = self.open("good", NO, found=[usb_ssd])            # USB cases say "spinning" for any drive
        self.assertEqual(tab.answer.text(), "This drive does not accept TRIM.")
        self.assertIn("USB case", tab.detail.text())
        hdd = dr.Drive("EXAMPLE HDD", 1 << 40, "HDD", False, dr.Health(), [dr.Volume("sdz1", 1 << 30, "ext4", [])],
                       path="/p/hdd")
        tab = self.open("good", NO, found=[hdd])                # not mounted, but it never needs TRIM anyway
        self.assertEqual(tab.answer.text(), "Not needed: spinning disks have no use for TRIM.")
        stick = dict(NO, rotational=False)
        tab = self.open("good", stick)
        self.assertEqual(tab.answer.text(), "This drive does not accept TRIM.")
        self.assertIn("USB case", tab.detail.text())
        self.assertFalse(tab.go.isEnabled())

    def test_a_picked_drive_without_a_folder_says_why(self):
        from diskygui import drives as dr
        win = MainWindow(theme=THEMES["w98"], drives=lambda: [dr.Drive("EXAMPLE SSD", 1 << 40, "SSD", False,
                                                                       dr.Health())])
        self.addCleanup(win.close)
        tab = next(p for p in win.pages if p.title == "Maintenance")
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.answer.text(), "Mount this drive first.")
        self.assertIn("Press Mount", tab.detail.text())
        self.assertFalse(tab.go.isEnabled())

    def test_a_picked_drive_is_trimmed_without_any_folder(self):
        from diskygui import drives as dr
        ssd = dr.Drive("EXAMPLE SSD", 1 << 40, "SSD", False, dr.Health(),
                       [dr.Volume("sdz1", 1 << 30, "ext4", ["/data"]), dr.Volume("sdz2", 1 << 30, "ntfs", ["/win"]),
                        dr.Volume("sdz3", 1 << 30, "btrfs", ["/games/library/shared/deep", "/more"]), dr.Volume("sdz4", 1, "ext4", [])],
                       path="/p/ssd")
        self.win = MainWindow(theme=THEMES["w98"], drives=lambda: [ssd],
                              runners={"Maintenance": EngineRunner([sys.executable, FAKE, "good"])})
        self.addCleanup(self.win.close)
        tab = next(p for p in self.win.pages if p.title == "Maintenance")
        asked_about, started = [], []
        tab.info_of = lambda place: asked_about.append(place) or dict(YES, mount=place)
        tab.wrap = lambda: ["env", "PRETEND_PROMPT=1"]
        tab.panel.drive_list.setCurrentRow(0)
        tab.panel.settle()
        self.assertEqual(tab.targets, ["/data", "/more"])               # every mounted file system, never NTFS
        self.assertNotIn("/win", asked_about)
        self.assertEqual((tab.go.isEnabled(), tab.go.text()), (True, "Trim now"))
        self.assertIn("Trim now covers: /data, /more", tab.detail.text())
        questions = []
        tab.confirm = lambda text: questions.append(text) or True
        real_start = tab.runner.start
        tab.runner.start = lambda mode, target, *a, **k: started.append((mode, target)) or real_start(mode, target, *a, **k)
        self.run_it(tab)
        self.assertEqual(started, [("trim", "/data"), ("trim", "/more")])   # one after the other
        self.assertIn("/data, /more", questions[0])
        self.assertEqual(len(questions), 1)                             # asked once for the whole drive
        self.assertIn("Done:", tab.status.text())

if __name__ == "__main__":
    unittest.main()
