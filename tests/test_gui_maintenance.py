# SPDX-License-Identifier: GPL-3.0-or-later
"""The Maintenance tab: which partitions can be checked or repaired, what is said before a repair, and the runs."""
import tests
import os
import sys
import time
import unittest

from disky import fscare
from diskygui.drives import Drive, Health, Volume

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from diskygui import maintenancetab
    from diskygui.runner import EngineRunner
    from diskygui.theme import THEMES
    from diskygui.window import MainWindow
    HAVE_QT = True
except ImportError as missing:
    if "PySide6" not in str(missing):
        raise
    HAVE_QT = False

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_engine.py")
HOME = "/home/someone"


def usb(*volumes, health=None):
    return Drive("EXAMPLE USB", 1 << 36, "HDD", True, health or Health(), list(volumes), path="/p/usb")


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Words(unittest.TestCase):
    def test_which_partitions_can_be_worked_on(self):
        a = maintenancetab.advice
        self.assertEqual(a(Volume("sdz1", 1, "exfat", []), HOME), (True, maintenancetab.READY))
        self.assertEqual(a(Volume("sdz1", 1, "ntfs", []), HOME), (False, fscare.NTFS_WHY))
        self.assertEqual(a(Volume("sdz1", 1, "exfat", ["/run/media/x/D"]), HOME),
                         (False, maintenancetab.MOUNTED.format(where="/run/media/x/D")))
        self.assertEqual(a(Volume("nvme0n1p2", 1, "btrfs", ["/"]), HOME), (False, maintenancetab.SYSTEM))
        self.assertEqual(a(Volume("sdz1", 1, "ext4", ["/home/someone"]), HOME), (False, maintenancetab.SYSTEM))

    def test_the_repair_question_holds_the_warnings_that_apply(self):
        v = Volume("sdz1", 1, "exfat", [])
        q = maintenancetab.repair_question
        for text in (q(v, None, False), q(v, True, False), q(v, False, True)):
            self.assertIn("Repair sdz1 (exFAT, 1 bytes)?", text)
            self.assertIn("copy them off first", text)
            self.assertIn("cannot be stopped", text)
        self.assertIn("has not been checked yet", q(v, None, False))
        self.assertIn("found no problems, so a repair is probably not needed", q(v, True, False))
        self.assertIn("The last check found problems.", q(v, False, False))
        self.assertIn("Repair sdz1 (exFAT, 1 bytes) on EXAMPLE USB?", q(v, None, False, "EXAMPLE USB"))   # which drive
        self.assertNotIn("FAILING", q(v, False, False))
        self.assertIn("FAILING", q(v, False, True))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Tab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open(self, scenario, drives):
        self.win = MainWindow(theme=THEMES["w98"], drives=lambda: drives or [],
                              runners={"Maintenance": EngineRunner([sys.executable, FAKE, scenario])})
        self.addCleanup(self.win.close)
        tab = next(p for p in self.win.pages if p.title == "Maintenance")
        self.win.tabs.setCurrentWidget(tab)
        tab.info_of = lambda folder: None
        return tab

    def wait(self, tab):
        tab.panel.settle()              # the drive is read again first, off the window's thread; then the job starts
        end = time.monotonic() + 5
        while tab.mode is not None:
            self.assertLess(time.monotonic(), end, "timed out")
            self.app.processEvents()
            time.sleep(0.005)

    def buttons(self, tab):
        return tab.check_button.isEnabled(), tab.repair_button.isEnabled()

    def test_the_drive_is_read_again_off_the_window_thread_before_a_check(self):
        tab = self.open("good", [usb(Volume("sdz1", 1 << 30, "vfat", []))])
        tab.panel.settle()
        tab.panel.drive_list.setCurrentRow(0)
        tab.panel.settle()
        listed = tab.panel.read_drives
        tab.panel.read_drives = lambda: (time.sleep(0.6), listed())[1]
        start = time.monotonic()
        tab.check_button.click()
        self.assertLess(time.monotonic() - start, 0.3)          # a stalled drive service cannot freeze the window
        self.assertEqual(self.buttons(tab), (False, False))     # and no second click meanwhile
        self.assertTrue(tab.panel.settle())
        self.wait(tab)
        self.assertEqual(tab.runner.last_argv[-6:], ["fscheck", "sdz1", "--json", "--stdin-stop", "--expect", "vfat:1073741824"])

    def test_check_then_repair_with_the_question_first(self):
        tab = self.open("bad", [usb(Volume("sdz1", 1 << 30, "vfat", []))])
        self.assertEqual((self.buttons(tab), tab.info.text()), ((False, False),
                                                                "Pick a drive on the left to see its partitions."))
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.parts.count(), 1)
        self.assertEqual(tab.parts.item(0).text().split()[:2], ["sdz1", "FAT"])           # an everyday name
        self.assertEqual((tab.picked().device, self.buttons(tab), tab.info.text()),
                         ("sdz1", (True, True), maintenancetab.READY))            # one partition: nothing to choose
        tab.check_button.click()
        tab.panel.settle()
        self.assertEqual(self.buttons(tab), (False, False))                 # one job at a time
        self.assertTrue(tests.others_locked(self.win, tab))
        self.wait(tab)
        self.assertEqual(tab.runner.last_argv[-6:], ["fscheck", "sdz1", "--json", "--stdin-stop", "--expect", "vfat:1073741824"])
        self.assertEqual((tab.maint_status.text(), tab.checked), ("sdz1: Problems found.", {("/p/usb", "sdz1"): False}))
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.repair_button.click()
        tab.panel.settle()
        self.assertEqual((len(asked), tab.mode), (1, None))                 # said no: nothing runs
        self.assertIn("The last check found problems.", asked[0])
        self.assertEqual(tab.runner.last_argv[-6], "fscheck")
        tab.confirm = lambda text: True
        tab.repair_button.click()
        tab.panel.settle()
        self.wait(tab)
        self.assertEqual(tab.runner.last_argv[-7:], ["fsrepair", "sdz1", "--json", "--stdin-stop", "--yes", "--expect", "vfat:1073741824"])
        self.assertIn("on EXAMPLE USB?", asked[0])
        self.assertEqual(tab.maint_status.text(), "sdz1: Repaired.")

    def test_nothing_is_offered_where_it_must_not_run(self):
        tab = self.open("good", [usb(Volume("sdz1", 1, "ntfs", []), Volume("sdz2", 1, "exfat", ["/run/media/x/D"]),
                                     Volume("sdz3", 1, "ext4", []))])
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual((tab.picked(), self.buttons(tab), tab.info.text()), (None, (False, False), "Pick a partition."))
        for row, text, ready in ((0, fscare.NTFS_WHY, False), (1, maintenancetab.MOUNTED.format(where="/run/media/x/D"), False),
                                 (2, maintenancetab.READY, True)):
            tab.parts.setCurrentRow(row)
            self.assertEqual((tab.info.text(), self.buttons(tab)), (text, (ready, ready)))
        started = []
        tab.runner.start = lambda *a, **k: started.append(a)
        tab.parts.setCurrentRow(0)
        tab.confirm = lambda text: True
        tab.check()
        tab.panel.settle()
        tab.repair()
        tab.panel.settle()
        self.assertEqual(started, [])                                       # also not when called directly

    def test_nothing_is_done_on_the_strength_of_an_old_list(self):
        first = usb(Volume("sdz1", 1 << 30, "vfat", []))
        second = Drive("ANOTHER DRIVE", 1 << 36, "HDD", True, Health(), [Volume("sdz1", 1 << 30, "vfat", [])],
                       path="/p/other")
        now = [first]
        tab = self.open("good", None)
        tab.panel.read_drives = lambda: list(now)
        tab.panel.load_drives()
        tab.panel.settle()
        tab.panel.drive_list.setCurrentRow(0)
        tab.check_button.click()
        tab.panel.settle()
        self.wait(tab)
        self.assertEqual(tab.maint_status.text(), "sdz1: No problems found.")
        started = []
        tab.runner.start = lambda *a, **k: started.append(a)
        tab.confirm = lambda text: True
        # Refresh with nothing changed: the pick is gone, so are the partitions and the buttons.
        tab.panel.refresh.click()
        tab.panel.settle()
        self.assertEqual((tab.parts.count(), self.buttons(tab), tab.maint_status.text()), (0, (False, False), ""))
        # The drive is swapped for another that got the same name sdz, and the list is not read again.
        tab.panel.drive_list.setCurrentRow(0)
        now[:] = [second]
        tab.repair_button.click()
        tab.panel.settle()
        tab.check_button.click()
        tab.panel.settle()
        self.assertEqual(started, [])
        self.assertIn("The drives changed", tab.maint_status.text())
        # Picking the new drive: nothing of the first one is remembered.
        tab.panel.drive_list.setCurrentRow(0)
        self.assertEqual(tab.panel.current_drive().model, "ANOTHER DRIVE")
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.repair_button.click()
        tab.panel.settle()
        self.assertIn("has not been checked yet", asked[0])
        self.assertIn("on ANOTHER DRIVE?", asked[0])

    def test_a_refusal_and_a_failing_drive_are_said_plainly(self):
        tab = self.open("refuse", [usb(Volume("sdz1", 1, "vfat", []), health=Health("failing", "Health: FAILING"))])
        tab.panel.drive_list.setCurrentRow(0)
        tab.check_button.click()
        tab.panel.settle()
        self.wait(tab)
        self.assertEqual(tab.maint_status.text(), "It is mounted. Unmount it first.")
        asked = []
        tab.confirm = lambda text: asked.append(text) or False
        tab.repair_button.click()
        tab.panel.settle()
        self.assertIn("FAILING", asked[0])
        self.assertIn("has not been checked yet", asked[0])                 # a refused check is no check


if __name__ == "__main__":
    unittest.main()
